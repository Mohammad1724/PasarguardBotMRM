"""Referral (affiliate) system.

A user's referral link is ``https://t.me/<bot>?start=ref_<user_id>``.
Binding happens on /start; rewards are paid to the referrer's wallet when
the referred user completes a deposit through any payment channel.

No app.telegram imports here except the shared logger sender — keep this
module importable from jobs and CRUD layers without cycles.
"""

from __future__ import annotations

import contextlib

from telethon import Button

from app import Kenzo
from app.db.crud.cryptopayments import get_user_crypto_stats
from app.db.crud.settings import SettingsManager
from app.db.crud.transactions import TransactionCRUD
from app.db.crud.user import UserCRUD, update_Money
from app.logger import LogType, get_logger
from app.telegram.shared.utils.logging import send_log_message

logger = get_logger(__name__)

REFERRAL_START_PREFIX = "ref_"


def parse_referral_start_param(param: str | None) -> int | None:
    """Return referrer id when param looks like ``ref_<id>``, else None."""
    if not param or not param.lower().startswith(REFERRAL_START_PREFIX):
        return None
    raw = param[len(REFERRAL_START_PREFIX):]
    return int(raw) if raw.isdigit() else None


def build_referral_link(bot_username: str, user_id: int) -> str:
    return f"https://t.me/{bot_username}?start={REFERRAL_START_PREFIX}{user_id}"


async def bind_referrer_from_start(user_id: int, param: str | None) -> bool:
    """Bind a referrer for user_id based on a /start deep-link param.

    Safe to call for every /start: rejects self-referral, unknown referrer
    and already-bound users (logic lives in ``UserCRUD.update_ref``).
    """
    referrer_id = parse_referral_start_param(param)
    if not referrer_id:
        return False
    settings = await SettingsManager().get_settings()
    if not settings or not settings.referral_enabled:
        return False
    ok = await UserCRUD().update_ref(user_id, referrer_id)
    if ok:
        logger.info("Referral bound: user=%s referrer=%s", user_id, referrer_id)
        with contextlib.suppress(Exception):
            await send_log_message(
                LogType.OTHER,
                message=(
                    "#زیرمجموعه\n"
                    f"👥 کاربر <code>{user_id}</code> با لینک زیرمجموعه‌گیری عضو شد.\n"
                    f"🔗 معرف: <code>{referrer_id}</code> | "
                    f"<a href='tg://user?id={referrer_id}'>پروفایل معرف</a>"
                ),
                parse_mode="html",
            )
    return bool(ok)


async def _count_prior_deposits(user_id: int) -> int:
    """Count already-completed deposits across all payment channels."""
    tx_count = await TransactionCRUD().count_user_transactions(user_id, status="approved")
    crypto_stats = await get_user_crypto_stats(user_id)
    return int(tx_count) + int(crypto_stats.get("count") or 0)


async def maybe_pay_referral_reward(user_id: int, deposit_amount: int, *, source: str) -> None:
    """Pay referral rewards after a successful deposit credit.

    Call right after the wallet has been credited and the deposit recorded:
    the first-deposit check counts previously recorded deposits, so the very
    first deposit (count == 0 before this one) triggers the fixed bonus.
    """
    try:
        settings = await SettingsManager().get_settings()
        if not settings or not settings.referral_enabled:
            return
        deposit_amount = int(deposit_amount or 0)
        if deposit_amount <= 0:
            return
        min_deposit = int(getattr(settings, "referral_min_deposit", 0) or 0)
        if deposit_amount < min_deposit:
            return

        user = await UserCRUD().read_user(user_id)
        if not user or not user.ref:
            return
        referrer_id = int(user.ref)

        prior = await _count_prior_deposits(user_id)
        # Call sites record the current deposit before invoking us, so the
        # very first successful deposit shows up as a combined count of 1.
        is_first = prior <= 1

        percent = int(getattr(settings, "referral_percent", 0) or 0)
        percent_reward = int(deposit_amount * percent / 100) if percent > 0 else 0
        first_bonus = int(getattr(settings, "referral_first_bonus", 0) or 0) if is_first else 0
        reward = percent_reward + first_bonus
        if reward <= 0:
            return

        referrer = await UserCRUD().read_user(referrer_id)
        if not referrer:
            return

        new_balance = await update_Money(user_id=referrer_id, Money=reward)
        if new_balance is None:
            logger.warning("Referral reward credit failed referrer=%s", referrer_id)
            return

        await TransactionCRUD().create(
            user_id=referrer_id,
            amount=reward,
            method="referral",
            status="approved",
        )

        label = "کارت به کارت" if source == "manual" else source.upper()
        message = (
            "🎁 **پاداش زیرمجموعه‌گیری!**\n\n"
            f"👤 کاربر معرفی‌شده شما یک شارژ موفق انجام داد ({label}).\n"
            f"💵 مبلغ شارژ: `{deposit_amount:,}` تومان\n"
        )
        if percent_reward > 0:
            message += f"📈 سهم شما ({percent}%): `+{percent_reward:,}` تومان\n"
        if first_bonus > 0:
            message += f"🎉 پاداش اولین شارژ: `+{first_bonus:,}` تومان\n"
        message += f"💰 موجودی جدید: `{int(new_balance):,}` تومان"
        with contextlib.suppress(Exception):
            await Kenzo.send_message(
                referrer_id,
                message,
                parse_mode="md",
                buttons=[[Button.inline(f"💳 موجودی: {int(new_balance):,} تومان", data="no_action")]],
            )

        await send_log_message(
            LogType.OTHER,
            message=(
                "#پاداش_زیرمجموعه\n"
                f"👤 معرف: <code>{referrer_id}</code> | <a href='tg://user?id={referrer_id}'>پروفایل</a>\n"
                f"👥 کاربر: <code>{user_id}</code> | <a href='tg://user?id={user_id}'>پروفایل</a>\n"
                f"💵 شارژ: <code>{deposit_amount:,}</code> تومان ({label})\n"
                f"🎁 پاداش: <code>{reward:,}</code> تومان"
            ),
            parse_mode="html",
        )
        logger.info(
            "Referral reward paid referrer=%s user=%s reward=%s (percent=%s first=%s)",
            referrer_id,
            user_id,
            reward,
            percent_reward,
            first_bonus,
        )
    except Exception as exc:
        logger.error("maybe_pay_referral_reward error user=%s: %s", user_id, exc)
