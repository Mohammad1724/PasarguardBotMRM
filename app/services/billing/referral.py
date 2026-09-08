"""Referral (affiliate) system.

A user's referral link is ``https://t.me/<bot>?start=ref_<user_id>``.
Binding happens on /start; rewards are paid to the referrer's wallet when
the referred user completes a deposit through any payment channel.

No app.telegram imports here except the shared logger sender — keep this
module importable from jobs and CRUD layers without cycles.
"""

from __future__ import annotations

import contextlib

from app import Kenzo
from app.db.crud.settings import SettingsManager
from app.db.crud.user import UserCRUD
from app.logger import LogType, get_logger
from app.telegram.shared.utils.logging import send_log_message

logger = get_logger(__name__)

REFERRAL_START_PREFIX = "ref_"


def parse_referral_start_param(param: str | None) -> int | None:
    """Return referrer id when param looks like ``ref_<id>``, else None."""
    if not param or not param.lower().startswith(REFERRAL_START_PREFIX):
        return None
    raw = param[len(REFERRAL_START_PREFIX) :]
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
    ok, _reason = await UserCRUD().update_ref(user_id, referrer_id)
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


async def maybe_pay_referral_reward(user_id: int, deposit_amount: int, *, source: str, source_id: int) -> None:
    """Best-effort notification only; wallet credit is atomic with the original deposit."""
    from app.db.base import AsyncSessionLocal
    from app.db.models.payment_safety import ReferralReward

    key = f"{'manual' if source == 'manual' else 'crypto'}:{source_id}"
    async with AsyncSessionLocal() as session:
        reward = await session.get(ReferralReward, key)
        if not reward or int(reward.user_id) != user_id:
            return
        with contextlib.suppress(Exception):
            await Kenzo.send_message(
                reward.referrer_id,
                f"🎁 پاداش معرفی: `{int(reward.amount):,}` تومان به کیف پول شما اضافه شد.\nشناسه شارژ: `{key}`",
                parse_mode="md",
            )
