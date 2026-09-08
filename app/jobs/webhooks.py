"""Bound the retention of non-sensitive webhook deduplication hashes."""

import time

from sqlalchemy import delete

from app.db.base import AsyncSessionLocal
from app.db.models.payment_safety import WebhookDelivery


async def prune_webhook_deliveries():
    async with AsyncSessionLocal() as session, session.begin():
        await session.execute(
            delete(WebhookDelivery).where(WebhookDelivery.completed_at < int(time.time()) - 7 * 86400)
        )
