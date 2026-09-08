"""Callback handler to apply days/volume gift codes to a chosen service."""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta

from telethon import events

from app.db.crud.gift_codes import GiftCodeCRUD
from app.db.crud.panels import PanelsManager
from app.db.crud.services import ServiceCRUD
from app.logger import LogType, get_logger
from app.services.billing.renewal import require_panel_userid
from app.telegram.shared.guards.callback_guards import notify_session_expired
from app.telegram.shared.utils.logging import send_log_message
from app.telegram.shared.utils.maintenance import bot_is_offline
from app.telegram.shared.utils.rate_limit import debounce_callback
from app.telegram.state import clear_user, get_data, set_step
from app.telegram.user.gift import texts
from app.utils.formatting.conversions import gigabytes_to_bytes
from app.utils.formatting.dates import Time_Date
from app.utils.formatting.traffic import format_size

logger = get_logger(__name__)


async def _set_latest_use_service(code_id: int, user_id: int, service_code: str) -> None:
    from sqlalchemy import select

    from app.db.base import AsyncSessionLocal as Session
    from app.db.models.gift_codes import GiftCodeUse

    try:
        async with Session() as session, session.begin():
            stmt = (
                select(GiftCodeUse)
                .where(GiftCodeUse.code_id == code_id, GiftCodeUse.user_id == user_id)
                .order_by(GiftCodeUse.id.desc())
                .limit(1)
            )
            use = (await session.execute(stmt)).scalar_one_or_none()
            if use:
                use.service_code = str(service_code)
    except Exception as exc:
        logger.warning("gift set_latest_use_service failed: %s", exc)


def _gift_apply_callback_filter(event: events.CallbackQuery.Event) -> bool:
    data = event.data.decode("utf-8", errors="ignore")
    return data.startswith("giftdays:")


@bot_is_offline
@debounce_callback()
async def gift_apply_callback(event: events.CallbackQuery.Event):
    data = event.data.decode("utf-8", errors="ignore")
    try:
        _, code, service_code = data.split(":")
    except ValueError:
        await notify_session_expired(event)
        return

    pending = await get_data(event.sender_id, "gift_pending")
    if pending != code:
        await notify_session_expired(event)
        return

    gift = await GiftCodeCRUD().get_by_code(code)
    if not gift or gift.type not in ("days", "volume"):
        await event.answer(texts.GIFT_INVALID, alert=True)
        return

    _, serv_msg = await ServiceCRUD().get_service(code=service_code)
    if not serv_msg or int(serv_msg.id or 0) != int(event.sender_id):
        await event.answer(texts.GIFT_INVALID_SERVICE, alert=True)
        return
    panel = await PanelsManager().get_panel_by_code(serv_msg.in_panel)
    if not panel:
        await event.answer(texts.GIFT_INVALID_SERVICE, alert=True)
        return

    # clear the pending flag first to prevent replay
    from app.telegram.state import set_data

    await set_data(event.sender_id, "gift_pending", None)
    await clear_user(event.sender_id)
    await set_step(event.sender_id, "home")

    from pasarguard import PasarguardAPI, UserModify

    try:
        panel_user = await PasarguardAPI(panel.base_url).get_user_by_id(
            user_id=require_panel_userid(serv_msg), token=panel.cookie
        )
        if gift.type == "days":
            now_dt = datetime.now(UTC)
            old_expire = panel_user.expire
            if old_expire is None:
                base = now_dt
            elif isinstance(old_expire, datetime):
                base = old_expire if old_expire.tzinfo else old_expire.replace(tzinfo=UTC)
            else:
                base = datetime.fromtimestamp(int(old_expire), tz=UTC)
            new_time = base + timedelta(days=int(gift.value))
            if new_time < now_dt:
                new_time = now_dt + timedelta(days=int(gift.value))
            await PasarguardAPI(panel.base_url).modify_user_by_id(
                user_id=require_panel_userid(serv_msg),
                user=UserModify(expire=new_time),
                token=panel.cookie,
            )
            expire_label = Time_Date(int(new_time.timestamp()))["jf"]
            await ServiceCRUD().update_service(
                code=serv_msg.code,
                expiration_time=int(new_time.timestamp()),
                warning=0,
                warning_time=0,
                expire_notified=False,
            )
            user_text = texts.GIFT_DAYS_DONE.format(
                code=gift.code,
                username=serv_msg.username,
                value=int(gift.value),
                expire=expire_label,
            )
            target = f"روز +{gift.value} → {serv_msg.username}"
        else:
            new_limit = int(panel_user.data_limit or 0) + gigabytes_to_bytes(float(gift.value))
            await PasarguardAPI(panel.base_url).modify_user_by_id(
                user_id=require_panel_userid(serv_msg),
                user=UserModify(data_limit=new_limit),
                token=panel.cookie,
            )
            await ServiceCRUD().update_service(
                code=serv_msg.code,
                package_size=int(new_limit),
                low_volume_notified=False,
            )
            user_text = texts.GIFT_VOLUME_DONE.format(
                code=gift.code,
                username=serv_msg.username,
                value=int(gift.value),
                total=format_size(new_limit, decimal_places=2),
            )
            target = f"حجم +{gift.value}GB → {serv_msg.username}"
    except Exception as exc:
        logger.error("gift apply failed code=%s service=%s: %s", gift.code, serv_msg.code, exc)
        await event.edit(texts.GIFT_APPLY_FAILED)
        return

    await _set_latest_use_service(int(gift.id), int(event.sender_id), str(serv_msg.code))
    with contextlib.suppress(Exception):
        await event.edit(user_text, parse_mode="md")
    with contextlib.suppress(Exception):
        await send_log_message(
            LogType.OTHER,
            message=(
                "#استفاده_کد_هدیه\n"
                f"🎟 کد: `{gift.code}`\n"
                f"👤 کاربر: `{event.sender_id}`\n"
                f"🎯 هدف: {target}\n"
                f"💎 مقدار: `{gift.value}`"
            ),
        )


def register(client):
    client.add_event_handler(
        gift_apply_callback,
        events.CallbackQuery(func=_gift_apply_callback_filter),
    )
