"""Authenticated, bounded webhook ingress. Failures are retryable, never false success."""

import hmac
import json

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

from app.logger import get_logger
from app.models.router_models import WebhookResponse
from app.routers.webhook.processor import process_webhook_events
from app.utils.security.secrets_cache import get_webhook_secret

logger = get_logger(__name__)
webhook_router = APIRouter()
MAX_BODY_BYTES = 1024 * 1024


@webhook_router.post("/webhook", response_model=WebhookResponse)
async def handle_webhook(request: Request) -> WebhookResponse:
    signature = request.headers.get("x-webhook-secret", "")
    try:
        secret = get_webhook_secret()
    except RuntimeError:
        raise HTTPException(503, "Webhook service not ready") from None
    if not signature or not hmac.compare_digest(signature.encode(), secret.encode()):
        raise HTTPException(403, "Invalid shared secret")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_BODY_BYTES:
            raise HTTPException(413, "Payload too large")
        body.extend(chunk)
    try:
        payload = json.loads(body)
    except ValueError, UnicodeError:
        raise HTTPException(400, "Invalid JSON") from None
    events = payload if isinstance(payload, list) else [payload]
    if not events or len(events) > 100 or any(not isinstance(event, dict) for event in events):
        raise HTTPException(400, "Expected 1 to 100 event objects")
    try:
        await process_webhook_events(events)
    except ValidationError:
        raise HTTPException(400, "Invalid event schema") from None
    except Exception:
        logger.exception("Webhook processing failed; sender should retry")
        raise HTTPException(503, "Event processing unavailable; retry later") from None
    return WebhookResponse(ok=True, message="Webhook processed")
