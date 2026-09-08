"""Paid cleanup rechecks the live expiry and never destroys an unsettled wallet intent."""

import asyncio

from httpx import HTTPStatusError
from sqlalchemy import select

from app.db.base import AsyncSessionLocal as Session
from app.db.models.auto_renew import AutoRenewPolicy as Policy
from app.db.models.panels import Panels
from app.db.models.services import Service
from app.db.redis import get_redis
from app.logger import get_logger
from app.services.auto_renew.locking import service_write_lock
from app.services.auto_renew.service import api_call, pending
from app.services.gifts import panel_value

logger = get_logger(__name__)
PAGE_SIZE, BATCHES = 50, 10
CURSOR_KEY = "auto-renew:paid-cleanup-cursor"


async def retire_paid(code, now):
    async with service_write_lock(code):
        async with Session() as session:
            service = await session.get(Service, code)
            if not service or service.is_test is True or await pending(session, code):
                return False
            if not service.expiration_time or service.expiration_time > now - 3 * 86400:
                return False
            panel = await session.get(Panels, service.in_panel)
            if not panel or not service.panel_userid:
                return False
        missing = False
        try:
            live = await api_call(panel, "get_user_by_id", user_id=service.panel_userid)
            if int(live.id) != service.panel_userid or live.username != service.username:
                return False
            expire = panel_value(live, "expire")
            if not expire or expire > now - 3 * 86400:
                return False
        except HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
            missing = True
        if not missing:
            try:
                await api_call(panel, "remove_user_by_id", user_id=service.panel_userid)
            except HTTPStatusError as exc:
                if exc.response.status_code != 404:
                    raise
        async with Session() as session, session.begin():
            fresh = await session.get(Service, code, with_for_update=True)
            current_panel = await session.get(Panels, service.in_panel)
            if (
                not fresh
                or fresh.is_test is True
                or fresh.panel_userid != service.panel_userid
                or fresh.in_panel != service.in_panel
                or fresh.username != service.username
                or fresh.id != service.id
                or not current_panel
                or current_panel.base_url != panel.base_url
            ):
                return False
            await session.delete(fresh)
            policy = await session.get(Policy, code, with_for_update=True)
            if policy:
                policy.state, policy.reason, policy.updated_at = "paused", "service_deleted", now
        return True


async def _cleanup_paid(panel_codes, now):
    if not panel_codes:
        return 0
    redis = await get_redis()
    cursor = int(await redis.get(CURSOR_KEY) or 0)
    deleted = 0
    for _ in range(BATCHES):
        async with Session() as session:
            rows = list(
                (
                    await session.scalars(
                        select(Service)
                        .where(
                            Service.in_panel.in_(panel_codes),
                            Service.is_test.is_not(True),
                            Service.code > cursor,
                            Service.expiration_time <= now - 3 * 86400,
                        )
                        .order_by(Service.code)
                        .limit(PAGE_SIZE)
                    )
                ).all()
            )
        if not rows:
            await redis.set(CURSOR_KEY, 0, ex=7 * 86400)
            break
        for row in rows:
            cursor = row.code
            # Claim progress before a slow/uncertain panel call; a dead first page
            # must not starve later codes across job runs.
            await redis.set(CURSOR_KEY, cursor, ex=7 * 86400)
            try:
                if await retire_paid(row.code, now):
                    deleted += 1
                    from app import Kenzo

                    await Kenzo.send_message(
                        row.id,
                        f"سرویس #{row.code} با نام {row.username} پس از سه روز از انقضا و عدم تمدید حذف شد.",
                        parse_mode=None,
                    )
            except Exception as exc:
                logger.warning("Paid cleanup/notice deferred code=%s error_type=%s", row.code, type(exc).__name__)
    return deleted


async def cleanup_paid(panel_codes, now):
    try:
        async with asyncio.timeout(45):
            return await _cleanup_paid(panel_codes, now)
    except TimeoutError:
        logger.info("Paid cleanup time budget reached; durable cursor continues next run")
        return None
