import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from app.db.base import AsyncSessionLocal, engine
from app.db.crud.cryptopayments import CryptoPaymentsCRUD
from app.db.crud.gift_codes import GiftCodeCRUD
from app.db.crud.transactions import TransactionCRUD
from app.db.crud.user import UserCRUD
from app.db.models.cryptopayments import CryptoPayments
from app.db.models.gift_codes import GiftCode, GiftCodeUse
from app.db.models.payment_safety import ReferralReward
from app.db.models.transaction import Transaction
from app.services.gifts import cancel_unapplied_use, make_plan, reserve_gift, validate_code


@pytest.mark.parametrize("code", ["X:Y", "A" * 25, "کدهدیه", "", "hello world"])
def test_gift_code_validation(code):
    with pytest.raises(ValueError):
        validate_code(code)


@pytest.mark.parametrize("kind", ["days", "volume"])
def test_unlimited_service_not_limited(kind):
    with pytest.raises(ValueError, match="نامحدود"):
        make_plan(SimpleNamespace(type=kind, value=10), SimpleNamespace(expire=None, data_limit=0))


async def test_wallet_gift_atomic_and_idempotent(users):
    await GiftCodeCRUD().create("GIFT", "balance", 500)
    first, balance = await reserve_gift("GIFT", 2, "a" * 32)
    again, repeated_balance = await reserve_gift("GIFT", 2, "a" * 32)
    assert first.id == again.id
    assert balance == repeated_balance == 500
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(Transaction)) == 1
        assert (await session.scalar(select(GiftCode))).times_used == 1


async def test_invalid_gift_does_not_credit(users):
    await GiftCodeCRUD().create("EXPIRED", "balance", 500, expires_at=int(time.time()) - 1)
    with pytest.raises(ValueError):
        await reserve_gift("EXPIRED", 2, "b" * 32)
    assert (await UserCRUD().read_user(2)).amount == 0


async def test_cancel_releases_exact_reservation_and_quota(users):
    await GiftCodeCRUD().create("DAYS", "days", 10, max_uses=2, per_user_limit=2)
    await reserve_gift("DAYS", 2, "a" * 32, "99", {"field": "expire", "old": 1, "new": 2})
    assert await cancel_unapplied_use("a" * 32, 2)
    assert not await cancel_unapplied_use("a" * 32, 2)
    async with AsyncSessionLocal() as session:
        assert (await session.scalar(select(GiftCode))).times_used == 0
        assert await session.scalar(select(func.count()).select_from(GiftCodeUse)) == 1
    await reserve_gift("DAYS", 2, "c" * 32, "99", {"field": "expire", "old": 3, "new": 4})


async def test_payment_and_referral_exactly_once(users):
    crud = CryptoPaymentsCRUD()
    payment = await crud.reserve_invoice(2, "STARS", "10", 1000)
    assert await crud.approve_and_credit(payment.order_id, 1000, payment_ref="stars:unique", user_id=2)
    assert not await crud.approve_and_credit(payment.order_id, 1000, payment_ref="stars:unique", user_id=2)
    assert (await UserCRUD().read_user(2)).amount == 1000
    assert (await UserCRUD().read_user(1)).amount == 200  # 10% + first bonus
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(ReferralReward)) == 1


async def test_charge_reference_cannot_credit_another_order(users):
    crud = CryptoPaymentsCRUD()
    first = await crud.reserve_invoice(2, "STARS", "10", 1000)
    second = await crud.reserve_invoice(2, "STARS", "10", 1000)
    assert await crud.approve_and_credit(first.order_id, 1000, payment_ref="stars:unique", user_id=2)
    assert not await crud.approve_and_credit(second.order_id, 1000, payment_ref="stars:unique", user_id=2)
    assert (await UserCRUD().read_user(2)).amount == 1000
    assert (await crud.get_by_order_id(second.order_id)).status == "Pending"


async def test_gifts_do_not_count_as_first_deposit(users):
    await GiftCodeCRUD().create("GIFT", "balance", 500)
    await reserve_gift("GIFT", 2, "a" * 32)
    tx = await TransactionCRUD().create(2, 1000, "manual")
    assert await TransactionCRUD().approve_manual(tx)
    assert (await UserCRUD().read_user(1)).amount == 200
    second = await TransactionCRUD().create(2, 1000, "manual")
    assert await TransactionCRUD().approve_manual(second)
    assert (await UserCRUD().read_user(1)).amount == 300


async def test_checkout_validates_amount_currency_and_owner(users):
    crud = CryptoPaymentsCRUD()
    row = await crud.reserve_invoice(2, "STARS", "10", 1000)
    assert not await crud.accept_stars_checkout(row.order_id, 3, 10, "XTR")
    assert not await crud.accept_stars_checkout(row.order_id, 2, 9, "XTR")
    assert not await crud.accept_stars_checkout(row.order_id, 2, 10, "USD")
    assert await crud.accept_stars_checkout(row.order_id, 2, 10, "XTR")
    assert (await crud.get_by_order_id(row.order_id)).status == "Processing"


async def test_late_payment_after_local_expiry_is_credited(users):
    crud = CryptoPaymentsCRUD()
    row = await crud.reserve_invoice(2, "STARS", "10", 1000)
    async with AsyncSessionLocal() as session, session.begin():
        stored = await session.get(CryptoPayments, row.order_id)
        stored.createtime = int(time.time()) - 3600
    await crud.age_invoices()
    assert (await crud.get_by_order_id(row.order_id)).status == "Expired"
    assert await crud.approve_and_credit(row.order_id, 1000, payment_ref="stars:late", user_id=2)
    assert not await crud.expire_payment(row.order_id)
    assert (await crud.get_by_order_id(row.order_id)).status == "Paid"


async def test_gateway_configuration_is_snapshotted(users):
    crud = CryptoPaymentsCRUD()
    row = await crud.reserve_invoice(2, "ZARINPAL", "", 1000, merchant="merchant-A", sandbox=True)
    assert row.status == "Creating"
    await crud.set_gateway_authority(row.order_id, "authority-1")
    stored = await crud.get_by_order_id(row.order_id)
    assert stored.amount == "authority-1" and stored.gateway_merchant == "merchant-A" and stored.gateway_sandbox == "1"


async def test_order_limit(users):
    crud = CryptoPaymentsCRUD()
    for _ in range(3):
        await crud.reserve_invoice(2, "STARS", "10", 1000)
    with pytest.raises(ValueError, match="pending"):
        await crud.reserve_invoice(2, "STARS", "10", 1000)


async def test_reward_failure_rolls_back_deposit(users, monkeypatch):
    from app.services.billing import referral_ledger

    monkeypatch.setattr(referral_ledger, "credit_referral", AsyncMock(side_effect=RuntimeError("synthetic")))
    crud = CryptoPaymentsCRUD()
    row = await crud.reserve_invoice(2, "STARS", "10", 1000)
    with pytest.raises(RuntimeError):
        await crud.approve_and_credit(row.order_id, 1000, payment_ref="stars:rollback", user_id=2)
    assert (await crud.get_by_order_id(row.order_id)).status == "Pending"
    assert (await UserCRUD().read_user(2)).amount == 0


@pytest.mark.skipif(engine.dialect.name == "sqlite", reason="Row-lock concurrency requires MariaDB/PostgreSQL")
async def test_concurrent_duplicate_payment(users):
    crud = CryptoPaymentsCRUD()
    row = await crud.reserve_invoice(2, "STARS", "10", 1000)
    results = await asyncio.gather(
        *[crud.approve_and_credit(row.order_id, 1000, payment_ref="stars:race", user_id=2) for _ in range(5)]
    )
    assert sum(bool(x) for x in results) == 1
    assert (await UserCRUD().read_user(1)).amount == 200


@pytest.mark.skipif(engine.dialect.name == "sqlite", reason="Row-lock concurrency requires MariaDB/PostgreSQL")
async def test_concurrent_gift_limit(users):
    await GiftCodeCRUD().create("GIFT", "balance", 500)
    results = await asyncio.gather(
        reserve_gift("GIFT", 2, "a" * 32), reserve_gift("GIFT", 3, "b" * 32), return_exceptions=True
    )
    assert sum(not isinstance(x, BaseException) for x in results) == 1
    async with AsyncSessionLocal() as session:
        assert (await session.scalar(select(GiftCode))).times_used == 1


async def test_distinct_checkout_query_cannot_charge_same_order_twice(users):
    crud = CryptoPaymentsCRUD()
    row = await crud.reserve_invoice(2, "STARS", "10", 1000)
    assert await crud.accept_stars_checkout(row.order_id, 2, 10, "XTR", query_id=111)
    assert await crud.accept_stars_checkout(row.order_id, 2, 10, "XTR", query_id=111)
    assert not await crud.accept_stars_checkout(row.order_id, 2, 10, "XTR", query_id=222)


@pytest.mark.skipif(engine.dialect.name == "sqlite", reason="Row-lock concurrency requires MariaDB/PostgreSQL")
async def test_concurrent_distinct_deposits_first_bonus_once(users):
    crud = CryptoPaymentsCRUD()
    rows = [await crud.reserve_invoice(2, "STARS", "10", 1000) for _ in range(2)]
    results = await asyncio.gather(
        *[crud.approve_and_credit(row.order_id, 1000, payment_ref=f"stars:{row.order_id}", user_id=2) for row in rows]
    )
    assert all(results)
    assert (await UserCRUD().read_user(2)).amount == 2000
    assert (await UserCRUD().read_user(1)).amount == 300
