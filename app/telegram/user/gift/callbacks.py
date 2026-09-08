"""Apply or reconcile a gift using a compact request token, not an unbounded code."""

from telethon import events

from app.db.crud.gift_codes import GiftCodeCRUD
from app.db.crud.panels import PanelsManager
from app.db.crud.services import ServiceCRUD
from app.db.crud.settings import SettingsManager
from app.logger import get_logger
from app.services.gifts import apply_service_gift
from app.telegram.shared.guards.callback_guards import notify_session_expired
from app.telegram.shared.utils.maintenance import bot_is_offline
from app.telegram.shared.utils.rate_limit import debounce_callback
from app.telegram.state import clear_user, get_data, set_step
from app.telegram.user.gift import texts

logger = get_logger(__name__)


def _gift_apply_callback_filter(event):
    return event.data.startswith(b"giftapply:")


@bot_is_offline
@debounce_callback()
async def gift_apply_callback(event):
    try:
        _, request_id, service_code = event.data.decode().split(":")
        if len(request_id) != 32 or not service_code.isascii() or not service_code.isdigit():
            raise ValueError("invalid payload")
    except ValueError, UnicodeError:
        await notify_session_expired(event)
        return
    pending = await get_data(event.sender_id, "gift_pending")
    if not isinstance(pending, dict) or pending.get("request_id") != request_id:
        await notify_session_expired(event)
        return
    settings = await SettingsManager().get_settings()
    if not settings or not settings.gift_mode:
        await event.answer(texts.GIFT_INACTIVE, alert=True)
        return
    gift = await GiftCodeCRUD().get_by_code(pending["code"])
    found, service = await ServiceCRUD().get_service(code=service_code)
    if (
        not gift
        or gift.type not in ("days", "volume")
        or not found
        or int(service.id or 0) != event.sender_id
        or service.is_test
    ):
        await event.answer(texts.GIFT_INVALID_SERVICE, alert=True)
        return
    panel = await PanelsManager().get_panel_by_code(service.in_panel)
    if not panel:
        await event.answer(texts.GIFT_INVALID_SERVICE, alert=True)
        return
    await event.answer()
    try:
        await apply_service_gift(gift, service, panel, event.sender_id, request_id)
    except ValueError as exc:
        await event.respond(str(exc))
        return
    except Exception:
        logger.exception("Gift request %s requires retry/reconciliation", request_id)
        await event.respond(
            "❌ اعمال هدیه تکمیل نشد. همین دکمه را دوباره بزنید؛ در صورت تکرار خطا، این شناسه را به پشتیبانی بدهید: "
            + request_id
        )
        return
    await clear_user(event.sender_id)
    await set_step(event.sender_id, "home")
    await event.edit(f"✅ هدیه {gift.code} برای {service.username} اعمال شد.")


def register(client):
    client.add_event_handler(gift_apply_callback, events.CallbackQuery(func=_gift_apply_callback_filter))
