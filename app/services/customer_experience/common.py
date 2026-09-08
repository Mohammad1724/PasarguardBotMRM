"""Small shared access and settings primitives. Support roles grant no finance access."""

import time

from sqlalchemy import select

from app.db.base import AsyncSessionLocal as Session
from app.db.models.customer_experience import CustomerExperienceAudit
from app.db.models.services import Service
from app.db.models.settings import Settings, resolve_settings_update_kwargs
from app.db.models.user import User
from config import ADMIN_ID

FLAGS = ("cx_tickets_enabled", "cx_onboarding_enabled", "cx_conversion_enabled", "cx_followup_enabled")


async def settings(session):
    value = await session.scalar(select(Settings))
    if not value:
        raise ValueError("تنظیمات ربات در دسترس نیست؛ دوباره تلاش کنید.")
    return value


def staff_ids(config):
    ids = getattr(config, "cx_support_ids", [])
    if not isinstance(ids, list):
        ids = []
    return set(ADMIN_ID) | {int(i) for i in ids if str(i).isdigit() and int(i) > 0}


async def require_user(session, user_id, *, lock=False):
    query = select(User).where(User.id == user_id)
    user = await session.scalar(query.with_for_update() if lock else query)
    if not user or user.status in ("ban", "BlockedBot", "DeleteAccount"):
        raise ValueError("حساب در دسترس نیست. ابتدا /start را بفرستید.")
    return user


async def owned_service(session, user_id, code, *, lock=False):
    query = select(Service).where(Service.code == code, Service.id == user_id)
    service = await session.scalar(query.with_for_update() if lock else query)
    if not service:
        raise ValueError("سرویس پیدا نشد یا متعلق به شما نیست.")
    return service


async def change_setting(actor_id, key, value):
    if actor_id not in ADMIN_ID or key not in (*FLAGS, "cx_support_ids"):
        raise ValueError("فقط مالک ربات اجازه تغییر تنظیمات را دارد.")
    async with Session() as session, session.begin():
        config = await session.scalar(select(Settings).with_for_update())
        if not config:
            raise ValueError("تنظیمات پیدا نشد.")
        old = getattr(config, key)
        for column, updated in resolve_settings_update_kwargs(config, **{key: value}).items():
            setattr(config, column, updated)
        session.add(
            CustomerExperienceAudit(
                actor_id=actor_id, action="setting", detail=f"{key}: {old} -> {value}", created_at=int(time.time())
            )
        )
