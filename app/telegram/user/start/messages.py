"""Message handlers for user start."""

from __future__ import annotations

from telethon import events
from telethon.tl.custom import Message

from app import Kenzo
from app.db.crud.user import UserCRUD, add_user, clear_reactivatable_status
from app.logger import get_logger
from app.services.billing.referral import bind_referrer_from_start, parse_referral_start_param
from app.telegram.keyboards.home import bhome_buttons
from app.telegram.shared.guards.channel_gate import (
    extract_start_param,
    get_not_joined_channels,
)
from app.telegram.shared.start_params import is_documented_start_param
from app.telegram.shared.utils.help_download import download_app_file
from app.telegram.shared.utils.maintenance import bot_is_offline
from app.telegram.state import clear_user, get_step, set_step
from app.telegram.user.start import helpers
from app.utils.formatting.dates import Time_Date

logger = get_logger(__name__)


@bot_is_offline
async def start_command_handler(event: Message):
    param = extract_start_param(event)
    cleared_status = await clear_reactivatable_status(event.sender_id)
    if cleared_status:
        logger.info("User %s returned — cleared DB status %s", event.sender_id, cleared_status)
    lang = await helpers.get_user_lang(event.sender_id)
    info = await UserCRUD().read_user(event.sender_id)

    logger.info("handling /start with param=%s", param)
    existing_user = info

    not_joined_channels = await get_not_joined_channels(event.sender_id)
    if not_joined_channels:
        await helpers.prompt_channel_join(event, lang)
        raise events.StopPropagation

    if not existing_user:
        await add_user(
            user_id=event.sender_id,
            step="start",
            time_s=Time_Date()["stamp"],
            language=lang,
        )

    await clear_user(event.sender_id)
    await set_step(event.sender_id, "start")
    welcome_text = await helpers.fetch_welcome_text()

    app_key = helpers.resolve_app_download_param(param)
    if app_key:
        await download_app_file(event, app_key)
    else:
        if helpers.parse_discount_start_param(param):
            await helpers.handle_discount_start_param(event.sender_id, param)
        elif parse_referral_start_param(param):
            await bind_referrer_from_start(event.sender_id, param)
        elif param and not is_documented_start_param(param):
            logger.info("Unknown start param %r — showing welcome menu", param)
        await helpers.send_welcome_menu(event, welcome_text, lang)

    raise events.StopPropagation


async def _home_back_filter(event: Message) -> bool:
    if event.is_channel or not event.is_private:
        return False
    if await get_step(event.sender_id) == "ban":
        return False
    return (event.message.text or "").strip() == "🏠 بازگشت"


@bot_is_offline
async def home_back_handler(event: Message):
    lang = await helpers.get_user_lang(event.sender_id)
    await set_step(user_id=event.sender_id, step="home")
    await Kenzo.send_message(
        entity=event.sender_id,
        message="**شما برگشتید به صفحه اصلی**",
        buttons=await bhome_buttons(event.sender_id, lang),
    )
    raise events.StopPropagation


def register(client):
    client.add_event_handler(
        start_command_handler,
        events.NewMessage(incoming=True, func=helpers.start_command_filter),
    )
    client.add_event_handler(
        home_back_handler,
        events.NewMessage(incoming=True, func=_home_back_filter),
    )
