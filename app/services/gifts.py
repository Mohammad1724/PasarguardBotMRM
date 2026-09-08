"""Gift fulfillment: wallet effects are transactional; panel effects have a durable intent.

Uncertain external writes retain their reservation. Retrying the same request reconciles
against the saved absolute target, never blindly adds the gift again.
"""

from __future__ import annotations

import json
import re
import time
from datetime import UTC, datetime, timedelta

from httpx import HTTPStatusError
from pasarguard import UserModify
from sqlalchemy import func, select

from app.db.base import AsyncSessionLocal as Session
from app.db.crud.services import ServiceCRUD
from app.db.models.gift_codes import GiftCode, GiftCodeUse
from app.db.models.transaction import Transaction
from app.db.models.user import User
from app.services.auto_renew.guards import service_argument_guard
from app.services.billing.renewal import require_panel_userid
from app.services.panels.auth import create_panel_api, panel_uses_api_key, refresh_panel_cookie

CODE_PATTERN = re.compile(r"[A-Z0-9_-]{1,24}\Z")


def validate_code(code: str) -> str:
    code = code.strip().upper()
    if not CODE_PATTERN.fullmatch(code):
        raise ValueError("کد باید ۱ تا ۲۴ کاراکتر انگلیسی، عدد، خط تیره یا زیرخط باشد.")
    return code


def validate_gift(gift, user_uses: int, now: int) -> None:
    if not gift or not gift.is_active:
        raise ValueError("کد هدیه نامعتبر یا غیرفعال است.")
    if gift.expires_at and gift.expires_at <= now:
        raise ValueError("کد هدیه منقضی شده است.")
    if gift.type not in ("balance", "days", "volume") or int(gift.value) <= 0:
        raise ValueError("مقدار کد هدیه نامعتبر است.")
    if gift.times_used >= gift.max_uses:
        raise ValueError("ظرفیت استفاده از کد تمام شده است.")
    if user_uses >= max(1, int(gift.per_user_limit)):
        raise ValueError("شما قبلاً از این کد استفاده کرده‌اید.")


async def reserve_gift(code: str, user_id: int, request_id: str, service_code=None, plan=None):
    async with Session() as session, session.begin():
        user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
        if not user:
            raise ValueError("کاربر پیدا نشد.")
        gift = await session.scalar(select(GiftCode).where(GiftCode.code == code).with_for_update())
        existing = await session.scalar(select(GiftCodeUse).where(GiftCodeUse.request_id == request_id))
        if existing:
            if existing.user_id != user_id or existing.code != code or existing.service_code != service_code:
                raise ValueError("درخواست با سابقه کد سازگار نیست.")
            return existing, int(user.amount or 0)
        unfinished = await session.scalar(
            select(GiftCodeUse.id)
            .where(GiftCodeUse.code == code, GiftCodeUse.user_id == user_id, GiftCodeUse.status == "applying")
            .limit(1)
        )
        if unfinished:
            raise ValueError("یک درخواست ناتمام برای این کد دارید؛ ابتدا همان درخواست را پیگیری کنید.")
        count = await session.scalar(
            select(func.count())
            .select_from(GiftCodeUse)
            .where(
                GiftCodeUse.code_id == gift.id if gift else False,
                GiftCodeUse.user_id == user_id,
                GiftCodeUse.status != "cancelled",
            )
        )
        validate_gift(gift, int(count or 0), int(time.time()))
        if (gift.type == "balance") != (service_code is None):
            raise ValueError("نوع کد با مقصد انتخابی سازگار نیست.")
        gift.times_used += 1
        use = GiftCodeUse(
            code_id=gift.id,
            code=gift.code,
            user_id=user_id,
            service_code=service_code,
            value=gift.value,
            used_at=int(time.time()),
            request_id=request_id,
            status="applied" if gift.type == "balance" else "applying",
            plan=json.dumps(plan) if plan else None,
        )
        session.add(use)
        if gift.type == "balance":
            user.amount = int(user.amount or 0) + int(gift.value)
            session.add(
                Transaction(
                    user_id=user_id,
                    amount=gift.value,
                    method="gift",
                    status="approved",
                    created_at=int(time.time()),
                    completed_at=int(time.time()),
                )
            )
        await session.flush()
        return use, int(user.amount or 0)


async def get_unfinished_gift(code: str, user_id: int):
    async with Session() as session:
        return await session.scalar(
            select(GiftCodeUse)
            .where(GiftCodeUse.code == code, GiftCodeUse.user_id == user_id, GiftCodeUse.status == "applying")
            .order_by(GiftCodeUse.id)
            .limit(1)
        )


async def get_use(request_id: str):
    async with Session() as session:
        return await session.scalar(select(GiftCodeUse).where(GiftCodeUse.request_id == request_id))


async def cancel_unapplied_use(request_id: str, user_id: int) -> bool:
    """Only for a definitively rejected write, never a timeout/ambiguous external result."""
    async with Session() as session, session.begin():
        use = await session.scalar(
            select(GiftCodeUse)
            .where(GiftCodeUse.request_id == request_id, GiftCodeUse.user_id == user_id)
            .with_for_update()
        )
        if not use or use.status != "applying":
            return False
        gift = await session.scalar(select(GiftCode).where(GiftCode.id == use.code_id).with_for_update())
        if gift:
            gift.times_used = max(0, gift.times_used - 1)
        use.status = "cancelled"
        return True


def panel_value(user, field: str):
    if field == "data_limit":
        return int(user.data_limit or 0)
    value = user.expire
    if value is None:
        return None
    if isinstance(value, datetime):
        return int((value if value.tzinfo else value.replace(tzinfo=UTC)).timestamp())
    return int(value)


def make_plan(gift, panel_user):
    field = "expire" if gift.type == "days" else "data_limit"
    old = panel_value(panel_user, field)
    if old is None or (field == "data_limit" and old == 0):
        raise ValueError("این بخش از سرویس نامحدود است؛ برای حفظ نامحدود بودن، هدیه اعمال نشد.")
    if field == "expire":
        new = max(old, int(time.time())) + int(timedelta(days=int(gift.value)).total_seconds())
    else:
        new = old + int(gift.value) * 1024**3
    return {"field": field, "old": old, "new": new}


async def panel_call(panel, method: str, **kwargs):
    api = create_panel_api(panel)
    try:
        return await getattr(api, method)(**kwargs)
    except HTTPStatusError as exc:
        if exc.response.status_code != 401 or panel_uses_api_key(panel):
            raise
        panel.cookie = await refresh_panel_cookie(panel)
        return await getattr(create_panel_api(panel), method)(**kwargs)


@service_argument_guard
async def apply_service_gift(gift, service, panel, user_id: int, request_id: str):
    from app.services.locks import distributed_lock

    # Fail closed if Redis is down. A fixed per-service key serializes this feature.
    async with distributed_lock(f"gift-service:{service.code}"):
        use = await get_use(request_id)
        if use and (use.user_id != user_id or use.service_code != str(service.code) or use.code_id != gift.id):
            raise ValueError("درخواست نامعتبر است.")
        if use and use.status == "applied":
            return use
        if use and use.status == "cancelled":
            raise ValueError("این درخواست لغو شده است؛ کد را دوباره وارد کنید.")
        userid = require_panel_userid(service)
        current = await panel_call(panel, "get_user_by_id", user_id=userid)
        if use:
            plan = json.loads(use.plan)
        else:
            plan = make_plan(gift, current)
            use, _ = await reserve_gift(gift.code, user_id, request_id, str(service.code), plan)
            plan = json.loads(use.plan)
        value = panel_value(current, plan["field"])
        if value not in (plan["old"], plan["new"]):
            raise ValueError("وضعیت سرویس تغییر کرده؛ برای تطبیق هدیه با پشتیبانی تماس بگیرید. کد دوباره مصرف نشد.")
        if value == plan["old"]:
            target = datetime.fromtimestamp(plan["new"], UTC) if plan["field"] == "expire" else plan["new"]
            try:
                await panel_call(panel, "modify_user_by_id", user_id=userid, user=UserModify(**{plan["field"]: target}))
            except HTTPStatusError as exc:
                if exc.response.status_code in (400, 403, 404, 422):
                    await cancel_unapplied_use(request_id, user_id)
                raise
            # Timeout/5xx keeps the intent: a retry reads the panel before writing again.
        updates = (
            {"expiration_time": plan["new"], "warning": 0, "warning_time": 0, "expire_notified": False}
            if plan["field"] == "expire"
            else {"package_size": plan["new"], "low_volume_notified": False}
        )
        synced, _message = await ServiceCRUD().update_service(code=service.code, **updates)
        if not synced:
            raise RuntimeError("Panel updated; local sync pending. Retry this request.")
        async with Session() as session, session.begin():
            row = await session.scalar(
                select(GiftCodeUse).where(GiftCodeUse.request_id == request_id).with_for_update()
            )
            row.status = "applied"
        use.status = "applied"
        return use
