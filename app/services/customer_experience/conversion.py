"""Wallet-only, fixed-volume same-panel trial conversion with durable absolute targets.

No usage reset, account recreation, link rotation or automatic refund after an uncertain write.
Existing funded intents remain resumable even if new sales are disabled.
"""

import math
import secrets
import time

from httpx import HTTPStatusError
from pasarguard import UserModify
from sqlalchemy import func, select

from app.db.base import AsyncSessionLocal as Session
from app.db.models.customer_experience import TrialConversion
from app.db.models.panels import Panels
from app.db.models.plans import Plan
from app.db.models.user import User
from app.services.customer_experience.common import owned_service, require_user, settings
from app.services.customer_experience.journeys import event_in_session, purchase_in_session
from app.services.gifts import panel_call, panel_value
from app.services.locks import distributed_lock


class InsufficientBalance(ValueError):
    pass


def snapshot(plan):
    if (
        not plan
        or plan.plan_type != "volume"
        or plan.data_limit_reset_strategy != "no_reset"
        or not math.isfinite(plan.price)
        or plan.price <= 0
        or plan.price != int(plan.price)
        or not math.isfinite(plan.storage)
        or plan.storage <= 0
        or plan.duration <= 0
    ):
        raise ValueError("نسخه اول فقط پلن حجمی با قیمت صحیح، زمان مشخص و بدون ریست دوره‌ای را تبدیل می‌کند.")
    return {
        "price": int(plan.price),
        "storage": float(plan.storage),
        "duration": int(plan.duration),
        "ip_limit": int(plan.ip_limit or 0),
        "panel_code": int(plan.panel_code),
    }


def panel_snapshot(user):
    return {
        "data_limit": panel_value(user, "data_limit"),
        "expire": panel_value(user, "expire"),
        "hwid_limit": int(getattr(user, "hwid_limit", 0) or 0),
        "data_limit_reset_strategy": str(
            getattr(
                getattr(user, "data_limit_reset_strategy", "no_reset"),
                "value",
                getattr(user, "data_limit_reset_strategy", "no_reset"),
            )
        ),
    }


def validate_panel_user(user, order):
    if int(user.id) != order.panel_userid or user.username != order.username:
        raise ValueError("هویت کاربر پنل تغییر کرده است؛ بررسی پشتیبانی لازم است.")
    if str(getattr(user.status, "value", user.status)) not in ("active", "expired", "limited"):
        raise ValueError("وضعیت این سرویس اجازه تبدیل نمی‌دهد؛ با پشتیبانی تماس بگیرید.")
    if getattr(user, "next_plan", None):
        raise ValueError("سرویس برنامه بعدی دارد؛ ابتدا مدیر باید آن را بررسی کند.")


async def get_order(user_id, token):
    async with Session() as session:
        await require_user(session, user_id)
        order = await session.get(TrialConversion, token)
        if not order or order.user_id != user_id:
            raise ValueError("سفارش پیدا نشد یا متعلق به شما نیست.")
        return order


async def pending_order(user_id, code):
    async with Session() as session:
        await owned_service(session, user_id, code)
        return await session.scalar(
            select(TrialConversion).where(
                TrialConversion.user_id == user_id,
                TrialConversion.service_code == code,
                TrialConversion.status == "applying",
            )
        )


async def quote(user_id, code, plan_id):
    now = int(time.time())
    async with Session() as session, session.begin():
        await require_user(session, user_id, lock=True)
        config = await settings(session)
        if not (config.cx_conversion_enabled and config.sale_mode and config.bot_mode):
            raise ValueError("تبدیل تست فعلاً غیرفعال است.")
        service = await owned_service(session, user_id, code)
        panel = await session.get(Panels, service.in_panel)
        plan = await session.get(Plan, plan_id)
        if service.is_test is not True or not panel or not panel.enable or not service.panel_userid:
            raise ValueError("تست قابل تبدیل نیست یا پنل در دسترس نیست.")
        plan_data = snapshot(plan)
        if plan_data["panel_code"] != service.in_panel:
            raise ValueError("پلن باید متعلق به همان پنل تست باشد.")
        count = await session.scalar(
            select(func.count())
            .select_from(TrialConversion)
            .where(
                TrialConversion.user_id == user_id,
                TrialConversion.created_at > now - 900,
            )
        )
        if count >= 10:
            raise ValueError("تعداد درخواست‌ها زیاد است؛ از تأییدیه قبلی استفاده کنید یا ۱۵ دقیقه بعد تلاش کنید.")
        order = TrialConversion(
            token=secrets.token_hex(16),
            user_id=user_id,
            service_code=code,
            panel_code=panel.code,
            panel_userid=service.panel_userid,
            username=service.username,
            plan_id=plan_id,
            price=plan_data["price"],
            plan_snapshot=plan_data,
            status="quoted",
            created_at=now,
            expires_at=now + 900,
            attempted=False,
        )
        session.add(order)
        await event_in_session(session, user_id, code, "plan_selected", now)
        return order


async def _reserve(user_id, token, current):
    now = int(time.time())
    async with Session() as session, session.begin():
        user = await require_user(session, user_id, lock=True)
        order = await session.get(TrialConversion, token, with_for_update=True)
        if not order or order.user_id != user_id:
            raise ValueError("سفارش نامعتبر است.")
        if order.status != "quoted":
            return order
        service = await owned_service(session, user_id, order.service_code, lock=True)
        config = await settings(session)
        panel = await session.get(Panels, order.panel_code)
        if not (config.cx_conversion_enabled and config.sale_mode and config.bot_mode and panel and panel.enable):
            raise ValueError("فروش یا پنل غیرفعال است؛ مبلغی کسر نشد.")
        if (
            service.is_test is not True
            or service.in_panel != order.panel_code
            or service.panel_userid != order.panel_userid
            or service.username != order.username
        ):
            raise ValueError("مشخصات سرویس تغییر کرده است.")
        if order.expires_at <= now or snapshot(await session.get(Plan, order.plan_id)) != order.plan_snapshot:
            raise ValueError("قیمت/پلن یا اعتبار تأییدیه تغییر کرده؛ دوباره پلن را انتخاب کنید. مبلغی کسر نشد.")
        active = await session.scalar(
            select(TrialConversion.token).where(TrialConversion.active_service_code == service.code)
        )
        if active:
            raise ValueError("یک تبدیل برای این سرویس ثبت شده؛ همان درخواست را پیگیری کنید.")
        validate_panel_user(current, order)
        old = panel_snapshot(current)
        if old["data_limit"] <= 0 or old["expire"] is None or old["data_limit_reset_strategy"] != "no_reset":
            raise ValueError("تست نامحدود یا دارای ریست دوره‌ای قابل تبدیل نیست.")
        if int(user.amount or 0) < order.price:
            raise InsufficientBalance("موجودی کافی نیست؛ با /charge کیف پول را شارژ کنید و سپس دوباره تأیید بزنید.")
        target = {
            "data_limit": max(old["data_limit"], int(current.used_traffic or 0))
            + int(order.plan_snapshot["storage"] * 1024**3),
            "expire": now + order.plan_snapshot["duration"] * 86400,
            "hwid_limit": order.plan_snapshot["ip_limit"],
            "data_limit_reset_strategy": "no_reset",
        }
        # Validate the SDK payload before deducting any money.
        UserModify(**target, status="active")
        user.amount = int(user.amount or 0) - order.price
        order.old_values, order.target_values = old, target
        order.status, order.active_service_code = "applying", service.code
        return order


async def _refund_definitive_rejection(user_id, token):
    async with Session() as session, session.begin():
        user = await session.get(User, user_id, with_for_update=True)
        if not user:
            raise ValueError("حساب کیف پول برای بازپرداخت پیدا نشد.")
        order = await session.get(TrialConversion, token, with_for_update=True)
        if order and order.user_id == user_id and order.status == "applying":
            user.amount = int(user.amount or 0) + order.price
            order.status, order.active_service_code = "refunded", None
            order.completed_at = int(time.time())


async def _complete(user_id, token):
    now = int(time.time())
    async with Session() as session, session.begin():
        await require_user(session, user_id, lock=True)
        order = await session.get(TrialConversion, token, with_for_update=True)
        if order.status == "applied":
            return order
        if order.status != "applying":
            raise ValueError("وضعیت سفارش نیازمند بررسی است.")
        service = await owned_service(session, user_id, order.service_code, lock=True)
        if (
            service.in_panel != order.panel_code
            or service.panel_userid != order.panel_userid
            or service.username != order.username
        ):
            raise ValueError("تطبیق محلی نیازمند بررسی مدیر است؛ دوباره پرداخت نکنید.")
        service.is_test, service.enable = False, True
        service.package_size = order.target_values["data_limit"]
        service.expiration_time = order.target_values["expire"]
        service.ip_limit = order.target_values["hwid_limit"]
        service.data_limit_reset_strategy = "no_reset"
        service.warning, service.warning_time = 0, 0
        service.low_volume_notified, service.expire_notified = False, False
        order.status, order.completed_at = "applied", now
        await purchase_in_session(session, user_id, service.code, now, converted=True)
        return order


async def execute(user_id, token):
    order = await get_order(user_id, token)
    async with distributed_lock(f"cx-service:{order.service_code}"):
        order = await get_order(user_id, token)
        if order.status == "applied":
            return order
        if order.status not in ("quoted", "applying"):
            raise ValueError("این درخواست خاتمه یافته است؛ دوباره پلن را انتخاب کنید.")
        async with Session() as session:
            service = await owned_service(session, user_id, order.service_code)
            if (
                service.in_panel != order.panel_code
                or service.panel_userid != order.panel_userid
                or service.username != order.username
            ):
                raise ValueError("هویت سرویس تغییر کرده است؛ بررسی مدیر لازم است.")
            panel = await session.get(Panels, order.panel_code)
            if not panel:
                raise ValueError("پنل پیدا نشد؛ دوباره پرداخت نکنید.")
        current = await panel_call(panel, "get_user_by_id", user_id=order.panel_userid)
        validate_panel_user(current, order)
        if order.status == "quoted":
            order = await _reserve(user_id, token, current)
        values = panel_snapshot(current)
        if values == order.target_values:
            return await _complete(user_id, token)
        if order.target_values["expire"] <= int(time.time()):
            raise ValueError("مهلت هدف ذخیره‌شده گذشته است؛ بررسی مدیر لازم است. دوباره پرداخت نکنید.")
        if values != order.old_values:
            raise ValueError("وضعیت پنل با درخواست سازگار نیست؛ مبلغ محفوظ است. بررسی پشتیبانی لازم است.")
        first_attempt = not order.attempted
        async with Session() as session, session.begin():
            row = await session.get(TrialConversion, token, with_for_update=True)
            row.attempted = True  # durable before the external write
        try:
            await panel_call(
                panel,
                "modify_user_by_id",
                user_id=order.panel_userid,
                user=UserModify(**order.target_values, status="active"),
            )
        except HTTPStatusError as exc:
            if first_attempt and exc.response.status_code in (400, 403, 404, 422):
                await _refund_definitive_rejection(user_id, token)
            raise
        # Read-after-write; do not declare success solely from an HTTP 200.
        current = await panel_call(panel, "get_user_by_id", user_id=order.panel_userid)
        validate_panel_user(current, order)
        if panel_snapshot(current) != order.target_values:
            raise ValueError("نتیجه پنل هنوز تطبیق داده نشده؛ همان درخواست را پیگیری کنید، دوباره پرداخت نکنید.")
        return await _complete(user_id, token)
