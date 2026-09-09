"""V1 product regressions. Panel and Telegram calls are fake; money and tickets use the test DB."""

import asyncio
import os
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import func, select
from telethon import events

from app.db.base import AsyncSessionLocal as Session, engine
from app.db.crud.services import ServiceCRUD
from app.db.crud.settings import SettingsManager
from app.db.models.customer_experience import CustomerEvent, SupportTicket, TicketMessage, TrialConversion, TrialJourney
from app.db.models.panels import Panels
from app.db.models.plans import Plan
from app.db.models.services import Service
from app.db.models.user import User
from app.services.customer_experience import conversion, journeys, lifecycle, tickets
from app.services.customer_experience.common import change_setting


@asynccontextmanager
async def fake_lock(*args, **kwargs):
    yield


@pytest.fixture
async def cx(users, monkeypatch):
    config = await SettingsManager().get_settings()
    await SettingsManager().update_setting(
        config.id,
        cx_tickets_enabled=True,
        cx_onboarding_enabled=True,
        cx_conversion_enabled=True,
        cx_followup_enabled=True,
        sale_mode=True,
        cx_support_ids=[3],
    )
    now = int(time.time())
    async with Session() as session, session.begin():
        (await session.get(User, 2)).amount = 10000
        session.add(
            Panels(
                code=10,
                name="Synthetic",
                base_url="https://panel.invalid",
                username="test",
                password="test-only",
                cookie="synthetic",
                button_settings={},
                subscription_settings={},
                test_settings={},
                renewal_settings={},
                feature_settings={},
            )
        )
        session.add_all(
            [
                Plan(
                    id=11,
                    price=1000,
                    storage=10,
                    duration=30,
                    panel_code=10,
                    plan_type="volume",
                    data_limit_reset_strategy="no_reset",
                    ip_limit=2,
                ),
                Plan(
                    id=12,
                    price=2000,
                    storage=20,
                    duration=30,
                    panel_code=99,
                    plan_type="volume",
                    data_limit_reset_strategy="no_reset",
                    ip_limit=0,
                ),
            ]
        )
    ok, message = await ServiceCRUD().create_service(
        code=100,
        username="test_100",
        id=2,
        panel_userid=222,
        in_panel=10,
        package_size=1024**3,
        expiration_time=now + 86400,
        createtime=now,
        is_test=True,
        enable=True,
    )
    assert ok, message
    current = SimpleNamespace(
        id=222,
        username="test_100",
        expire=now + 86400,
        data_limit=1024**3,
        used_traffic=512 * 1024**2,
        hwid_limit=0,
        data_limit_reset_strategy="no_reset",
        status="active",
        subscription_url="https://panel.invalid/sub/secret-test-link",
        next_plan=None,
    )
    writes = []

    async def call(panel, method, **kwargs):
        if method == "get_user_by_id":
            return current
        if method == "modify_user_by_id":
            values = kwargs["user"].model_dump(exclude_none=True)
            writes.append(values)
            for key, value in values.items():
                setattr(current, key, value)
            return current
        if method == "remove_user_by_id":
            writes.append("delete")
            return None
        raise AssertionError(method)

    monkeypatch.setattr(conversion, "distributed_lock", fake_lock)
    monkeypatch.setattr(lifecycle, "distributed_lock", fake_lock)
    monkeypatch.setattr(conversion, "panel_call", call)
    monkeypatch.setattr(lifecycle, "panel_call", call)
    monkeypatch.setattr(lifecycle, "notify_deletion", AsyncMock())
    return SimpleNamespace(current=current, writes=writes, call=call, now=now)


async def balance(user=2):
    async with Session() as session:
        return (await session.get(User, user)).amount


async def test_new_features_default_off(users):
    config = await SettingsManager().get_settings()
    assert not any(
        (
            config.cx_tickets_enabled,
            config.cx_conversion_enabled,
            config.cx_onboarding_enabled,
            config.cx_followup_enabled,
        )
    )
    assert config.cx_support_ids == []


async def test_ticket_first_message_is_atomic_and_replay_safe(cx):
    row, msg, created = await tickets.add_message(
        2, "<b>literal text</b>", service_code=100, topic="connection", source_chat_id=2, source_message_id=10
    )
    same, same_msg, repeated = await tickets.add_message(
        2, "<b>literal text</b>", service_code=100, source_chat_id=2, source_message_id=10
    )
    assert created and not repeated and row.id == same.id and msg.id == same_msg.id
    assert row.service_code == 100 and msg.text == "<b>literal text</b>"
    async with Session() as session:
        assert await session.scalar(select(func.count()).select_from(SupportTicket)) == 1
        assert await session.scalar(select(func.count()).select_from(TicketMessage)) == 1


async def test_ticket_foreign_service_rejected_without_orphans(cx):
    with pytest.raises(ValueError):
        await tickets.add_message(3, "test", service_code=100)
    async with Session() as session:
        assert await session.scalar(select(func.count()).select_from(SupportTicket)) == 0


async def test_ticket_staff_can_reply_but_not_see_after_role_revoked(cx):
    row, _, _ = await tickets.add_message(2, "help", service_code=100, file_id="test-file")
    await tickets.change_status(3, row.id, "claim")
    replied, _, _ = await tickets.add_message(3, "answer", ticket_id=row.id)
    assert replied.assigned_to == 3 and replied.status == "waiting"
    await change_setting(1, "cx_support_ids", [])
    for call in (tickets.history(3, row.id), tickets.change_status(3, row.id, "close"), tickets.attachment(3, 1)):
        with pytest.raises(ValueError):
            await call
    assert (await tickets.history(2, row.id))[0].id == row.id


async def test_ticket_closed_messages_and_reopen(cx):
    row, _, _ = await tickets.add_message(2, "help")
    await tickets.change_status(2, row.id, "close")
    with pytest.raises(ValueError):
        await tickets.add_message(2, "should not save", ticket_id=row.id)
    await tickets.change_status(2, row.id, "reopen")
    row, _, _ = await tickets.add_message(2, "now open", ticket_id=row.id)
    assert row.status == "open"
    _, _, messages, _ = await tickets.history(2, row.id)
    assert len([m for m in messages if m.kind == "system"]) == 2


async def test_ticket_open_cap_and_reopen_cap(cx):
    first, _, _ = await tickets.add_message(2, "one")
    await tickets.change_status(2, first.id, "close")
    for _ in range(3):
        await tickets.add_message(2, "help")
    with pytest.raises(ValueError):
        await tickets.add_message(2, "fourth")
    with pytest.raises(ValueError):
        await tickets.change_status(2, first.id, "reopen")


@pytest.mark.parametrize("text", ["", "x" * 3001])
async def test_ticket_invalid_text(cx, text):
    with pytest.raises(ValueError):
        await tickets.add_message(2, text)


async def test_ticket_attachment_only_and_ownership(cx):
    row, msg, _ = await tickets.add_message(2, "", file_id="telegram-ref")
    assert await tickets.attachment(2, msg.id) == "telegram-ref"
    await change_setting(1, "cx_support_ids", [])
    with pytest.raises(ValueError):
        await tickets.attachment(3, msg.id)
    await tickets.change_status(2, row.id, "close")
    await change_setting(1, "cx_tickets_enabled", False)
    assert (await tickets.history(2, row.id))[0].status == "closed"
    with pytest.raises(ValueError):
        await tickets.add_message(2, "disabled new ticket")


async def test_ticket_history_paginates_without_duplicates(cx):
    row, _, _ = await tickets.add_message(2, "first")
    for i in range(8):
        await tickets.add_message(2, str(i), ticket_id=row.id)
    _, _, recent, more = await tickets.history(2, row.id)
    _, _, old, more_old = await tickets.history(2, row.id, before=recent[0].id)
    assert more and not more_old and len(recent) == 5 and len(old) == 4
    assert not ({m.id for m in recent} & {m.id for m in old})


async def test_support_role_cannot_administer_flags_or_convert_foreign_trial(cx):
    with pytest.raises(ValueError):
        await change_setting(3, "cx_conversion_enabled", False)
    with pytest.raises(ValueError):
        await conversion.quote(3, 100, 11)
    with pytest.raises(ValueError):
        await tickets.list_tickets(2, staff_queue=True)


async def test_conversion_preserves_identity_and_debits_once(cx):
    order = await conversion.quote(2, 100, 11)
    result = await conversion.execute(2, order.token)
    assert result.status == "applied"
    assert await balance() == 9000
    again = await conversion.execute(2, order.token)
    assert again.token == result.token and len(cx.writes) == 1 and await balance() == 9000
    assert cx.current.data_limit == 11 * 1024**3
    assert cx.current.used_traffic == 512 * 1024**2
    assert cx.current.subscription_url.endswith("secret-test-link")
    assert "username" not in cx.writes[0] and "group_ids" not in cx.writes[0]
    async with Session() as session:
        service = await session.get(Service, 100)
        assert service.is_test is False and service.panel_userid == 222 and service.username == "test_100"
        assert service.ip_limit == 2
        assert (await session.get(TrialJourney, 100)).first_purchase_at
        assert (
            await session.scalar(
                select(func.count()).select_from(CustomerEvent).where(CustomerEvent.name == "trial_converted")
            )
            == 1
        )


async def test_conversion_rejects_forged_plan_owner_and_token(cx):
    with pytest.raises(ValueError):
        await conversion.quote(2, 100, 12)
    order = await conversion.quote(2, 100, 11)
    with pytest.raises(ValueError):
        await conversion.execute(3, order.token)
    with pytest.raises(ValueError):
        await conversion.execute(2, "a" * 32)
    assert await balance() == 10000 and cx.writes == []


@pytest.mark.parametrize(
    "change", ["price", "storage", "duration", "panel", "expired", "sale_off", "panel_off", "flag_off"]
)
async def test_conversion_revalidates_confirmation(cx, change):
    order = await conversion.quote(2, 100, 11)
    async with Session() as session, session.begin():
        plan = await session.get(Plan, 11)
        if change in ("price", "storage", "duration"):
            setattr(plan, change, getattr(plan, change) + 1)
        elif change == "panel":
            plan.panel_code = 99
        elif change == "expired":
            (await session.get(TrialConversion, order.token)).expires_at = 1
        elif change == "panel_off":
            (await session.get(Panels, 10)).enable = False
    if change in ("sale_off", "flag_off"):
        config = await SettingsManager().get_settings()
        await SettingsManager().update_setting(
            config.id, **{"sale_mode" if change == "sale_off" else "cx_conversion_enabled": False}
        )
    with pytest.raises(ValueError):
        await conversion.execute(2, order.token)
    assert await balance() == 10000 and cx.writes == []


async def test_insufficient_balance_then_topup_same_quote(cx):
    async with Session() as session, session.begin():
        (await session.get(User, 2)).amount = 0
    order = await conversion.quote(2, 100, 11)
    with pytest.raises(conversion.InsufficientBalance):
        await conversion.execute(2, order.token)
    assert (await conversion.get_order(2, order.token)).status == "quoted"
    async with Session() as session, session.begin():
        (await session.get(User, 2)).amount = 1000
    assert (await conversion.execute(2, order.token)).status == "applied"
    assert await balance() == 0


async def test_conversion_lost_response_reconciles_no_second_write(cx, monkeypatch):
    async def lose_response(panel, method, **kwargs):
        result = await cx.call(panel, method, **kwargs)
        if method == "modify_user_by_id":
            raise TimeoutError("response lost after application")
        return result

    monkeypatch.setattr(conversion, "panel_call", lose_response)
    order = await conversion.quote(2, 100, 11)
    with pytest.raises(TimeoutError):
        await conversion.execute(2, order.token)
    assert await balance() == 9000 and (await conversion.get_order(2, order.token)).status == "applying"
    await change_setting(1, "cx_conversion_enabled", False)
    assert (await conversion.execute(2, order.token)).status == "applied"
    assert len(cx.writes) == 1 and await balance() == 9000


async def test_db_sync_failure_reconciles_without_reapplying(cx, monkeypatch):
    order = await conversion.quote(2, 100, 11)
    complete = conversion._complete
    monkeypatch.setattr(conversion, "_complete", AsyncMock(side_effect=RuntimeError("db down")))
    with pytest.raises(RuntimeError):
        await conversion.execute(2, order.token)
    monkeypatch.setattr(conversion, "_complete", complete)
    assert (await conversion.execute(2, order.token)).status == "applied"
    assert len(cx.writes) == 1 and await balance() == 9000


async def test_conversion_drift_and_second_quote_do_not_charge_again(cx, monkeypatch):
    first = await conversion.quote(2, 100, 11)
    second = await conversion.quote(2, 100, 11)

    async def timeout(panel, method, **kwargs):
        if method == "modify_user_by_id":
            raise TimeoutError("ambiguous")
        return cx.current

    monkeypatch.setattr(conversion, "panel_call", timeout)
    with pytest.raises(TimeoutError):
        await conversion.execute(2, first.token)
    with pytest.raises(ValueError):
        await conversion.execute(2, second.token)
    cx.current.data_limit += 100
    with pytest.raises(ValueError):
        await conversion.execute(2, first.token)
    assert await balance() == 9000


@pytest.mark.parametrize("status", [400, 403, 404, 422, 500])
async def test_definitive_rejection_refunds_only_once(cx, monkeypatch, status):
    async def rejected(panel, method, **kwargs):
        if method == "modify_user_by_id":
            raise httpx.HTTPStatusError(
                "synthetic", request=httpx.Request("PUT", "https://panel.invalid"), response=httpx.Response(status)
            )
        return cx.current

    monkeypatch.setattr(conversion, "panel_call", rejected)
    order = await conversion.quote(2, 100, 11)
    with pytest.raises(httpx.HTTPStatusError):
        await conversion.execute(2, order.token)
    assert await balance() == (9000 if status == 500 else 10000)
    assert (await conversion.get_order(2, order.token)).status == ("applying" if status == 500 else "refunded")
    if status != 500:
        with pytest.raises(ValueError):
            await conversion.execute(2, order.token)
        assert await balance() == 10000


async def test_uncertain_then_rejected_does_not_refund_blindly(cx, monkeypatch):
    writes = 0

    async def rejected(panel, method, **kwargs):
        nonlocal writes
        if method == "modify_user_by_id":
            writes += 1
            if writes == 1:
                raise TimeoutError()
            raise httpx.HTTPStatusError(
                "synthetic", request=httpx.Request("PUT", "https://panel.invalid"), response=httpx.Response(403)
            )
        return cx.current

    monkeypatch.setattr(conversion, "panel_call", rejected)
    order = await conversion.quote(2, 100, 11)
    with pytest.raises(TimeoutError):
        await conversion.execute(2, order.token)
    with pytest.raises(httpx.HTTPStatusError):
        await conversion.execute(2, order.token)
    assert await balance() == 9000
    assert (await conversion.get_order(2, order.token)).status == "applying"


async def test_retire_expired_trial_retains_then_deletes(cx):
    now = cx.now
    cx.current.expire = now - 1
    await ServiceCRUD().update_service(100, expiration_time=now - 1)
    assert not await lifecycle.retire_trial(100, now=now)
    assert cx.writes == []
    async with Session() as session:
        row = await session.get(TrialJourney, 100)
        assert row.ended_at == now - 1 and not row.opted_in
    assert await lifecycle.retire_trial(100, now=now + journeys.RETENTION)
    assert cx.writes == ["delete"]
    assert (await ServiceCRUD().get_service(100))[0] is False


async def test_retire_stale_event_does_not_end_active_trial(cx):
    assert not await lifecycle.retire_trial(100)
    async with Session() as session:
        assert (await session.get(TrialJourney, 100)).ended_at is None


async def test_volume_exhausted_trial_retained_and_convertible(cx):
    cx.current.used_traffic = cx.current.data_limit
    cx.current.status = "limited"
    assert not await lifecycle.retire_trial(100)
    order = await conversion.quote(2, 100, 11)
    await conversion.execute(2, order.token)
    assert not await lifecycle.retire_trial(100, now=cx.now + 31 * 86400)
    assert "delete" not in cx.writes


async def test_cleanup_keeps_paid_intent_on_panel_failure(cx, monkeypatch):
    order = await conversion.quote(2, 100, 11)
    await conversion._reserve(2, order.token, cx.current)
    cx.current.expire = 1
    get = AsyncMock(side_effect=TimeoutError())
    monkeypatch.setattr(lifecycle, "panel_call", get)
    assert not await lifecycle.retire_trial(100, now=cx.now + 10 * 86400)
    get.assert_not_awaited()
    assert (await ServiceCRUD().get_service(100))[0]


async def test_retirement_panel_failure_does_not_delete_local_row(cx, monkeypatch):
    cx.current.expire = 1
    await ServiceCRUD().update_service(100, expiration_time=1)

    async def failure(panel, method, **kwargs):
        if method == "remove_user_by_id":
            raise TimeoutError()
        return cx.current

    monkeypatch.setattr(lifecycle, "panel_call", failure)
    with pytest.raises(TimeoutError):
        await lifecycle.retire_trial(100)
    assert (await ServiceCRUD().get_service(100))[0]


async def due_journey(group="message", *, optin=True):
    # Noon Tehran, independent of test execution timezone.
    now = int(datetime(2026, 9, 8, 9, 0, tzinfo=UTC).timestamp())
    async with Session() as session, session.begin():
        row = await session.get(TrialJourney, 100)
        row.created_at, row.expires_at, row.ended_at = now - 86400, now - 3 * 3600, now - 3 * 3600
        row.retain_until = row.ended_at + journeys.RETENTION
        row.opted_in, row.consented_at = optin, now - 86400 if optin else None
        row.experiment_group = group
    return now


async def test_followup_optin_required_and_sends_once(cx):
    now = await due_journey(optin=False)
    send = AsyncMock()
    assert await journeys.send_followups(send, now=now) == 0
    await journeys.consent(2, 100, allowed=True)
    assert await journeys.send_followups(send, now=now) == 1
    assert await journeys.send_followups(send, now=now + 300) == 0
    send.assert_awaited_once_with(2, 100)
    await journeys.click(2, 100)
    async with Session() as session:
        row = await session.get(TrialJourney, 100)
        assert row.sent_at == now and row.clicked_at and row.attempted_at == now


async def test_followup_holdout_does_not_send(cx):
    now = await due_journey("holdout")
    send = AsyncMock()
    assert await journeys.send_followups(send, now=now) == 0
    send.assert_not_awaited()
    async with Session() as session:
        assert (await session.get(TrialJourney, 100)).followup_status == "holdout"


@pytest.mark.parametrize("reason", ["optout", "purchase", "banned", "disabled", "night", "too_late", "transferred"])
async def test_followup_suppression(cx, reason):
    now = await due_journey()
    if reason == "optout":
        await journeys.consent(2, allowed=False)
    elif reason == "purchase":
        await ServiceCRUD().create_service(
            cx_purchase=True, code=101, username="paid", id=2, in_panel=10, expiration_time=now + 86400, is_test=False
        )
    elif reason == "banned":
        async with Session() as session, session.begin():
            (await session.get(User, 2)).status = "ban"
    elif reason == "disabled":
        await change_setting(1, "cx_followup_enabled", False)
    elif reason == "night":
        now += 12 * 3600
    elif reason == "too_late":
        now += 3 * 86400
    elif reason == "transferred":
        await ServiceCRUD().update_service(100, id=3)
    send = AsyncMock()
    assert await journeys.send_followups(send, now=now) == 0
    send.assert_not_awaited()


async def test_uncertain_followup_and_optout_race_never_retry(cx):
    now = await due_journey()

    async def unknown(user, code):
        await journeys.consent(user, allowed=False)
        raise TimeoutError("accepted or not unknown")

    send = AsyncMock(side_effect=unknown)
    await journeys.send_followups(send, now=now)
    await journeys.consent(2, 100, allowed=True)
    await journeys.send_followups(send, now=now + 300)
    assert send.await_count == 1
    async with Session() as session:
        assert (await session.get(TrialJourney, 100)).attempted_at == now


async def test_purchase_metrics_rollback_with_service_insert(cx):
    before = (await journeys.report())[0].get("purchase_delivered", 0)
    ok, _ = await ServiceCRUD().create_service(cx_purchase=True, code=100, username="duplicate", id=2, is_test=False)
    assert not ok
    after = (await journeys.report())[0].get("purchase_delivered", 0)
    assert before == after
    async with Session() as session:
        assert (await session.get(TrialJourney, 100)).first_purchase_at is None


async def test_customer_events_deduplicate_and_owner_checked(cx):
    await journeys.track_event(2, 100, "guide_started")
    await journeys.track_event(2, 100, "guide_started")
    with pytest.raises(ValueError):
        await journeys.track_event(3, 100, "connected_self_reported")
    async with Session() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(CustomerEvent).where(CustomerEvent.name == "guide_started")
            )
            == 1
        )


async def test_telegram_callbacks_private_and_forged_ids(cx):
    from app.telegram.user.customer_experience import handlers

    event = SimpleNamespace(
        is_private=False, sender_id=2, data=b"cx:plans:100:0", answer=AsyncMock(), respond=AsyncMock()
    )
    with pytest.raises(events.StopPropagation):
        await handlers.callback(event)
    event.respond.assert_not_awaited()
    event.is_private = True
    event.sender_id = 3
    for data in [b"cx:guide:100", b"cx:toggle:0", b"cx:pending:0", b"cx:plans:100:0", b"cx:quote:100:11"]:
        event.data = data
        with pytest.raises(events.StopPropagation):
            await handlers.callback(event)
    assert await balance() == 10000


async def test_callback_payloads_fit_telegram_limit(cx):
    from app.telegram.user.customer_experience.handlers import service_button_rows

    config = await SettingsManager().get_settings()
    _, service = await ServiceCRUD().get_service(100)
    rows = await service_button_rows(service, config)
    assert len(rows) == 2
    assert {b.data for row in rows for b in row.buttons} == {b"cx:plans:100:0", b"cx:remind:100"}
    assert all(len(b.data) <= 64 for row in rows for b in row.buttons)


@pytest.mark.skipif(engine.dialect.name == "sqlite", reason="Real row locks required")
async def test_concurrent_ticket_duplicate_and_cap(cx):
    result = await asyncio.gather(
        *[tickets.add_message(2, "one update", source_chat_id=2, source_message_id=99) for _ in range(5)]
    )
    assert sum(int(created) for _, _, created in result) == 1
    assert len({row.id for row, _, _ in result}) == 1
    results = await asyncio.gather(*[tickets.add_message(2, "new") for _ in range(5)], return_exceptions=True)
    assert sum(not isinstance(r, Exception) for r in results) == 2


@pytest.mark.skipif(engine.dialect.name == "sqlite", reason="Real row locks required")
async def test_concurrent_followup_claim(cx):
    now = await due_journey()
    send = AsyncMock()
    results = await asyncio.gather(journeys.send_followups(send, now=now), journeys.send_followups(send, now=now))
    assert sum(results) == 1 and send.await_count == 1


@pytest.mark.skipif(
    not os.getenv("TEST_REDIS_URL") or engine.dialect.name == "sqlite", reason="Real Redis and database required"
)
async def test_real_conversion_and_retirement_lock(cx, monkeypatch):
    from redis.asyncio import Redis

    from app.services import locks

    client = Redis.from_url(os.environ["TEST_REDIS_URL"], decode_responses=True)
    monkeypatch.setattr(locks, "get_redis", AsyncMock(return_value=client))
    monkeypatch.setattr(conversion, "distributed_lock", locks.distributed_lock)
    monkeypatch.setattr(lifecycle, "distributed_lock", locks.distributed_lock)
    order = await conversion.quote(2, 100, 11)
    entered, proceed = asyncio.Event(), asyncio.Event()

    async def slow(panel, method, **kwargs):
        if method == "modify_user_by_id":
            entered.set()
            await proceed.wait()
        return await cx.call(panel, method, **kwargs)

    monkeypatch.setattr(conversion, "panel_call", slow)
    task = asyncio.create_task(conversion.execute(2, order.token))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        with pytest.raises(RuntimeError, match="progress"):
            await conversion.execute(2, order.token)
        with pytest.raises(RuntimeError, match="progress"):
            await lifecycle.retire_trial(100)
        proceed.set()
        assert (await task).status == "applied"
        assert await balance() == 9000 and len(cx.writes) == 1
    finally:
        proceed.set()
        if not task.done():
            task.cancel()
        await client.aclose()


async def test_retired_trials_not_rescanned_until_cleanup_due(cx, monkeypatch):
    now = int(time.time())
    async with Session() as session, session.begin():
        for code in range(1, 51):
            session.add(Service(code=code, username=f"old{code}", id=2, is_test=True, expiration_time=now - 10000))
            session.add(
                TrialJourney(
                    service_code=code,
                    user_id=2,
                    panel_code=10,
                    created_at=now - 20000,
                    expires_at=now - 10000,
                    ended_at=now - 10000,
                    retain_until=now + 86400,
                    experiment_group="message",
                    followup_status="pending",
                    opted_in=False,
                )
            )
        (await session.get(Service, 100)).expiration_time = now - 1
    retire = AsyncMock(return_value=False)
    monkeypatch.setattr(lifecycle, "retire_trial", retire)
    await lifecycle.cleanup_trials()
    retire.assert_awaited_once()
    assert retire.call_args.args == (100,)


async def test_expired_unapplied_target_not_written(cx, monkeypatch):
    order = await conversion.quote(2, 100, 11)
    await conversion._reserve(2, order.token, cx.current)
    async with Session() as session, session.begin():
        row = await session.get(TrialConversion, order.token)
        row.target_values = {**row.target_values, "expire": 1}
    with pytest.raises(ValueError):
        await conversion.execute(2, order.token)
    assert cx.writes == [] and await balance() == 9000


async def test_claimed_followup_after_crash_is_not_resent(cx):
    now = await due_journey()
    async with Session() as session, session.begin():
        row = await session.get(TrialJourney, 100)
        row.followup_status, row.attempted_at = "claimed", now - 300
    send = AsyncMock()
    assert await journeys.send_followups(send, now=now) == 0
    send.assert_not_awaited()


async def test_two_trials_same_user_get_only_one_delivery_attempt(cx):
    now = await due_journey()
    async with Session() as session, session.begin():
        session.add(Service(code=101, username="second-test", id=2, is_test=True, expiration_time=now - 10800))
        session.add(
            TrialJourney(
                service_code=101,
                user_id=2,
                panel_code=10,
                created_at=now - 86400,
                ended_at=now - 10800,
                expires_at=now - 10800,
                retain_until=now + 86400,
                experiment_group="message",
                followup_status="pending",
                opted_in=True,
                consented_at=now - 86400,
            )
        )
    send = AsyncMock()
    assert await journeys.send_followups(send, now=now) == 1
    assert await journeys.send_followups(send, now=now + 300) == 0
    send.assert_awaited_once()


async def test_customer_unsubscribe_and_purchase_during_send_are_not_overwritten(cx):
    now = await due_journey()

    async def send(user_id, code):
        async with Session() as session, session.begin():
            await journeys.purchase_in_session(session, user_id, 101, now)

    await journeys.send_followups(send, now=now)
    async with Session() as session:
        row = await session.get(TrialJourney, 100)
        assert row.followup_status == "purchased" and row.first_purchase_at == now and row.sent_at == now


async def test_matured_report_excludes_recent_cohorts(cx):
    now = await due_journey()
    _, _, cohorts, _ = await journeys.report(now=now)
    assert cohorts["message"] == (0, 0)
    async with Session() as session, session.begin():
        row = await session.get(TrialJourney, 100)
        row.created_at = now - 10 * 86400
        row.ended_at = now - 8 * 86400
        row.first_purchase_at = now - 7 * 86400
    _, _, cohorts, _ = await journeys.report(now=now)
    assert cohorts["message"] == (1, 1)
    await journeys.consent(2, allowed=False)
    assert (await journeys.report(now=now))[2]["message"] == (1, 1)


async def test_ticket_media_message_and_plain_parse_mode(cx, monkeypatch):
    from app.telegram.user.customer_experience import handlers

    state = {2: "cx_new:100:connection:network"}

    async def get_step(uid):
        return state.get(uid)

    async def set_step(uid, step):
        state[uid] = step

    monkeypatch.setattr(handlers, "get_step", get_step)
    monkeypatch.setattr(handlers, "set_step", set_step)
    monkeypatch.setattr(handlers, "notify_ticket", AsyncMock())
    monkeypatch.setattr(handlers, "pack_bot_file_id", lambda media: "photo-reference")
    event = SimpleNamespace(
        sender_id=2,
        chat_id=2,
        is_private=True,
        raw_text="<b>not HTML</b>",
        message=SimpleNamespace(
            id=500,
            media=object(),
            photo=object(),
            document=None,
            file=SimpleNamespace(size=100, mime_type="image/jpeg"),
        ),
        respond=AsyncMock(),
    )
    with pytest.raises(events.StopPropagation):
        await handlers.message.__wrapped__(event)
    assert state[2] == "home"
    assert event.respond.call_args.kwargs["parse_mode"] is None
    async with Session() as session:
        msg = await session.scalar(select(TicketMessage))
        assert msg.file_id == "photo-reference" and "<b>not HTML</b>" in msg.text
        assert "وصل نمی‌شود" in msg.text


async def test_ticket_large_attachment_not_saved(cx, monkeypatch):
    from app.telegram.user.customer_experience import handlers

    monkeypatch.setattr(handlers, "get_step", AsyncMock(return_value="cx_new:100:other:other"))
    event = SimpleNamespace(
        sender_id=2,
        chat_id=2,
        is_private=True,
        raw_text="",
        message=SimpleNamespace(
            id=501,
            media=object(),
            photo=None,
            document=object(),
            file=SimpleNamespace(size=20 * 1024**2, mime_type="application/pdf"),
        ),
        respond=AsyncMock(),
    )
    with pytest.raises(events.StopPropagation):
        await handlers.message.__wrapped__(event)
    async with Session() as session:
        assert await session.scalar(select(func.count()).select_from(TicketMessage)) == 0


async def test_disabled_guide_and_foreign_success_callback_do_not_count(cx):
    from app.telegram.user.customer_experience import handlers

    await change_setting(1, "cx_onboarding_enabled", False)
    event = SimpleNamespace(
        is_private=True, sender_id=2, data=b"cx:connected:100", answer=AsyncMock(), respond=AsyncMock()
    )
    with pytest.raises(events.StopPropagation):
        await handlers.callback(event)
    async with Session() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(CustomerEvent).where(CustomerEvent.name == "connected_self_reported")
            )
            == 0
        )


@pytest.mark.parametrize("os_key", ["android", "ios", "windows", "macos"])
async def test_onboarding_uses_owned_subscription_without_rotating(cx, monkeypatch, os_key):
    from app.telegram.user.customer_experience import handlers

    monkeypatch.setattr(handlers, "panel_call", cx.call)
    event = SimpleNamespace(sender_id=2, respond=AsyncMock())
    await handlers.guide(event, 100, os_key, installed=True)
    buttons = event.respond.call_args.kwargs["buttons"]
    assert buttons[0][0].copy_text.endswith("secret-test-link")
    assert cx.writes == []


@pytest.mark.skipif(engine.dialect.name == "sqlite", reason="Real row locks required")
async def test_concurrent_different_quotes_reserve_only_one_debit(cx):
    first = await conversion.quote(2, 100, 11)
    second = await conversion.quote(2, 100, 11)
    results = await asyncio.gather(
        conversion._reserve(2, first.token, cx.current),
        conversion._reserve(2, second.token, cx.current),
        return_exceptions=True,
    )
    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert await balance() == 9000


@pytest.mark.parametrize("admin", [False, True])
@pytest.mark.parametrize("is_test", [False, True])
async def test_service_cards_omit_removed_buttons_even_when_enabled(cx, admin, is_test):
    from app.telegram.keyboards.services import create_inline_service_buttons

    config = await SettingsManager().get_settings()
    await SettingsManager().update_setting(
        config.id,
        usage_chart_mode=True,
        cx_onboarding_enabled=True,
        cx_tickets_enabled=True,
        support_mode=True,
        qr_mode=True,
        client_list_mode=True,
    )
    config = await SettingsManager().get_settings()
    async with Session() as session:
        service = await session.get(Service, 100)
        panel = await session.get(Panels, 10)
        service.is_test = is_test
        markup = await create_inline_service_buttons(
            service,
            panel=panel,
            settings=config,
            admin=admin,
            link="https://example.invalid/sub/synthetic",
            status="disabled",
        )
    buttons = [button for row in markup.rows for button in row.buttons]
    callbacks = [getattr(button, "data", b"") for button in buttons]
    callbacks = [data.encode() if isinstance(data, str) else data for data in callbacks]
    assert not any(data.startswith((b"UsageChart:", b"cx:guide:", b"cx:topics:")) for data in callbacks)
    assert b"getQrcode:100" in callbacks and b"showClients:100" in callbacks
    assert all(row.buttons for row in markup.rows)


@pytest.mark.parametrize("is_test", [False, True])
async def test_delivery_buttons_keep_conversion_and_reminder_only(cx, is_test):
    from app.telegram.user.customer_experience.handlers import service_button_rows

    config = SimpleNamespace(
        cx_onboarding_enabled=True,
        cx_tickets_enabled=True,
        support_mode=True,
        cx_conversion_enabled=True,
        sale_mode=True,
        cx_followup_enabled=True,
    )
    rows = await service_button_rows(SimpleNamespace(code=100, is_test=is_test), config)
    callbacks = [button.data for row in rows for button in row.buttons]
    assert callbacks == ([b"cx:plans:100:0", b"cx:remind:100"] if is_test else [])
