"""User gift code module — redeem balance/days/volume gift codes."""

from __future__ import annotations

import contextlib

from telethon import Button, events
from telethon.tl.custom import Message

from app.db.crud.gift_codes import (
    REDEEM_EXHAUSTED,
    REDEEM_EXPIRED,
    REDEEM_INACTIVE,
    REDEEM_NOT_FOUND,
    REDEEM_USER_LIMIT,
    GiftCodeCRUD,
)
from app.db.crud.panels import PanelsManager
from app.db.crud.services import ServiceCRUD
from app.db.crud.settings import SettingsManager
from app.db.crud.user import UserCRUD
from app.logger import LogType, get_logger
from app.telegram.shared.guards.channel_gate import ensure_channel_membership
from app.telegram.shared.utils.maintenance import bot_is_offline
from app.telegram.state import clear_user, get_step, set_data, set_step
from app.telegram.user.gift import texts

logger = get_logger(__name__)

GIFT_REDEEM_CALLBACK_PREFIX = "giftdays:"

_STATUS_ALERTS = {
    REDEEM_NOT_FOUND: texts.GIFT_INVALID,
    REDEEM_INACTIVE: texts.GIFT_INACTIVE,
    REDEEM_EXPIRED: texts.GIFT_EXPIRED,
    REDEEM_EXHAUSTED: texts.GIFT_EXHAUSTED,
    REDEEM_USER_LIMIT: texts.GIFT_USER_LIMIT,
}


async def _gift_command_filter(event) -> bool:
    if event.is_channel or not event.is_private:
        return False
    from app.telegram.keyboards.common import is_keyboard_config_step

    if is_keyboard_config_step(await get_step(event.sender_id)):
        return False
    msg = (event.message.text or "").strip()
    if not msg:
        return False
    if msg.lower().startswith("/gift"):
        return True
    settings = await SettingsManager().get_settings()
    if not settings or not getattr(settings, "gift_mode", False):
        return False
    from app.db.crud.keyboards import get_button_text

    return msg == await get_button_text("bt.menu_gift", texts.GIFT_MENU_TEXT)


@bot_is_offline
async def gift_menu_handler(event: Message):
    if not await ensure_channel_membership(event):
        raise events.StopPropagation
    settings = await SettingsManager().get_settings()
    if not settings or not getattr(settings, "gift_mode", False):
        raise events.StopPropagation
    await clear_user(event.sender_id)
    await event.respond(texts.GIFT_PROMPT, parse_mode="md")
    await set_step(event.sender_id, "gift_redeem")
    raise events.StopPropagation


@bot_is_offline
async def gift_code_input_handler(event: Message):
    msg = (event.message.text or "").strip()
    if not msg:
        raise events.StopPropagation
    if msg in ("🏠", "🏠 بازگشت", "/start", "/panel"):
        from app.telegram.keyboards.home import bhome_buttons

        await clear_user(event.sender_id)
        info = await UserCRUD().read_user(event.sender_id)
        lang = info.language if info and info.language else "fa"
        await event.respond("🏠 به منوی اصلی بازگشتید.", buttons=await bhome_buttons(event.sender_id, lang))
        await set_step(event.sender_id, "home")
        raise events.StopPropagation

    import uuid

    from app.services.gifts import get_unfinished_gift, reserve_gift

    settings = await SettingsManager().get_settings()
    if not settings or not settings.gift_mode:
        await event.respond(texts.GIFT_INACTIVE)
        raise events.StopPropagation
    gift = await GiftCodeCRUD().get_by_code(msg)
    unfinished = await get_unfinished_gift(gift.code, event.sender_id) if gift else None
    if not gift or (not gift.is_active and not unfinished):
        await event.respond(texts.GIFT_INVALID)
        raise events.StopPropagation
    request_id = unfinished.request_id if unfinished else uuid.uuid4().hex
    if gift.type == "balance":
        try:
            _use, balance = await reserve_gift(gift.code, event.sender_id, request_id)
        except ValueError as exc:
            await event.respond(str(exc))
            raise events.StopPropagation from None
        await event.respond(
            texts.GIFT_BALANCE_DONE.format(code=gift.code, value=int(gift.value), balance=balance), parse_mode="md"
        )
        await _finish(event)
        raise events.StopPropagation

    services = await ServiceCRUD().get_services_reverse(event.sender_id)
    services = [s for s in services if s.in_panel and not getattr(s, "is_test", False)][:20]
    buttons = []
    for service in services:
        if unfinished and str(service.code) != unfinished.service_code:
            continue
        panel = await PanelsManager().get_panel_by_code(service.in_panel)
        if panel:
            buttons.append(
                [Button.inline(f"{service.username} ({panel.name})", f"giftapply:{request_id}:{service.code}")]
            )
    if not buttons:
        await event.respond(texts.GIFT_NO_SERVICES)
        await _finish(event)
        raise events.StopPropagation
    # No consumption until a service is selected and validated.
    await set_data(event.sender_id, "gift_pending", {"code": gift.code, "request_id": request_id})
    await event.respond("لطفاً سرویس موردنظر را انتخاب کنید:", buttons=buttons)
    await set_step(event.sender_id, "gift_service_pick")
    raise events.StopPropagation


async def _finish(event) -> None:
    from app.telegram.keyboards.home import bhome_buttons

    await clear_user(event.sender_id)
    info = await UserCRUD().read_user(event.sender_id)
    lang = info.language if info and info.language else "fa"
    await event.respond("🏠 به منوی اصلی بازگشتید.", buttons=await bhome_buttons(event.sender_id, lang))
    await set_step(event.sender_id, "home")


async def _log_redeem(gift, user_id: int, service_code, target: str) -> None:
    with contextlib.suppress(Exception):
        from app.telegram.shared.utils.logging import send_log_message

        await send_log_message(
            LogType.OTHER,
            message=(
                f"#استفاده_کد_هدیه\n"
                f"🎟 کد: `{gift.code}`\n"
                f"👤 کاربر: `{user_id}`\n"
                f"🎯 هدف: {target}\n"
                f"💎 مقدار: `{gift.value}`"
            ),
        )


def register(client):
    client.add_event_handler(
        gift_menu_handler,
        events.NewMessage(incoming=True, func=_gift_command_filter),
    )
    client.add_event_handler(
        gift_code_input_handler,
        events.NewMessage(incoming=True, func=_gift_code_input_filter),
    )


async def _gift_code_input_filter(event) -> bool:
    if event.is_channel or not event.is_private:
        return False
    return (await get_step(event.sender_id)) == "gift_redeem"
