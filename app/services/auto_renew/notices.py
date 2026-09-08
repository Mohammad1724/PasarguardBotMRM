"""Durable at-most-once notice attempts. An unconfirmed advance notice never authorizes a debit."""

import asyncio

from sqlalchemy import select

from app.db.base import AsyncSessionLocal as Session
from app.db.models.auto_renew import AutoRenewAttempt, AutoRenewNotice as Notice, AutoRenewPolicy as Policy
from app.db.models.services import Service
from app.db.models.user import User
from app.services.auto_renew.service import now_ts
from app.services.customer_experience.common import settings

REASONS = {
    "plan_changed": "قیمت یا مشخصات پلن تغییر کرده؛ انتخاب و تأیید جدید لازم است.",
    "identity_changed": "مشخصات یا مالکیت سرویس تغییر کرده است.",
    "service_changed": "زمان، حجم یا محدودیت سرویس در پنل تغییر کرده؛ دوباره تأیید کنید.",
    "unsupported_service": "وضعیت فعلی سرویس برای تمدید خودکار مناسب نیست.",
    "too_late": "بیش از ۴۸ ساعت از انقضا گذشته؛ تمدید دستی لازم است.",
    "notice_unconfirmed": "تحویل اعلان پیش از برداشت تأیید نشد؛ تأییدیه جدید لازم است.",
    "cycle_already_attempted": "این دوره قبلاً درخواست داشته؛ رسید قبلی را بررسی کنید.",
    "charge_cap": "مبلغ از سقف مجاز بیشتر است.",
    "panel_rejected": "پنل درخواست را قطعی رد کرده و مبلغ برگشته است.",
    "account_unavailable": "حساب مشتری در دسترس نیست.",
    "low_balance": "موجودی کیف پول کافی نیست.",
    "monthly_cap": "سقف ماهانه همین سرویس پر شده است.",
    "cancelled": "به درخواست مشتری/مالک خاموش شده است.",
    "processing": "درخواست پرداخت‌شده در حال تطبیق است.",
}


def text_for(row):
    prefix = f"تمدید خودکار سرویس #{row.service_code}\n"
    if row.kind == "upcoming":
        return prefix + (
            f"مبلغ تمدید: {row.payload['price']:,} تومان از کیف پول.\n"
            "برداشت زودتر از ۲۴ ساعت پیش از انقضا و زودتر از ۶ ساعت پس از این اعلان انجام نمی‌شود.\n"
            f"سقف ماه میلادیِ همین سرویس: {row.payload['monthly_cap']:,} تومان.\n"
            "تا قبل از ثبت برداشت، می‌توانید آن را خاموش کنید."
        )
    if row.kind == "low_balance":
        return (
            prefix
            + f"موجودی کافی نیست؛ مبلغ لازم {row.payload['price']:,} تومان است.\nبا /charge شارژ کنید؛ تا ۴۸ ساعت پس از انقضا، در صورت باقی‌بودن رضایت شما، دوباره بررسی می‌شود."
        )
    if row.kind == "budget":
        return (
            prefix
            + "سقف ماهانه همین سرویس کافی نیست؛ هیچ مبلغی برای این تلاش کسر نشد. برای تغییر سقف، پلن را دوباره انتخاب و تأیید کنید."
        )
    if row.kind == "paused":
        return prefix + "برنامه متوقف شد.\n" + REASONS.get(row.payload.get("reason"), "بررسی تنظیمات لازم است.")
    if row.kind == "review":
        return prefix + "درخواست پرداخت‌شده نیازمند بررسی است؛ دوباره پرداخت نکنید. رسید: " + row.payload["token"]
    return (
        prefix
        + ("✅ تمدید انجام شد." if row.payload["status"] == "applied" else "مبلغ به کیف پول برگشت.")
        + f"\nمبلغ: {row.payload['price']:,} تومان\nرسید: {row.payload['token']}"
    )


async def eligible(session, row):
    user = await session.get(User, row.user_id)
    if not user or user.status in ("ban", "BlockedBot", "DeleteAccount"):
        return False
    if row.kind in ("receipt", "review"):
        order = await session.get(AutoRenewAttempt, row.payload.get("token"))
        return bool(order and order.user_id == row.user_id)
    policy = await session.get(Policy, row.service_code)
    service = await session.get(Service, row.service_code)
    if (
        not policy
        or policy.user_id != row.user_id
        or policy.revision != row.revision
        or not service
        or service.id != row.user_id
    ):
        return False
    if row.kind == "paused":
        return policy.state == "paused"
    config = await settings(session)
    return bool(
        config.auto_renew_enabled
        and config.bot_mode
        and config.tamdid_mode
        and policy.state == "enabled"
        and policy.expected_values["expire"] == row.cycle_expire
    )


async def dispatch(send, limit=20):
    async with Session() as session:
        keys = list(
            (
                await session.scalars(
                    select(Notice.key)
                    .where(Notice.state == "pending")
                    .order_by(Notice.created_at, Notice.key)
                    .limit(limit)
                )
            ).all()
        )
    for key in keys:
        async with Session() as session, session.begin():
            row = await session.get(Notice, key, with_for_update=True)
            if not row or row.state != "pending":
                continue
            if not await eligible(session, row):
                row.state = "cancelled"
                continue
            row.state = "claimed"  # crash means no resend; not permission to charge
        async with Session() as session:
            row = await session.get(Notice, key)
            allowed = await eligible(session, row)
        if not allowed:
            async with Session() as session, session.begin():
                (await session.get(Notice, key, with_for_update=True)).state = "cancelled"
            continue
        try:
            async with asyncio.timeout(10):
                await send(row, text_for(row))
            state = "sent"
        except Exception:
            state = "uncertain"
        async with Session() as session, session.begin():
            row = await session.get(Notice, key, with_for_update=True)
            row.state = state
            if state == "sent":
                row.sent_at = now_ts()
