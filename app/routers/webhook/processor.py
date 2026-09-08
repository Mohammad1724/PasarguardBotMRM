"""
Webhook event processor - routes events to appropriate handlers.
"""

from typing import Any

from app.logger import get_logger
from app.models.router_models import WebhookEvent
from app.routers.webhook.handlers import (
    handle_days_left_reached,
    handle_usage_percent_reached,
    handle_user_created,
    handle_user_deleted,
    handle_user_disabled,
    handle_user_enabled,
    handle_user_expired,
    handle_user_limited,
    handle_user_updated,
)

logger = get_logger(__name__)


async def process_webhook_events(events: list[dict[str, Any]]) -> None:
    """Process a list of webhook events."""

    import hashlib
    import json
    import time

    from app.db.base import AsyncSessionLocal
    from app.db.models.payment_safety import WebhookDelivery
    from app.services.locks import distributed_lock

    # Validate the entire batch before applying any event.
    validated = [(data, WebhookEvent(**data)) for data in events]
    for data, event in validated:
        digest = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        async with distributed_lock(f"webhook:{digest}"):
            async with AsyncSessionLocal() as session:
                delivered = await session.get(WebhookDelivery, digest)
                if delivered and delivered.completed_at > int(time.time()) - 86400:
                    continue
            await handle_event(event)
            async with AsyncSessionLocal() as session, session.begin():
                await session.merge(WebhookDelivery(digest=digest, completed_at=int(time.time())))


async def handle_event(event: WebhookEvent) -> None:
    """Handle a single webhook event."""

    event_handlers = {
        "user_created": handle_user_created,
        "user_updated": handle_user_updated,
        "user_deleted": handle_user_deleted,
        "user_limited": handle_user_limited,
        "user_expired": handle_user_expired,
        "user_disabled": handle_user_disabled,
        "user_enabled": handle_user_enabled,
        "reached_days_left": handle_days_left_reached,
        "reached_usage_percent": handle_usage_percent_reached,
    }

    handler = event_handlers.get(event.action)
    if handler:
        await handler(event)
    else:
        logger.warning(f"Unknown webhook action: {event.action}")
