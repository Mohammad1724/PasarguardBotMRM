"""
ZarinPal Payment Processor

Polls pending online-gateway invoices (cryptopayments.arz = "ZARINPAL"),
expires stale ones and verifies paid ones against the ZarinPal v4 API.
The pending row stores the ZarinPal authority in ``amount`` (String column).
"""

import contextlib
from datetime import UTC, datetime

from telethon import Button

from app import Kenzo
from app.db.crud.cryptopayments import CryptoPaymentsCRUD
from app.db.crud.settings import SettingsManager
from app.logger import LogType, get_logger
from app.services.billing.direct_pay_fulfillment import (
    try_fulfill_after_crypto_credit,
)
from app.services.billing.gateways import zarinpal
from app.services.billing.payment_bonus import calculate_payment_bonus
from app.services.billing.referral import maybe_pay_referral_reward
from app.telegram.shared.utils.logging import send_log_message

from .base import BasePaymentProcessor

logger = get_logger(__name__)


def _format_user_message(payment, settings, bonus, total_amount, new_amount, ref_id: str, sandbox: bool) -> str:
    parts = [
        "🎉 <b>پرداخت آنلاین شما با موفقیت انجام شد!</b>",
        "",
        f"📋 <b>شماره فاکتور:</b> <code>{payment.order_id}</code>",
        f"💵 <b>مبلغ:</b> <code>{int(payment.amount_irt):,}</code> تومان",
    ]
    if bonus > 0:
        parts.extend(
            [
                f"🎁 <b>بونوس:</b> +<code>{int(bonus):,}</code> تومان ({settings.crypto_bonus_percent}%)",
                f"💰 <b>مجموع:</b> <code>{int(total_amount):,}</code> تومان",
            ]
        )
    parts.extend(
        [
            f"🏦 <b>درگاه:</b> زرین‌پال{' (آزمایشی)' if sandbox else ''}",
            f"🧾 <b>کد رهگیری:</b> <code>{ref_id}</code>",
            "",
            f"💳 <b>موجودی جدید:</b> <code>{int(new_amount):,}</code> تومان",
            "",
            f"<code>#zarinpal_{payment.order_id}</code>",
        ]
    )
    return "\n".join(parts)


def _format_admin_log(payment, settings, bonus, total_amount, new_amount, ref_id: str, sandbox: bool) -> str:
    parts = [
        "#فاکتور_زرینپال",
        "<b>✅ فاکتور آنلاین کاربر با موفقیت پرداخت شد</b>",
        "",
        f"<b>👤 شناسه کاربری:</b> <code>{payment.user_id}</code> | "
        f"<a href='tg://user?id={payment.user_id}'>پروفایل کاربر</a>",
        f"<b>📋 شماره فاکتور:</b> <code>{payment.order_id}</code>",
        f"<b>💵 مبلغ فاکتور:</b> <code>{int(payment.amount_irt):,}</code> تومان",
    ]
    if bonus > 0:
        parts.extend(
            [
                f"<b>🎁 بونوس:</b> +<code>{int(bonus):,}</code> تومان ({settings.crypto_bonus_percent}%)",
                f"<b>💰 مجموع:</b> <code>{int(total_amount):,}</code> تومان",
            ]
        )
    parts.extend(
        [
            f"<b>🏦 درگاه:</b> زرین‌پال{' (آزمایشی)' if sandbox else ''}",
            f"<b>🧾 کد رهگیری:</b> <code>{ref_id}</code>",
            f"<b>💳 موجودی جدید کاربر:</b> <code>{int(new_amount):,}</code> تومان",
            "",
            f"<code>#zarinpal_{payment.order_id}</code>",
        ]
    )
    return "\n".join(parts)


async def confirm_zarinpal_payment(payment, settings, ref_id: str) -> bool:
    """Credit the wallet for a verified ZarinPal invoice. Returns True on success."""
    bonus = await calculate_payment_bonus(
        amount=int(payment.amount_irt),
        bonus_enabled=settings.crypto_bonus_enabled,
        bonus_percent=settings.crypto_bonus_percent,
    )
    total_amount = int(payment.amount_irt) + bonus
    approved = await CryptoPaymentsCRUD().approve_and_credit(
        payment.order_id,
        total_amount,
        int(datetime.now(UTC).timestamp()),
        payment_ref=f"zarinpal:{payment.amount}",
        gateway_ref_id=ref_id or None,
    )
    if not approved:
        logger.warning("ZarinPal payment already processed or invalid: order_id=%s", payment.order_id)
        return False
    payment, new_amount = approved

    fulfilled = await try_fulfill_after_crypto_credit(int(payment.order_id))
    await maybe_pay_referral_reward(
        int(payment.user_id), int(payment.amount_irt), source="zarinpal", source_id=int(payment.order_id)
    )
    if not fulfilled:
        user_msg = _format_user_message(
            payment, settings, bonus, total_amount, new_amount, ref_id, settings.zarinpal_sandbox
        )
        with contextlib.suppress(Exception):
            await Kenzo.send_message(
                payment.user_id,
                user_msg,
                parse_mode="html",
                buttons=[
                    [
                        Button.inline(
                            text=f"💳 موجودی: {int(new_amount):,} تومان",
                            data="no_action",
                        )
                    ]
                ],
            )

    admin_log = _format_admin_log(payment, settings, bonus, total_amount, new_amount, ref_id, settings.zarinpal_sandbox)
    await send_log_message(
        LogType.CRYPTO,
        message=admin_log,
        parse_mode="html",
        buttons=[
            [
                Button.inline(
                    text=f"💳 موجودی: {int(new_amount):,} تومان",
                    data="no_action",
                )
            ]
        ],
    )
    return True


class ZarinpalProcessor(BasePaymentProcessor):
    def __init__(self):
        super().__init__("zarinpal")

    async def check_payments(self):
        settings = await SettingsManager().get_settings()
        if not settings:
            return

        crud = CryptoPaymentsCRUD()
        pending_payments = await crud.get_pending_by_arz("ZARINPAL")
        if not pending_payments:
            return

        for payment in pending_payments:
            await crud.schedule_gateway_retry(payment)
            result = await zarinpal.verify_payment(
                (payment.gateway_merchant or settings.zarinpal_merchant),
                int(payment.amount_irt),
                str(payment.amount),
                sandbox=(
                    payment.gateway_sandbox == "1" if payment.gateway_sandbox is not None else settings.zarinpal_sandbox
                ),
            )
            if result.ok:
                try:
                    await confirm_zarinpal_payment(payment, settings, result.ref_id)
                except Exception as exc:
                    logger.error("Error processing ZarinPal payment %s: %s", payment.order_id, exc)
