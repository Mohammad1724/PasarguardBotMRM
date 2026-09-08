"""Message handlers for admin home panel."""

import contextlib
from functools import wraps

from telethon import Button, events
from telethon.tl.custom import Message

from app import Kenzo
from app.db.crud.keyboards import get_button_text
from app.logger import get_logger
from app.telegram.admin.admin_home.service import (
    ADD_PANEL_STEPS,
    open_admin_shortcut,
    send_admin_category,
    send_admin_home,
)
from app.telegram.keyboards.admin_navigation import (
    ADMIN_APP_LABEL,
    ADMIN_ROOT_LABEL,
    ADMIN_SHORTCUTS,
    CATEGORIES_BY_LABEL,
)
from app.telegram.keyboards.common import is_keyboard_config_step
from app.telegram.shared.url_presets import format_admin_links_message, get_bot_username
from app.telegram.state import get_step
from app.telegram.state.store import clear_user_conversation
from config import ADMIN_ID

logger = get_logger(__name__)


def _navigation_errors(handler):
    """Even on delivery/storage failure, never fall through into free-text consumers."""

    @wraps(handler)
    async def wrapped(event):
        try:
            return await handler(event)
        except events.StopPropagation:
            raise
        except Exception as exc:
            logger.warning("Admin navigation failed error_type=%s", type(exc).__name__)
            with contextlib.suppress(Exception):
                await event.respond("باز کردن این بخش ممکن نشد؛ کمی بعد دوباره /panel را بفرستید.", parse_mode=None)
            raise events.StopPropagation from None

    return wrapped


def _panel_command_filter(event: Message) -> bool:
    if event.sender_id not in ADMIN_ID or not event.is_private:
        return False
    msg = (event.message.text or "").strip()
    return msg in {"/panel", "🔙 بازگشت به پنل", ADMIN_ROOT_LABEL}


@_navigation_errors
async def message_handler_admin_panel(event: Message):
    if not _panel_command_filter(event):
        return
    user_id = event.sender_id
    if await get_step(user_id) in ADD_PANEL_STEPS:
        await clear_user_conversation(user_id)
    user = await event.get_sender()
    username = user.username if user else None
    await send_admin_home(user_id, username)
    raise events.StopPropagation


async def _admin_menu_entry_filter(event: Message) -> bool:
    if event.sender_id not in ADMIN_ID or not event.is_private:
        return False
    if is_keyboard_config_step(await get_step(event.sender_id)):
        return False
    msg = (event.message.text or "").strip()
    if msg == "🔗 لینک های آماده":
        return True
    menu_text = await get_button_text("bt.menu_admin_panel", "⚙️ پنل مدیریت")
    return msg in {menu_text, "⚙️ پنل مدیریت"}


@_navigation_errors
async def admin_menu_entry_handler(event: Message):
    if not await _admin_menu_entry_filter(event):
        return
    msg = (event.message.text or "").strip()
    if msg == "🔗 لینک های آماده":
        bot_username = await get_bot_username(Kenzo)
        links_message = format_admin_links_message(bot_username)
        buttons = [[Button.inline("🔙 بازگشت به پنل", data="back_to_admin_panel")]]
        await event.respond(links_message, buttons=buttons, parse_mode="md")
        raise events.StopPropagation

    user = await event.get_sender()
    await send_admin_home(event.sender_id, user.username if user else None)
    raise events.StopPropagation


async def admin_navigation_filter(event: Message) -> bool:
    if not event.is_private or event.sender_id not in ADMIN_ID:
        return False
    text = (event.message.text or "").strip()
    if text not in CATEGORIES_BY_LABEL and text not in ADMIN_SHORTCUTS and text != ADMIN_APP_LABEL:
        return False
    # A label typed in the keyboard editor is content, not a navigation command.
    return not is_keyboard_config_step(await get_step(event.sender_id))


@_navigation_errors
async def admin_navigation_handler(event: Message):
    if not await admin_navigation_filter(event):
        return
    text = (event.message.text or "").strip()
    category = CATEGORIES_BY_LABEL.get(text)
    try:
        if category:
            await send_admin_category(event.sender_id, category.key)
        else:
            await open_admin_shortcut(event, "miniapp" if text == ADMIN_APP_LABEL else ADMIN_SHORTCUTS[text])
    except ValueError as exc:
        await event.respond(str(exc), parse_mode=None)
    # Never let a category/shortcut become a ticket reply, broadcast or wizard value.
    raise events.StopPropagation


def register(client):
    client.add_event_handler(admin_navigation_handler, events.NewMessage(incoming=True, func=admin_navigation_filter))
    client.add_event_handler(
        message_handler_admin_panel,
        events.NewMessage(incoming=True, func=_panel_command_filter),
    )
    client.add_event_handler(
        admin_menu_entry_handler,
        events.NewMessage(incoming=True, func=_admin_menu_entry_filter),
    )
