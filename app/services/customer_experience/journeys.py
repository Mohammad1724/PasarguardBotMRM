"""Opt-in trial follow-up and descriptive conversion metrics (not causal attribution)."""

import hashlib
import time
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select, update

from app.db.base import AsyncSessionLocal as Session
from app.db.models.customer_experience import CustomerEvent, TrialConversion, TrialJourney
from app.db.models.services import Service
from app.services.customer_experience.common import owned_service, require_user, settings

RETENTION = 7 * 86400
FOLLOWUP_DELAY = 2 * 3600
FOLLOWUP_WINDOW = 48 * 3600
EVENT_NAMES = {
    "trial_created",
    "guide_started",
    "connected_self_reported",
    "plan_selected",
    "purchase_delivered",
    "trial_converted",
}


async def event_in_session(session, user_id, service_code, name, now):
    if name not in EVENT_NAMES:
        raise ValueError("Unknown event")
    key = f"{user_id}:{service_code}:{name}"
    if not await session.get(CustomerEvent, key):
        session.add(CustomerEvent(key=key, user_id=user_id, service_code=service_code, name=name, created_at=now))


async def track_event(user_id, service_code, name):
    async with Session() as session, session.begin():
        await require_user(session, user_id, lock=True)
        await owned_service(session, user_id, service_code)
        await event_in_session(session, user_id, service_code, name, int(time.time()))


async def enroll_in_session(session, service, config):
    if not any(getattr(config, k) for k in ("cx_onboarding_enabled", "cx_conversion_enabled", "cx_followup_enabled")):
        return
    now = int(time.time())
    expires = int(service.expiration_time or now)
    # Stable user-level 20% holdout. No claim of significance for small samples.
    group = "holdout" if int(hashlib.sha256(str(service.id).encode()).hexdigest()[:8], 16) % 5 == 0 else "message"
    session.add(
        TrialJourney(
            service_code=service.code,
            user_id=service.id,
            panel_code=service.in_panel,
            created_at=now,
            expires_at=expires,
            retain_until=expires + RETENTION,
            experiment_group=group,
            opted_in=False,
            followup_status="pending",
        )
    )
    await event_in_session(session, service.id, service.code, "trial_created", now)


async def purchase_in_session(session, user_id, service_code, now, *, converted=False):
    await event_in_session(
        session, user_id, service_code, "trial_converted" if converted else "purchase_delivered", now
    )
    if converted:
        row = await session.get(TrialJourney, service_code)
        if row and not row.ended_at:
            row.ended_at = min(now, row.expires_at)
            row.retain_until = row.ended_at + RETENTION

    await session.execute(
        update(TrialJourney)
        .where(
            TrialJourney.user_id == user_id,
            TrialJourney.first_purchase_at.is_(None),
        )
        .values(first_purchase_at=now)
    )
    await session.execute(
        update(TrialJourney)
        .where(
            TrialJourney.user_id == user_id,
            TrialJourney.followup_status.in_(("pending", "claimed")),
        )
        .values(followup_status="purchased")
    )


async def consent(user_id, service_code=None, *, allowed=False):
    async with Session() as session, session.begin():
        await require_user(session, user_id, lock=True)
        if not allowed:
            await session.execute(update(TrialJourney).where(TrialJourney.user_id == user_id).values(opted_in=False))
            await session.execute(
                update(TrialJourney)
                .where(
                    TrialJourney.user_id == user_id,
                    TrialJourney.followup_status.in_(("pending", "claimed")),
                )
                .values(followup_status="opted_out")
            )
            return
        config = await settings(session)
        if not config.cx_followup_enabled:
            raise ValueError("یادآوری فعلاً غیرفعال است.")
        await owned_service(session, user_id, service_code)
        row = await session.get(TrialJourney, service_code)
        if not row or row.user_id != user_id or row.first_purchase_at:
            raise ValueError("این تست در برنامه یادآوری نیست.")
        row.opted_in = True
        row.consented_at = row.consented_at or int(time.time())
        if row.followup_status == "opted_out" and row.attempted_at is None:
            row.followup_status = "pending"


async def click(user_id, service_code):
    async with Session() as session, session.begin():
        await require_user(session, user_id, lock=True)
        await owned_service(session, user_id, service_code)
        row = await session.get(TrialJourney, service_code)
        if row and row.user_id == user_id and row.sent_at and not row.clicked_at:
            row.clicked_at = int(time.time())


def sending_hour(now):
    return 10 <= datetime.fromtimestamp(now, UTC).astimezone(ZoneInfo("Asia/Tehran")).hour < 21


async def _eligible(session, row, now):
    if not row.opted_in or row.first_purchase_at or not row.ended_at:
        return False
    if not row.ended_at + FOLLOWUP_DELAY <= now <= row.ended_at + FOLLOWUP_WINDOW:
        return False
    try:
        await require_user(session, row.user_id)
        service = await owned_service(session, row.user_id, row.service_code)
    except ValueError:
        return False
    if service.is_test is not True:
        return False
    if await session.scalar(
        select(Service.code)
        .where(
            Service.id == row.user_id,
            or_(Service.is_test.is_(False), Service.is_test.is_(None)),
        )
        .limit(1)
    ):
        return False
    return not await session.scalar(
        select(TrialConversion.token)
        .where(
            TrialConversion.user_id == row.user_id,
            TrialConversion.status == "applying",
        )
        .limit(1)
    )


async def send_followups(send, *, now=None):
    """At most one delivery attempt per user. An ambiguous send is NEVER retried automatically.

    A committed claim survives process death. Sending/DB acknowledgement is not atomic;
    claimed rows after a crash remain visible, not silently resent.
    """
    now = int(time.time()) if now is None else now
    if not sending_hour(now):
        return 0
    async with Session() as session:
        config = await settings(session)
        if not (config.cx_followup_enabled and config.bot_mode and config.sale_mode):
            return 0
        candidates = list(
            (
                await session.execute(
                    select(TrialJourney.service_code, TrialJourney.user_id)
                    .where(
                        TrialJourney.followup_status == "pending",
                        TrialJourney.opted_in.is_(True),
                        TrialJourney.attempted_at.is_(None),
                        TrialJourney.ended_at <= now - FOLLOWUP_DELAY,
                    )
                    .order_by(TrialJourney.ended_at)
                    .limit(50)
                )
            ).all()
        )
    sent = 0
    for code, user_id in candidates:
        async with Session() as session, session.begin():
            # Lock the user BEFORE reading this mutable journey. An initial snapshot read
            # followed by FOR UPDATE can fail with MariaDB 1020 during competing claims.
            try:
                await require_user(session, user_id, lock=True)
            except ValueError:
                await session.execute(
                    update(TrialJourney)
                    .where(
                        TrialJourney.service_code == code,
                        TrialJourney.followup_status == "pending",
                    )
                    .values(followup_status="ineligible")
                )
                continue
            row = await session.get(TrialJourney, code, with_for_update=True)
            if not row or row.user_id != user_id:
                continue
            if row.followup_status != "pending" or row.attempted_at is not None:
                continue
            if not await _eligible(session, row, now):
                row.followup_status = "ineligible"
                continue
            previous = await session.scalar(
                select(TrialJourney.service_code)
                .where(
                    TrialJourney.user_id == row.user_id,
                    TrialJourney.service_code != code,
                    or_(TrialJourney.attempted_at.is_not(None), TrialJourney.followup_status == "holdout"),
                )
                .limit(1)
            )
            if previous:
                row.followup_status = "capped"
                continue
            row.followup_status = "holdout" if row.experiment_group == "holdout" else "claimed"
            if row.experiment_group == "holdout":
                continue
            row.attempted_at = now
            user_id = row.user_id
        # Recheck switches, opt-out, paid service and ownership immediately before sending.
        async with Session() as session:
            row = await session.get(TrialJourney, code)
            config = await settings(session)
            eligible = (
                config.cx_followup_enabled
                and config.bot_mode
                and config.sale_mode
                and row.followup_status == "claimed"
                and await _eligible(session, row, now)
            )
        if not eligible:
            async with Session() as session, session.begin():
                await session.execute(
                    update(TrialJourney)
                    .where(
                        TrialJourney.service_code == code,
                        TrialJourney.followup_status == "claimed",
                    )
                    .values(followup_status="cancelled")
                )
            continue
        try:
            import asyncio

            async with asyncio.timeout(20):
                await send(user_id, code)
            status = "sent"
            sent += 1
        except Exception:
            status = "uncertain"
        async with Session() as session, session.begin():
            # Do not overwrite a concurrent purchase/opt-out. sent_at is delivery evidence only.
            row = await session.get(TrialJourney, code, with_for_update=True)
            if row.followup_status == "claimed":
                row.followup_status = status
            if status == "sent":
                row.sent_at = now
    return sent


async def report(now=None):
    now = int(time.time()) if now is None else now
    since = now - 30 * 86400
    async with Session() as session:
        totals = dict(
            (
                await session.execute(
                    select(CustomerEvent.name, func.count())
                    .where(
                        CustomerEvent.created_at >= since,
                    )
                    .group_by(CustomerEvent.name)
                )
            ).all()
        )
        states = dict(
            (
                await session.execute(
                    select(TrialJourney.followup_status, func.count())
                    .where(
                        TrialJourney.created_at >= since,
                    )
                    .group_by(TrialJourney.followup_status)
                )
            ).all()
        )
        cohorts = {}
        for group in ("message", "holdout"):
            base = [
                TrialJourney.experiment_group == group,
                TrialJourney.consented_at.is_not(None),
                TrialJourney.created_at >= since,
                TrialJourney.ended_at <= now - RETENTION,
            ]
            total = await session.scalar(select(func.count()).select_from(TrialJourney).where(*base))
            paid = await session.scalar(
                select(func.count())
                .select_from(TrialJourney)
                .where(
                    *base,
                    TrialJourney.first_purchase_at >= TrialJourney.created_at,
                    TrialJourney.first_purchase_at <= TrialJourney.ended_at + RETENTION,
                )
            )
            cohorts[group] = (total, paid)
        pending = await session.scalar(
            select(func.count()).select_from(TrialConversion).where(TrialConversion.status == "applying")
        )
        return totals, states, cohorts, pending
