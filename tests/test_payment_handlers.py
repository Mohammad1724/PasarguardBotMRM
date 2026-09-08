from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telethon import events, types

from app.db.crud.cryptopayments import CryptoPaymentsCRUD
from app.db.crud.settings import SettingsManager
from app.db.crud.user import UserCRUD
from app.jobs.payments import zarinpal
from app.services.billing import referral
from app.telegram.user.balance import stars


async def test_raw_stars_service_message_dispatch(monkeypatch):
    confirm = AsyncMock(return_value=True)
    monkeypatch.setattr(stars, "confirm_stars_payment", confirm)
    action = types.MessageActionPaymentSentMe(
        currency="XTR",
        total_amount=10,
        payload=b"stars:55555",
        charge=types.PaymentCharge(id="charge", provider_charge_id=""),
    )
    message = types.MessageService(id=1, peer_id=types.PeerUser(2), date=datetime.now(UTC), action=action)
    update = types.UpdateNewMessage(message=message, pts=1, pts_count=1)
    with pytest.raises(events.StopPropagation):
        await stars.stars_payment_sent_handler(update)
    confirm.assert_awaited_once_with(55555, 10, payer_id=2, charge_id="charge")
    client = SimpleNamespace(add_event_handler=lambda handler, builder: registrations.append((handler, builder)))
    registrations = []
    stars.register(client)
    assert any(
        handler is stars.stars_payment_sent_handler and isinstance(builder, events.Raw)
        for handler, builder in registrations
    )


async def test_stars_referral_even_when_direct_purchase_succeeds(users, monkeypatch):
    row = await CryptoPaymentsCRUD().reserve_invoice(2, "STARS", "10", 1000)
    monkeypatch.setattr(stars, "try_fulfill_after_crypto_credit", AsyncMock(return_value=True))
    monkeypatch.setattr(stars, "maybe_pay_referral_reward", AsyncMock())
    monkeypatch.setattr(stars, "send_log_message", AsyncMock())
    assert await stars.confirm_stars_payment(row.order_id, 10, payer_id=2, charge_id="paid-1")
    assert (await UserCRUD().read_user(1)).amount == 200
    stars.maybe_pay_referral_reward.assert_awaited_once()


async def test_old_zarinpal_invoice_still_verified(users, monkeypatch):
    crud = CryptoPaymentsCRUD()
    row = await crud.reserve_invoice(2, "ZARINPAL", "", 1000, merchant="OLD", sandbox=True)
    await crud.set_gateway_authority(row.order_id, "AUTHORITY")
    await crud.update_payment_status(row.order_id, "Expired")
    verify = AsyncMock(return_value=SimpleNamespace(ok=True, ref_id="BANK-REF"))
    monkeypatch.setattr(zarinpal.zarinpal, "verify_payment", verify)
    monkeypatch.setattr(zarinpal, "try_fulfill_after_crypto_credit", AsyncMock(return_value=True))
    monkeypatch.setattr(zarinpal, "maybe_pay_referral_reward", AsyncMock())
    monkeypatch.setattr(zarinpal, "send_log_message", AsyncMock())
    await zarinpal.ZarinpalProcessor().check_payments()
    verify.assert_awaited_once_with("OLD", 1000, "AUTHORITY", sandbox=True)
    paid = await crud.get_by_order_id(row.order_id)
    assert paid.status == "Paid"
    assert paid.gateway_ref_id == "BANK-REF"
    assert (await UserCRUD().read_user(1)).amount == 200


async def test_referrer_failed_binding_is_false(users):
    assert not await referral.bind_referrer_from_start(2, "ref_2")
    assert not await referral.bind_referrer_from_start(2, "ref_3")
    assert not await referral.bind_referrer_from_start(3, "ref_999")


async def test_stars_persists_and_links_before_send(users, monkeypatch):
    from app.telegram.user.balance import states

    await SettingsManager().update_setting_by_name("stars_mode", True)
    await SettingsManager().update_setting_by_name("stars_deposit_min", 1)
    recorded = []

    async def telegram_call(request):
        if isinstance(request, stars.functions.messages.SendMediaRequest):
            order = stars._parse_stars_payload(request.media.payload)
            row = await CryptoPaymentsCRUD().get_by_order_id(order)
            assert row and row.status == "Pending"
            assert recorded == [order]

    monkeypatch.setattr(stars, "Kenzo", telegram_call)
    monkeypatch.setattr(stars, "is_direct_pay_active", AsyncMock(return_value=True))

    async def link(user, order):
        recorded.append(order)

    monkeypatch.setattr(stars, "link_crypto_order", link)
    monkeypatch.setattr(stars, "clear_user", AsyncMock())
    monkeypatch.setattr(stars, "set_step", AsyncMock())
    monkeypatch.setattr(stars, "bhome_buttons", AsyncMock(return_value=[]))
    monkeypatch.setattr(stars, "send_log_message", AsyncMock())
    event = SimpleNamespace(sender_id=2, respond=AsyncMock())
    await stars.create_stars_invoice(event, amount_irt=1000)
    assert len(recorded) == 1
    stars.set_step.assert_awaited_with(2, states.STEP_HOME)


async def test_stars_history_recovers_missed_payment(users, monkeypatch):
    from app.jobs.payments import stars as history

    row = await CryptoPaymentsCRUD().reserve_invoice(2, "STARS", "10", 1000)
    await CryptoPaymentsCRUD().expire_payment(row.order_id)
    tx = SimpleNamespace(
        refund=False,
        pending=False,
        failed=False,
        bot_payload=f"stars:{row.order_id}".encode(),
        peer=types.StarsTransactionPeer(types.PeerUser(2)),
        amount=types.StarsAmount(10, 0),
        id="history-charge",
    )
    monkeypatch.setattr(history, "Kenzo", AsyncMock(return_value=SimpleNamespace(history=[tx], next_offset=None)))
    monkeypatch.setattr(stars, "try_fulfill_after_crypto_credit", AsyncMock(return_value=True))
    monkeypatch.setattr(stars, "maybe_pay_referral_reward", AsyncMock())
    monkeypatch.setattr(stars, "send_log_message", AsyncMock())
    await history.reconcile_stars()
    assert (await UserCRUD().read_user(2)).amount == 1000
    assert (await CryptoPaymentsCRUD().get_by_order_id(row.order_id)).status == "Paid"
    await history.reconcile_stars()
    assert (await UserCRUD().read_user(2)).amount == 1000
