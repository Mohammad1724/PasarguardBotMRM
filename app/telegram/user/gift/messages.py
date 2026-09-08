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
    REDEEM_OK,
    REDEEM_USER_LIMIT,
    GiftCodeCRUD,
)
from app.db.crud.panels import PanelsManager
from app.db.crud.services import ServiceCRUD
from app.db.crud.settings import SettingsManager
from app.db.crud.transactions import TransactionCRUD
from app.db.crud.user import UserCRUD, update_Money
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

    status, gift = await GiftCodeCRUD().redeem(msg, event.sender_id)
    if status != REDEEM_OK:
        alert = _STATUS_ALERTS.get(status, texts.GIFT_INVALID)
        await event.respond(alert)
        raise events.StopPropagation

    if gift.type == "balance":
        new_balance = await update_Money(user_id=event.sender_id, Money=int(gift.value))
        await TransactionCRUD().create(
            user_id=event.sender_id,
            amount=int(gift.value),
            method="gift",
            status="approved",
        )
        await event.respond(
            texts.GIFT_BALANCE_DONE.format(code=gift.code, value=int(gift.value), balance=int(new_balance or 0)),
            parse_mode="md",
        )
        await _log_redeem(gift, event.sender_id, None, "کیف پول")
        await _finish(event)
        raise events.StopPropagation

    # days / volume — remember pending code and ask the user to pick a service
    services = await ServiceCRUD().get_services_reverse(event.sender_id)
    services = [s for s in services if s.in_panel and not getattr(s, "is_test", False)][:20]
    if not services:
        await event.respond(texts.GIFT_NO_SERVICES)
        # refund the use so the code is not wasted
        await _refund_use(gift, event.sender_id)
        await _finish(event)
        raise events.StopPropagation


    await set_data(event.sender_id, "gift_pending", gift.code)
    buttons = []
    for service in services:
        panel = await PanelsManager().get_panel_by_code(service.in_panel) if service.in_panel else None
        label = f"{service.username} ({panel.remark})" if panel else service.username
        buttons.append(
            [Button.inline(label, f"{GIFT_REDEEM_CALLBACK_PREFIX}{gift.code}:{service.code}")]
        )
    await event.respond(
        "لطفاً کانفیگ موردنظر برای اعمال کد را انتخاب کنید:",
        buttons=buttons,
    )
    await set_step(event.sender_id, "gift_service_pick")
    raise events.StopPropagation


async def _refund_use(gift, user_id: int) -> None:
    from sqlalchemy import delete

    from app.db.base import AsyncSessionLocal as Session
    from app.db.models.gift_codes import GiftCodeUse

    try:
        async with Session() as session, session.begin():
            await session.execute(
                delete(GiftCodeUse).where(
                    GiftCodeUse.code_id == gift.id,
                    GiftCodeUse.user_id == user_id,
                )
            )
    except Exception as exc:
        logger.warning("gift refund_use failed: %s", exc)


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
