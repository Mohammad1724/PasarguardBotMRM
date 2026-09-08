"""Two-phase administration with optimistic conflict detection and atomic audit."""

import secrets
import time
from contextlib import AsyncExitStack

from sqlalchemy import func, select

from app.db.base import AsyncSessionLocal as Session
from app.db.models.admin_app import AdminChange, AdminState
from app.services.admin_app import configuration as cfg
from app.services.admin_app.auth import fail, identity


async def refresh_actor(session, previous):
    fresh = await identity(session, previous["id"], lock=True)
    if (fresh["owner"], fresh.get("access_version", 0)) != (previous["owner"], previous.get("access_version", 0)):
        fail(401, "مجوز نشست هنگام انتظار تغییر کرده؛ دوباره از ربات وارد شوید.")
    return fresh


def validate_target(entity, target):
    if not isinstance(target, str) or not 1 <= len(target) <= 100:
        fail(422, "شناسه نامعتبر")
    if entity in ("plans", "users", "services", "tickets", "grants"):
        if entity == "plans" and target == "new":
            return
        if not target.isascii() or not target.isdigit() or not 0 < int(target) < 2**52:
            fail(422, "شناسه عددی نامعتبر")
    elif entity == "layout" and target != "home":
        fail(404, "این منو هنوز جابه‌جایی آزاد ندارد.")


async def document(actor, entity, target):
    validate_target(entity, target)
    cfg.permission(actor, entity, target)
    async with Session() as session:
        value = await cfg.snapshot(session, entity, target)
        return {"value": value, "version": cfg.fingerprint(value)}


async def draft(actor, body):
    cfg.fields(body, ("entity", "target", "value", "version", "reason"))
    entity, target = body.get("entity"), body.get("target")
    if not isinstance(entity, str):
        fail(422, "بخش نامعتبر")
    validate_target(entity, target)
    cfg.permission(actor, entity, target)
    reason = cfg.label(body.get("reason", "تغییر از مینی‌اپ"), 200)
    async with Session() as session, session.begin():
        # The seeded singleton serializes web writers including role changes.
        if not await session.get(AdminState, 1, with_for_update=True):
            fail(503, "migration مینی‌اپ اجرا نشده است.")
        actor = await refresh_actor(session, actor)
        before = await cfg.snapshot(session, entity, target)
        if cfg.fingerprint(before) != body.get("version"):
            fail(409, "اطلاعات تغییر کرده؛ صفحه را تازه کنید.")
        after = await cfg.validate(session, entity, target, body.get("value"), before, actor)
        now = int(time.time())
        count = await session.scalar(
            select(func.count())
            .select_from(AdminChange)
            .where(AdminChange.actor_id == actor["id"], AdminChange.created_at > now - 3600)
        )
        if count >= 120:
            fail(429, "سقف تغییرات ساعتی پر شده است.")
        row = AdminChange(
            token=secrets.token_hex(16),
            actor_id=actor["id"],
            entity=entity,
            target=target,
            before=before,
            after=after,
            reason=reason,
            status="draft",
            created_at=now,
        )
        session.add(row)
        return {
            "token": row.token,
            "before": before,
            "after": after,
            "expires_in": 900,
            "warning": {
                "plans": "تغییر شرایط پلن، برنامه‌های تمدید آینده را متوقف می‌کند؛ بازگرداندن قیمت، رضایت قبلی را خودکار زنده نمی‌کند. پرداخت ناتمام حفظ می‌شود.",
                "grants": "با انتشار مجوزها، همه نشست‌های قبلی این ادمین نامعتبر می‌شوند و ورود تازه لازم است.",
                "services": "فقط برداشت آینده متوقف می‌شود؛ درخواست قبلاً پرداخت‌شده باقی می‌ماند و باید تعیین تکلیف شود.",
                "users": "فقط دسترسی به ربات تغییر می‌کند؛ سرویس پنل حذف یا مسدود نمی‌شود و موجودی تغییر نمی‌کند.",
                "tickets": "پاسخ در گفت‌وگوی تیکت ثبت می‌شود. این عملیات تضمین ارسال اعلان تلگرام نیست.",
            }.get(
                entity,
                "تنظیمات پرداخت، تأیید خودکار و پاداش‌ها ممکن است مستقیماً روی وجه مشتری اثر بگذارند؛ مقادیر و پیکربندی درگاه را بررسی کنید."
                if target == "payment_settings"
                else "فقط پس از تأیید انتشار اعمال می‌شود. تغییرات قبلی خارج از این مینی‌اپ نیز در بررسی تعارض لحاظ می‌شوند.",
            ),
        }


async def publish(actor, token):
    async with Session() as session:
        row = await session.get(AdminChange, token)
        if not row or row.actor_id != actor["id"]:
            fail(404, "پیش‌نویس پیدا نشد.")
        entity, target = row.entity, row.target
    async with AsyncExitStack() as stack:
        if entity == "services":
            from app.services.auto_renew.locking import service_write_lock

            await stack.enter_async_context(service_write_lock(int(target)))
        async with Session() as session, session.begin():
            await session.get(AdminState, 1, with_for_update=True)
            actor = await refresh_actor(session, actor)
            cfg.permission(actor, entity, target)
            row = await session.get(AdminChange, token, with_for_update=True)
            if row.status == "published":
                return {"result": row.result, "replayed": True}
            if row.status != "draft" or int(time.time()) - row.created_at > 900:
                fail(409, "پیش‌نویس منقضی یا لغو شده است.")
            before = await cfg.snapshot(session, entity, target, lock=True)
            if before != row.before:
                fail(409, "تعارض با تغییر جدید؛ اطلاعات را دوباره بخوانید و تأییدیه تازه بسازید.")
            data = await cfg.validate(session, entity, target, row.after, before, actor)
            now = int(time.time())
            row.result = await cfg.apply(session, entity, target, data, actor, now)
            row.status, row.published_at = "published", now
            return {"result": row.result, "replayed": False}


async def discard(actor, token):
    async with Session() as session, session.begin():
        row = await session.get(AdminChange, token, with_for_update=True)
        if not row or row.actor_id != actor["id"]:
            fail(404, "پیش‌نویس پیدا نشد.")
        if row.status != "draft":
            fail(409, "تغییر قبلاً اعمال شده است.")
        row.status = "cancelled"
        return {"cancelled": True}


async def restore_draft(actor, token):
    async with Session() as session:
        row = await session.get(AdminChange, token)
        if not row or row.status != "published":
            fail(404, "تغییر منتشرشده پیدا نشد.")
        if row.entity not in ("settings", "buttons", "layout", "texts", "plans") or not row.before:
            fail(422, "بازگردانی فقط برای تنظیمات موجود است، نه ایجاد پلن یا عملیات مشتری/مالی.")
        cfg.permission(actor, row.entity, row.target)
        if row.actor_id != actor["id"] and not actor["owner"]:
            fail(403, "فقط مالک می‌تواند تغییر ادمین دیگر را بازگردانی کند.")
        current = await cfg.snapshot(session, row.entity, row.target)
        expected = row.after
        if row.entity == "plans":
            expected = {**expected, "id": int(row.target)}
        if current != expected:
            fail(409, "پس از این نسخه تغییر دیگری انجام شده؛ بازگردانی خودکار متوقف شد.")
        value = {k: v for k, v in row.before.items() if not (row.entity == "plans" and k == "id")}
        body = {
            "entity": row.entity,
            "target": row.target,
            "value": value,
            "version": cfg.fingerprint(current),
            "reason": "بازگردانی تغییر " + token[:8],
        }
    return await draft(actor, body)
