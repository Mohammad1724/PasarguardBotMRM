"""A customer keyboard entry opens a signed inline WebView, not an admin panel."""

import re

from fastapi import HTTPException
from telethon import events
from telethon.tl.types import KeyboardButtonWebView

from app.db.base import AsyncSessionLocal as Session
from app.services import customer_app as customer

MODULE_NAME = "user.customer_app"
MODULE_ENABLED = True
MODULE_ORDER = 110
_registered_clients = set()


def matches(event):
    if not event.is_private:
        return False
    text = (event.raw_text or "").strip()
    return text == customer.CUSTOMER_APP_LABEL or bool(re.fullmatch(r"/miniapp(?:@\w+)?", text, re.I))


async def open_app(event):
    if not event.is_private:
        return
    try:
        address = customer.url()
        async with Session() as session:
            await customer.identity(session, event.sender_id)
    except HTTPException as exc:
        await event.respond(exc.detail)
        raise events.StopPropagation from None
    await event.respond(
        "🖥 حساب من\nموجودی کیف پول و سرویس‌های خودتان را اینجا ببینید. ورود با حساب تلگرام شما انجام می‌شود.",
        buttons=[[KeyboardButtonWebView("باز کردن داشبورد من", address)]],
    )
    # Read-only entry: do not reset an in-progress payment/conversation state.
    raise events.StopPropagation


def setup(client):
    if id(client) in _registered_clients:
        return
    _registered_clients.add(id(client))
    client.add_event_handler(open_app, events.NewMessage(incoming=True, func=matches))
