"""Callback handlers for admin home panel."""

import contextlib

from telethon import events

from app.telegram.admin.admin_home.service import open_admin_shortcut, send_admin_category, send_admin_home
from app.telegram.keyboards.admin_navigation import ADMIN_NAV_PREFIX, CATEGORIES_BY_KEY
from config import ADMIN_ID


async def callback_back_to_admin_panel(event: events.CallbackQuery.Event):
    if not event.is_private or event.sender_id not in ADMIN_ID:
        return
    await event.answer()
    with contextlib.suppress(Exception):
        await event.delete()
    user = await event.get_sender()
    username = user.username if user else None
    await send_admin_home(event.sender_id, username)
    raise events.StopPropagation


async def callback_admin_navigation(event: events.CallbackQuery.Event):
    if not event.is_private or event.sender_id not in ADMIN_ID:
        await event.answer("این بخش مخصوص مالک ربات در گفتگوی خصوصی است.", alert=True)
        raise events.StopPropagation
    data = event.data.decode("utf-8", errors="replace")
    key = data.removeprefix(ADMIN_NAV_PREFIX)
    if not data.startswith(ADMIN_NAV_PREFIX) or key not in {*CATEGORIES_BY_KEY, "miniapp"}:
        await event.answer("دسته معتبر نیست؛ /panel را بفرستید.", alert=True)
        raise events.StopPropagation
    await event.answer()
    if key == "miniapp":
        await open_admin_shortcut(event, "miniapp")
    else:
        await send_admin_category(event.sender_id, key)
    raise events.StopPropagation


def register(client):
    client.add_event_handler(callback_admin_navigation, events.CallbackQuery(pattern=rb"^admin_menu:"))
    client.add_event_handler(
        callback_back_to_admin_panel,
        events.CallbackQuery(data="back_to_admin_panel", func=lambda e: e.sender_id in ADMIN_ID),
    )
