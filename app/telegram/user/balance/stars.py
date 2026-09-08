"""Telegram Stars (XTR) payment flow for the balance module.

Creates Star invoices via MTProto (InputMediaInvoice with currency "XTR"),
answers pre-checkout queries and credits the wallet when a
MessageActionPaymentSentMe arrives. Pending orders reuse the cryptopayments
table with arz = "STARS" (amount column stores the star count).
"""

from __future__ import annotations

import contextlib
import os

from telethon import Button, events, functions, types

from app import Kenzo
from app.db.crud.cryptopayments import CryptoPaymentsCRUD, count_pending_orders
from app.db.crud.settings import SettingsManager
from app.logger import LogType, get_logger
from app.services.billing.direct_pay_flow import is_direct_pay_active
from app.services.billing.direct_pay_fulfillment import try_fulfill_after_crypto_credit
from app.services.billing.direct_pay_store import get_pending_for_user, link_crypto_order
from app.services.billing.payment_bonus import calculate_payment_bonus
from app.services.billing.referral import maybe_pay_referral_reward
from app.telegram.keyboards.home import bhome_buttons
from app.telegram.shared.utils.logging import send_log_message
from app.telegram.shared.utils.maintenance import bot_is_offline
from app.telegram.state import clear_user, get_step, set_step
from app.telegram.user.balance import states, texts
from app.telegram.user.balance.messages import (
    respond_deposit_amount_range_error,
    respond_deposit_numeric_error,
)
from app.utils.formatting.dates import Time_Date

logger = get_logger(__name__)

STARS_PAYLOAD_PREFIX = "stars:"


def _parse_stars_payload(payload: bytes | str | None) -> int | None:
    try:
        raw = payload.decode() if isinstance(payload, bytes) else str(payload or "")
    except Exception:
        return None
    if not raw.startswith(STARS_PAYLOAD_PREFIX):
        return None
    order = raw[len(STARS_PAYLOAD_PREFIX) :]
    return int(order) if order.isdigit() else None


async def create_stars_invoice(event, *, amount_irt: int) -> None:
    """Quote the amount in Stars and send a Stars invoice to the user."""
    settings = await SettingsManager().get_settings()
    rate = int(getattr(settings, "stars_rate", 0) or 0)
    if not settings or not getattr(settings, "stars_mode", False) or rate <= 0:
        await event.respond(texts.STARS_RATE_UNSET)
        await set_step(event.sender_id, states.STEP_HOME)
        return

    amount = int(amount_irt)
    if amount < settings.stars_deposit_min or amount > settings.stars_deposit_max:
        await respond_deposit_amount_range_error(
            event,
            text_key="stars_amount_range_error",
            default=texts.STARS_AMOUNT_RANGE_ERROR_DEFAULT,
            min_amount=settings.stars_deposit_min,
            max_amount=settings.stars_deposit_max,
        )
        return
    await CryptoPaymentsCRUD().age_invoices()
    if await count_pending_orders(event.sender_id) >= 3:
        await event.respond(
            texts.PENDING_ORDERS_LIMIT,
            buttons=await bhome_buttons(event.sender_id, "fa"),
        )
        await set_step(event.sender_id, states.STEP_HOME)
        return

    stars = max(1, (amount + rate - 1) // rate)
    crud = CryptoPaymentsCRUD()
    await crud.age_invoices()
    try:
        payment = await crud.reserve_invoice(event.sender_id, "STARS", str(stars), amount)
    except ValueError, RuntimeError:
        await event.respond(texts.PENDING_ORDERS_LIMIT)
        return
    order = payment.order_id
    if await is_direct_pay_active(event.sender_id) or await get_pending_for_user(event.sender_id):
        await link_crypto_order(int(event.sender_id), int(order))
    description = texts.STARS_INVOICE_DESCRIPTION.format(amount=f"{amount:,}", stars=stars)
    media = types.InputMediaInvoice(
        title=texts.STARS_INVOICE_TITLE,
        description=description,
        invoice=types.Invoice(
            currency="XTR",
            prices=[types.LabeledPrice(label=texts.STARS_INVOICE_TITLE, amount=stars)],
        ),
        payload=f"{STARS_PAYLOAD_PREFIX}{order}".encode(),
        provider="",
        provider_data=types.DataJSON(data="{}"),
    )
    try:
        await Kenzo(
            functions.messages.SendMediaRequest(
                peer=event.sender_id,
                media=media,
                message="",
                random_id=int.from_bytes(os.urandom(8), "big", signed=True),
            )
        )
    except Exception as exc:
        logger.error("Failed to send Stars invoice for order %s: %s", order, exc)
        await event.respond(
            texts.ZARINPAL_REQUEST_FAILED,
            buttons=await bhome_buttons(event.sender_id, "fa"),
        )
        await set_step(event.sender_id, states.STEP_HOME)
        return

    await event.respond(
        texts.STARS_SENT_TEMPLATE.format(order=order, amount=amount, stars=stars, rate=rate),
        parse_mode="html",
        buttons=await bhome_buttons(event.sender_id, "fa"),
    )
    await clear_user(event.sender_id)

    log_text = (
        "#فاکتور_جدید_استارز\n"
        f"👤 شناسه کاربر: <code>{event.sender_id}</code> | "
        f"<a href='tg://user?id={event.sender_id}'>پروفایل کاربر</a>\n"
        f"💡 شماره فاکتور: <code>{order}</code>\n"
        f"💵 مبلغ فاکتور: <code>{amount:,}</code> تومان\n"
        f"⭐ معادل: <code>{stars}</code> ستاره (نرخ: <code>{rate:,}</code> تومان)"
    )
    await send_log_message(LogType.CRYPTO, message=log_text, parse_mode="html")
    await set_step(event.sender_id, states.STEP_HOME)


@bot_is_offline
async def stars_payment_handler(event):
    msg = event.message.message
    if msg.isdigit():
        await create_stars_invoice(event, amount_irt=int(msg))
        raise events.StopPropagation
    await respond_deposit_numeric_error(
        event,
        text_key="stars_numeric_error",
        default=texts.STARS_NUMERIC_ERROR_DEFAULT,
    )
    raise events.StopPropagation


async def stars_precheckout_handler(event: types.UpdateBotPrecheckoutQuery):
    """Answer Telegram's pre-checkout query for Stars invoices."""
    order_id = _parse_stars_payload(event.payload)
    valid = False
    try:
        if order_id is not None:
            valid = await CryptoPaymentsCRUD().accept_stars_checkout(
                order_id, int(event.user_id), int(event.total_amount), event.currency, query_id=int(event.query_id)
            )
    except Exception:
        logger.exception("Stars checkout validation failed")
    try:
        await Kenzo(
            functions.messages.SetBotPrecheckoutResultsRequest(
                query_id=event.query_id,
                success=valid,
                error=None if valid else "فاکتور نامعتبر یا منقضی شده است",
            )
        )
    except Exception as exc:
        logger.error("Failed to answer pre-checkout for order %s: %s", order_id, exc)


async def confirm_stars_payment(order_id: int, stars_paid: int, *, payer_id: int, charge_id: str) -> bool:
    settings = await SettingsManager().get_settings()
    crud = CryptoPaymentsCRUD()
    payment = await crud.get_by_order_id(order_id)
    if (
        not payment
        or (payment.arz or "").upper() != "STARS"
        or payment.status not in ("Pending", "Processing", "Expired")
    ):
        logger.warning("Stars payment for unknown/closed order %s", order_id)
        return False
    try:
        stored_stars = int(payment.amount)
    except TypeError, ValueError:
        stored_stars = -1
    if stars_paid != stored_stars or payment.user_id != payer_id or not charge_id or len(charge_id) > 240:
        logger.error(
            "Stars amount mismatch order=%s paid=%s stored=%s — skipping auto credit",
            order_id,
            stars_paid,
            stored_stars,
        )
        return False

    bonus = await calculate_payment_bonus(
        amount=int(payment.amount_irt),
        bonus_enabled=settings.crypto_bonus_enabled,
        bonus_percent=settings.crypto_bonus_percent,
    )
    total_amount = int(payment.amount_irt) + bonus
    approved = await crud.approve_and_credit(
        order_id, total_amount, int(Time_Date()["stamp"]), payment_ref=f"stars:{charge_id}", user_id=payer_id
    )
    if not approved:
        logger.warning("Stars payment already processed: order_id=%s", order_id)
        return False
    payment, new_amount = approved

    fulfilled = await try_fulfill_after_crypto_credit(int(order_id))
    await maybe_pay_referral_reward(
        int(payment.user_id), int(payment.amount_irt), source="stars", source_id=int(order_id)
    )
    if not fulfilled:
        user_msg = texts.STARS_PAYMENT_SUCCESS.format(
            order=order_id,
            stars=stars_paid,
            amount=int(payment.amount_irt),
            balance=int(new_amount),
        )
        with contextlib.suppress(Exception):
            await Kenzo.send_message(
                payment.user_id,
                user_msg,
                parse_mode="md",
                buttons=[[Button.inline(f"💳 موجودی: {int(new_amount):,} تومان", data="no_action")]],
            )

    await send_log_message(
        LogType.CRYPTO,
        message=(
            "#پرداخت_استارز\n"
            "<b>✅ فاکتور ستاره‌ای کاربر با موفقیت پرداخت شد</b>\n\n"
            f"<b>👤 شناسه کاربری:</b> <code>{payment.user_id}</code> | "
            f"<a href='tg://user?id={payment.user_id}'>پروفایل کاربر</a>\n"
            f"<b>📋 شماره فاکتور:</b> <code>{order_id}</code>\n"
            f"<b>⭐ ستاره‌ها:</b> <code>{stars_paid}</code>\n"
            f"<b>💵 مبلغ:</b> <code>{int(payment.amount_irt):,}</code> تومان\n"
            f"<b>💳 موجودی جدید:</b> <code>{int(new_amount):,}</code> تومان"
        ),
        parse_mode="html",
    )
    return True


async def stars_payment_sent_handler(event):
    """Handle MessageActionPaymentSentMe (successful Stars payment)."""
    message = getattr(event, "message", None)
    if not isinstance(message, types.MessageService) or getattr(message, "out", False):
        return
    peer = message.from_id or message.peer_id
    if not isinstance(peer, types.PeerUser):
        return
    action = getattr(message, "action", None)
    if not isinstance(action, types.MessageActionPaymentSentMe):
        return
    if (action.currency or "").upper() != "XTR":
        return
    order_id = _parse_stars_payload(action.payload)
    if order_id is None:
        return
    try:
        await confirm_stars_payment(
            order_id, int(action.total_amount), payer_id=int(peer.user_id), charge_id=action.charge.id
        )
    except Exception as exc:
        logger.error("Error processing Stars payment %s: %s", order_id, exc)
    raise events.StopPropagation


def register(client):
    client.add_event_handler(
        stars_payment_handler,
        events.NewMessage(incoming=True, func=_stars_message_filter),
    )
    client.add_event_handler(
        stars_payment_sent_handler,
        events.Raw(types.UpdateNewMessage),
    )
    client.add_event_handler(
        stars_precheckout_handler,
        events.Raw(types.UpdateBotPrecheckoutQuery),
    )


async def _stars_message_filter(event):
    if event.is_channel or not event.is_private:
        return False
    if (await get_step(event.sender_id)) != states.STEP_STARS_2:
        return False
    msg = event.message.message
    if not msg:
        return False
    return not msg.lower().startswith(("/start", "/panel")) and msg not in states.LEGACY_CANCEL_MESSAGES


def _stars_action_filter(event):
    return isinstance(getattr(event.message, "action", None), types.MessageActionPaymentSentMe)
