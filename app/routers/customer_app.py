"""Customer-only views. Every service lookup is scoped to the signed session owner."""

import time
from pathlib import Path as FilePath

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select

from app.db.base import AsyncSessionLocal as Session
from app.db.models.services import Service
from app.db.models.user import User
from app.routers.admin_app import payload
from app.services import customer_app as customer
from app.services.admin_app import auth, configuration as cfg

router = APIRouter(prefix=customer.CUSTOMER_PATH, include_in_schema=False)
ASSETS = FilePath(__file__).resolve().parents[1] / "assets" / "customer_app"


@router.get("")
@router.get("/")
async def page():
    return FileResponse(ASSETS / "index.html")


@router.get("/assets/{name}")
async def asset(name: str):
    if name not in ("app.js", "style.css"):
        auth.fail(404, "فایل پیدا نشد.")
    return FileResponse(ASSETS / name)


@router.post("/api/auth")
async def login(request: Request):
    data = await payload(request)
    cfg.fields(data, ("init_data",))
    return await customer.exchange(request, data.get("init_data"))


@router.post("/api/logout")
async def logout(request: Request, actor=Depends(customer.authenticate)):
    await (await auth.redis_required()).delete(request.state.customer_session_key)
    return {"logged_out": True}


@router.get("/api/me")
async def me(actor=Depends(customer.authenticate)):
    async with Session() as session:
        user = await session.get(User, actor["id"])
        if not user or user.status in ("ban", "BlockedBot", "DeleteAccount"):
            auth.fail(403, "حساب شما در دسترس نیست.")
        total = await session.scalar(select(func.count()).select_from(Service).where(Service.id == actor["id"]))
        active = await session.scalar(
            select(func.count())
            .select_from(Service)
            .where(
                Service.id == actor["id"],
                Service.enable.is_(True),
                or_(
                    Service.expiration_time.is_(None),
                    Service.expiration_time == 0,
                    Service.expiration_time > int(time.time()),
                ),
            )
        )
        return {
            "user_id": str(user.id),
            "balance": str(user.amount or 0),
            "currency": "IRT",
            "services_count": total,
            "active_count": active,
            "read_at": int(time.time()),
            "source": "bot_database",
        }


def public_service(row):
    # No panel URL, credentials, subscription tokens or other customers' details.
    return {
        "code": str(row.code),
        "username": row.username,
        "enabled": bool(row.enable),
        "is_test": bool(row.is_test),
        "package_bytes": str(row.package_size) if row.package_size is not None else None,
        "created_at": row.createtime,
        "expires_at": row.expiration_time,
    }


@router.get("/api/services")
async def services(page: int = Query(1, ge=1, le=10000), actor=Depends(customer.authenticate)):
    async with Session() as session:
        query = select(Service).where(Service.id == actor["id"])
        total = await session.scalar(select(func.count()).select_from(query.subquery()))
        rows = (await session.scalars(query.order_by(Service.code.desc()).offset((page - 1) * 25).limit(25))).all()
        return {"items": [public_service(row) for row in rows], "total": total, "page": page, "page_size": 25}


@router.get("/api/services/{code}")
async def service_detail(code: int = Path(ge=1, le=2**63 - 1), actor=Depends(customer.authenticate)):
    async with Session() as session:
        row = await session.scalar(select(Service).where(Service.code == code, Service.id == actor["id"]))
        if not row:
            auth.fail(404, "سرویس پیدا نشد یا متعلق به شما نیست.")
        return public_service(row)
