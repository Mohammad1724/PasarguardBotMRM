"""Authenticated same-origin administration. No anonymous demo API exists here."""

import json
import time
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import String, cast, func, or_, select

from app.db.base import AsyncSessionLocal as Session
from app.db.models.admin_app import AdminChange, AdminGrant
from app.db.models.auto_renew import AutoRenewAttempt
from app.db.models.customer_experience import SupportTicket, TicketMessage
from app.db.models.panels import Panels
from app.db.models.plans import Plan
from app.db.models.services import Service
from app.db.models.transaction import Transaction
from app.db.models.user import User
from app.services.admin_app import auth, configuration as cfg, service
from app.telegram.keyboards.registry import KEYBOARD_BUTTON_TITLES

router = APIRouter(prefix="/admin", include_in_schema=False)
ASSETS = Path(__file__).resolve().parents[1] / "assets" / "admin_app"


async def payload(request):
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 65536:
            auth.fail(413, "حجم درخواست بیش از حد مجاز است.")
    try:
        value = json.loads(body)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except ValueError, UnicodeDecodeError, RecursionError:
        auth.fail(422, "JSON نامعتبر")


@router.get("")
@router.get("/")
async def page():
    return FileResponse(ASSETS / "index.html", headers={"Cache-Control": "no-store"})


@router.get("/assets/{name}")
async def asset(name: str):
    if name not in ("app.js", "style.css", "Vazirmatn.woff2", "OFL.txt"):
        auth.fail(404, "فایل پیدا نشد.")
    return FileResponse(ASSETS / name, headers={"Cache-Control": "no-cache"})


@router.post("/api/auth")
async def login(request: Request):
    data = await payload(request)
    cfg.fields(data, ("init_data",))
    return await auth.exchange(request, data.get("init_data"))


@router.post("/api/logout")
async def logout(request: Request, actor=Depends(auth.authenticate)):
    await (await auth.redis_required()).delete(request.state.admin_session_key)
    return {"logged_out": True}


@router.get("/api/me")
async def me(actor=Depends(auth.authenticate)):
    return {
        "actor": actor,
        "permissions": auth.PERMISSIONS,
        "sections": cfg.SECTION_KEYS,
        "buttons": [
            {"key": k, "title": v, "default": cfg.KEYBOARD_BUTTON_DEFAULTS.get(k, k)}
            for k, v in KEYBOARD_BUTTON_TITLES.items()
        ],
        "home_keys": cfg.HOME_KEYS,
        "texts": cfg.TEXTS,
    }


@router.get("/api/document/{entity}/{target}")
async def get_document(entity: str, target: str, actor=Depends(auth.authenticate)):
    return await service.document(actor, entity, target)


@router.post("/api/changes")
async def create_change(request: Request, actor=Depends(auth.authenticate)):
    return await service.draft(actor, await payload(request))


@router.post("/api/changes/{token}/{action}")
async def change_action(token: str, action: str, request: Request, actor=Depends(auth.authenticate)):
    data = await payload(request)
    if set(data) != {"confirm"} or data["confirm"] is not True:
        auth.fail(422, "تأیید صریح لازم است.")
    if action == "publish":
        return await service.publish(actor, token)
    if action == "discard":
        return await service.discard(actor, token)
    if action == "restore":
        return await service.restore_draft(actor, token)
    return auth.fail(404, "عملیات نامعتبر")


@router.get("/api/dashboard")
async def dashboard(actor=Depends(auth.authenticate)):
    data = {"server_time": int(time.time()), "counts": {}}
    async with Session() as session:
        for permission, model, name in [
            ("users.view", User, "users"),
            ("services.view", Service, "services"),
            ("plans.manage", Plan, "plans"),
            ("tickets.view", SupportTicket, "tickets"),
        ]:
            if permission in actor["permissions"]:
                q = select(func.count()).select_from(model)
                if model == SupportTicket:
                    q = q.where(SupportTicket.status != "closed")
                data["counts"][name] = await session.scalar(q)
        if "finance.view" in actor["permissions"]:
            now = int(time.time())
            data["activity"] = [
                await session.scalar(
                    select(func.count())
                    .select_from(Transaction)
                    .where(
                        Transaction.created_at >= now - (7 - i) * 86400,
                        Transaction.created_at < now - (6 - i) * 86400,
                    )
                )
                for i in range(7)
            ]
            data["counts"]["wallet_liability"] = str(
                await session.scalar(select(func.coalesce(func.sum(User.amount), 0)))
            )
            data["counts"]["pending_renewals"] = await session.scalar(
                select(func.count())
                .select_from(AutoRenewAttempt)
                .where(AutoRenewAttempt.status.in_(("applying", "review")))
            )
    return data


@router.get("/api/list/{entity}")
async def listing(
    entity: str,
    q: str = Query("", max_length=64),
    page: int = Query(1, ge=1, le=10000),
    actor=Depends(auth.authenticate),
):
    mappings = {
        "users": (User, "users.view", ("id", "status", "time_s", "tested"), "id"),
        "services": (
            Service,
            "services.view",
            ("code", "username", "id", "in_panel", "enable", "is_test", "expiration_time"),
            "code",
        ),
        "plans": (Plan, "plans.manage", cfg.PLAN_FIELDS, "id"),
        "tickets": (
            SupportTicket,
            "tickets.view",
            ("id", "user_id", "topic", "status", "assigned_to", "updated_at"),
            "id",
        ),
        "finance": (Transaction, "finance.view", ("id", "user_id", "amount", "method", "status", "created_at"), "id"),
        "renewals": (
            AutoRenewAttempt,
            "finance.view",
            ("token", "user_id", "service_code", "price", "status", "month", "created_at"),
            "created_at",
        ),
        "audit": (
            AdminChange,
            "audit.view",
            ("token", "actor_id", "entity", "target", "reason", "status", "created_at", "published_at"),
            "created_at",
        ),
        "grants": (AdminGrant, None, ("user_id", "name", "permissions", "updated_at"), "user_id"),
        "panels": (Panels, "plans.manage", ("code", "name", "enable"), "code"),
    }
    if entity not in mappings:
        auth.fail(404, "فهرست نامعتبر")
    model, permission, columns, sort = mappings[entity]
    if permission:
        auth.require(actor, permission)
    elif not actor["owner"]:
        auth.fail(403, "فقط مالک")
    async with Session() as session:
        query = select(model)
        if q:
            terms = [cast(getattr(model, columns[0]), String).contains(q, autoescape=True)]
            for key in ("username", "user_id", "name"):
                if key in columns:
                    terms.append(cast(getattr(model, key), String).contains(q, autoescape=True))
            query = query.where(or_(*terms))
        total = await session.scalar(select(func.count()).select_from(query.subquery()))
        rows = (
            await session.scalars(
                query.order_by(getattr(model, sort).desc(), getattr(model, columns[0]).desc())
                .offset((page - 1) * 25)
                .limit(25)
            )
        ).all()
        items = []
        for row in rows:
            item = {k: getattr(row, k) for k in columns}
            if "button_icon" in item:
                item["button_icon"] = str(item["button_icon"]) if item["button_icon"] else None
            if entity == "users" and "finance.view" in actor["permissions"]:
                item["amount"] = str(row.amount or 0)
            if "amount" in item:
                item["amount"] = str(item["amount"] or 0)
            items.append(item)
        return {"items": items, "total": total, "page": page, "page_size": 25}


@router.get("/api/tickets/{ticket_id}")
async def ticket_thread(ticket_id: int, before: int = Query(2**63 - 1, gt=0), actor=Depends(auth.authenticate)):
    auth.require(actor, "tickets.view")
    async with Session() as session:
        ticket = await session.get(SupportTicket, ticket_id)
        if not ticket:
            auth.fail(404, "تیکت پیدا نشد.")
        rows = (
            await session.scalars(
                select(TicketMessage)
                .where(TicketMessage.ticket_id == ticket_id, TicketMessage.id < before)
                .order_by(TicketMessage.id.desc())
                .limit(50)
            )
        ).all()
        return {
            "ticket": {"id": ticket.id, "user_id": ticket.user_id, "status": ticket.status},
            "messages": [
                {
                    "id": r.id,
                    "kind": r.kind,
                    "text": r.text,
                    "has_attachment": bool(r.file_id),
                    "created_at": r.created_at,
                }
                for r in reversed(rows)
            ],
            "next_before": rows[-1].id if len(rows) == 50 else None,
        }


@router.get("/api/audit/{token}")
async def audit_detail(token: str, actor=Depends(auth.authenticate)):
    auth.require(actor, "audit.view")
    async with Session() as session:
        row = await session.get(AdminChange, token)
        if not row:
            auth.fail(404, "تغییر پیدا نشد.")
        # Audit permission alone must not reveal a private ticket reply or finance settings.
        cfg.permission(actor, row.entity, row.target)
        return {
            "actor_id": row.actor_id,
            "created_at": row.created_at,
            "before": row.before,
            "after": row.after,
            "reason": row.reason,
            "token": row.token,
            "entity": row.entity,
            "target": row.target,
            "status": row.status,
        }
