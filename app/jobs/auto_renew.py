"""Bounded, restart-safe wallet renewals. Flags gate new charges, not recovery of paid intents."""

import asyncio

from sqlalchemy import select
from telethon import Button

from app import Kenzo
from app.db.base import AsyncSessionLocal as Session
from app.db.models.auto_renew import AutoRenewAttempt as Attempt, AutoRenewPolicy as Policy
from app.logger import get_logger
from app.services.auto_renew.notices import dispatch
from app.services.auto_renew.service import now_ts, process_policy, reconcile
from app.services.locks import distributed_lock

logger = get_logger(__name__)


async def send_notice(row, text):
    await Kenzo.send_message(
        row.user_id,
        text,
        parse_mode=None,
        buttons=[
            [Button.inline("مدیریت تمدید خودکار", f"ar:view:{row.service_code}")],
            [Button.inline("خاموش کردن تمدیدهای بعدی", f"ar:off:{row.service_code}")],
        ],
    )


async def auto_renew_job():
    try:
        async with distributed_lock("auto-renew-job"), asyncio.timeout(45):
            now = now_ts()
            async with Session() as session:
                attempts = list(
                    (
                        await session.scalars(
                            select(Attempt.token)
                            .where(
                                Attempt.status == "applying",
                                Attempt.next_retry_at <= now,
                            )
                            .order_by(Attempt.next_retry_at)
                            .limit(10)
                        )
                    ).all()
                )
                codes = list(
                    (
                        await session.scalars(
                            select(Policy.service_code)
                            .where(
                                Policy.state == "enabled",
                                Policy.next_check_at <= now,
                            )
                            .order_by(Policy.next_check_at)
                            .limit(10)
                        )
                    ).all()
                )
            await dispatch(send_notice, limit=10)
            for token in attempts:
                try:
                    await reconcile(token)
                except Exception as exc:
                    logger.warning("Renew recovery deferred error_type=%s", type(exc).__name__)
            for code in codes:
                try:
                    await process_policy(code)
                except Exception as exc:
                    logger.warning("Renew check deferred code=%s error_type=%s", code, type(exc).__name__)
            await dispatch(send_notice, limit=10)
    except TimeoutError, RuntimeError:
        logger.info("Auto-renew tick deferred (time budget, Redis unavailable or another worker)")
