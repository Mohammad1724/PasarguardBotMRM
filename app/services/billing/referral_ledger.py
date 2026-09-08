"""Runs inside the deposit transaction: never credit a referral in a second transaction."""

import time

from sqlalchemy import func, select

from app.db.models.cryptopayments import CryptoPayments
from app.db.models.payment_safety import ReferralReward
from app.db.models.transaction import Transaction
from app.db.models.user import User


async def credit_referral(session, user, amount: int, source_key: str, settings) -> None:
    if not settings or not settings.referral_enabled or not user.ref or int(user.ref) == int(user.id):
        return
    if amount <= 0 or amount < int(settings.referral_min_deposit or 0):
        return
    if await session.get(ReferralReward, source_key):
        return
    # Deposits serialize on the referred user's wallet row, already locked by caller.
    # Only real deposits count: gifts/referral credits must not suppress the first bonus.
    manual = await session.scalar(
        select(func.count())
        .select_from(Transaction)
        .where(Transaction.user_id == user.id, Transaction.status == "approved", Transaction.method == "manual")
    )
    crypto = await session.scalar(
        select(func.count())
        .select_from(CryptoPayments)
        .where(CryptoPayments.user_id == user.id, CryptoPayments.status == "Paid")
    )
    first_claim = await session.scalar(select(ReferralReward.source_key).where(ReferralReward.first_user_id == user.id))
    first = int(manual or 0) + int(crypto or 0) <= 1 and not first_claim
    reward = amount * max(0, min(100, int(settings.referral_percent or 0))) // 100
    bonus = max(0, int(settings.referral_first_bonus or 0)) if first else 0
    reward += bonus
    if reward <= 0:
        return
    referrer = await session.scalar(select(User).where(User.id == int(user.ref)).with_for_update())
    if not referrer:
        return
    referrer.amount = int(referrer.amount or 0) + reward
    now = int(time.time())
    session.add(
        ReferralReward(
            source_key=source_key,
            user_id=user.id,
            referrer_id=referrer.id,
            amount=reward,
            first_user_id=user.id if bonus else None,
            created_at=now,
        )
    )
    session.add(
        Transaction(
            user_id=referrer.id, amount=reward, method="referral", status="approved", created_at=now, completed_at=now
        )
    )
