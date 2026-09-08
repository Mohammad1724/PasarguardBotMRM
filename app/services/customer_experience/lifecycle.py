"""One retirement path for cron and webhooks, serialized with trial conversion."""

import asyncio
import time

from httpx import HTTPStatusError
from sqlalchemy import and_, exists, or_, select

from app.db.base import AsyncSessionLocal as Session
from app.db.models.customer_experience import TrialConversion, TrialJourney
from app.db.models.panels import Panels
from app.db.models.services import Service
from app.logger import get_logger
from app.services.customer_experience.common import settings
from app.services.customer_experience.journeys import RETENTION, enroll_in_session
from app.services.gifts import panel_call, panel_value
from app.services.locks import distributed_lock

logger = get_logger(__name__)


async def retire_trial(code, *, now=None):
    """Returns True only when deleted. Stale events never delete an already converted user."""
    now = int(time.time()) if now is None else now
    async with distributed_lock(f"cx-service:{code}"):
        async with Session() as session:
            service = await session.get(Service, code)
            if not service or service.is_test is not True:
                return False
            if await session.scalar(
                select(TrialConversion.token)
                .where(
                    TrialConversion.service_code == code,
                    TrialConversion.status == "applying",
                )
                .limit(1)
            ):
                return False
            panel = await session.get(Panels, service.in_panel)
            if not panel or not service.panel_userid:
                return False
        missing = False
        try:
            current = await panel_call(panel, "get_user_by_id", user_id=service.panel_userid)
            if int(current.id) != service.panel_userid or current.username != service.username:
                return False
            expire = panel_value(current, "expire")
            expired = expire is not None and expire <= now
            limited = int(current.data_limit or 0) > 0 and int(current.used_traffic or 0) >= int(current.data_limit)
            if not (expired or limited):
                if expire and expire > now and service.expiration_time != expire:
                    async with Session() as session, session.begin():
                        fresh = await session.get(Service, code, with_for_update=True)
                        if fresh and fresh.is_test is True:
                            fresh.expiration_time = expire
                return False
        except HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
            missing = True
        async with Session() as session, session.begin():
            config = await settings(session)
            row = await session.get(TrialJourney, code, with_for_update=True)
            if row is None:
                # Existing trials may be retained, but never silently opted into outreach.
                await enroll_in_session(session, service, config)
                await session.flush()
                row = await session.get(TrialJourney, code)
            if row:
                if not row.ended_at:
                    row.ended_at = min(now, int(service.expiration_time or now))
                    row.retain_until = row.ended_at + RETENTION
                if not missing and now < row.retain_until:
                    return False
        if not missing:
            try:
                await panel_call(panel, "remove_user_by_id", user_id=service.panel_userid)
            except HTTPStatusError as exc:
                if exc.response.status_code != 404:
                    raise
        async with Session() as session, session.begin():
            fresh = await session.get(Service, code, with_for_update=True)
            if fresh and fresh.is_test is True and fresh.panel_userid == service.panel_userid:
                await session.delete(fresh)
                deleted = True
            else:
                deleted = False
        if deleted:
            await notify_deletion(service)
        return deleted


async def notify_deletion(service):
    """Best-effort factual service notice, not a marketing follow-up."""
    from app import Kenzo

    try:
        async with asyncio.timeout(10):
            await Kenzo.send_message(
                service.id,
                f"تست #{service.code} با نام {service.username} پس از پایان مهلت نگه‌داری حذف شد.",
                parse_mode=None,
            )
    except Exception as exc:
        logger.warning("Trial deletion notice failed code=%s error_type=%s", service.code, type(exc).__name__)


async def cleanup_trials():
    now = int(time.time())
    # Keyset pagination is stable while rows are deleted; no OFFSET skipping.
    cursor = 0
    deleted = 0
    for _ in range(10):  # bounded to 500 candidates per tick
        async with Session() as session:
            codes = list(
                (
                    await session.scalars(
                        select(Service.code)
                        .outerjoin(
                            TrialJourney,
                            TrialJourney.service_code == Service.code,
                        )
                        .where(
                            Service.is_test.is_(True),
                            Service.code > cursor,
                            ~exists(
                                select(TrialConversion.token).where(
                                    TrialConversion.service_code == Service.code,
                                    TrialConversion.status == "applying",
                                )
                            ),
                            or_(
                                and_(Service.expiration_time <= now, TrialJourney.ended_at.is_(None)),
                                TrialJourney.retain_until <= now,
                            ),
                        )
                        .order_by(Service.code)
                        .limit(50)
                    )
                ).all()
            )
        if not codes:
            break
        for code in codes:
            cursor = code
            try:
                async with asyncio.timeout(25):
                    deleted += int(await retire_trial(code, now=now))
            except Exception as exc:
                logger.warning("Trial retirement deferred code=%s error_type=%s", code, type(exc).__name__)
    return deleted
