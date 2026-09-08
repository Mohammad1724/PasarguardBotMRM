"""Recurring wallet consent/billing tests: fake panel/Telegram, real SQL transactions."""

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import func, select
from telethon import events

from app.db.base import AsyncSessionLocal as Session, engine
from app.db.crud.settings import SettingsManager
from app.db.models.auto_renew import (
    AutoRenewAttempt as Attempt,
    AutoRenewConsent as Consent,
    AutoRenewNotice as Notice,
    AutoRenewPolicy as Policy,
)
from app.db.models.panels import Panels
from app.db.models.plans import Plan
from app.db.models.services import Service
from app.db.models.user import User
from app.services import locks
from app.services.auto_renew import cleanup, locking, notices, service as ar


@asynccontextmanager
async def fake_lock(*args, **kwargs):
    yield


@pytest.fixture
async def renewal(users, monkeypatch):
    clock = [int(datetime(2026, 9, 8, 9, tzinfo=UTC).timestamp())]
    monkeypatch.setattr(ar, "now_ts", lambda: clock[0])
    monkeypatch.setattr(notices, "now_ts", lambda: clock[0])
    monkeypatch.setattr(locks, "distributed_lock", fake_lock)
    config = await SettingsManager().get_settings()
    await SettingsManager().update_setting(config.id, auto_renew_enabled=True, tamdid_mode=True)
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
                    price=1000,
                    storage=10,
                    duration=30,
                    panel_code=99,
                    plan_type="volume",
                    data_limit_reset_strategy="no_reset",
                    ip_limit=2,
                ),
                Service(
                    code=100,
                    id=2,
                    username="paid_100",
                    in_panel=10,
                    panel_userid=222,
                    is_test=False,
                    enable=True,
                    expiration_time=clock[0] + 20 * 3600,
                    package_size=1024**3,
                ),
            ]
        )
    current = SimpleNamespace(
        id=222,
        username="paid_100",
        expire=clock[0] + 20 * 3600,
        data_limit=1024**3,
        used_traffic=512 * 1024**2,
        hwid_limit=0,
        data_limit_reset_strategy="no_reset",
        status="active",
        next_plan=None,
        subscription_url="https://panel.invalid/sub/private",
        group_ids=[1, 2],
    )
    writes = []

    async def panel_call(panel, method, **kwargs):
        if method == "get_user_by_id":
            return current
        if method == "modify_user_by_id":
            values = kwargs["user"].model_dump(exclude_none=True)
            writes.append(values)
            for k, v in values.items():
                setattr(current, k, v)
            return current
        if method == "remove_user_by_id":
            writes.append("delete")
            return None
        raise AssertionError(method)

    monkeypatch.setattr(ar, "panel_call", panel_call)
    return SimpleNamespace(clock=clock, current=current, writes=writes, call=panel_call)


async def balance():
    async with Session() as session:
        return (await session.get(User, 2)).amount


async def enable(renewal, multiple=2):
    quote = await ar.make_consent(2, 100, 11, multiple)
    await ar.confirm(2, quote.token)
    return quote


async def advance_notice(renewal, *, multiple=2):
    await enable(renewal, multiple)
    await ar.process_policy(100)
    await notices.dispatch(AsyncMock())
    renewal.clock[0] += ar.NOTICE_LEAD


async def policy():
    async with Session() as session:
        return await session.get(Policy, 100)


async def attempts():
    async with Session() as session:
        return list((await session.scalars(select(Attempt))).all())


async def test_default_off(users):
    assert not (await SettingsManager().get_settings()).auto_renew_enabled


async def test_selection_is_not_consent_or_debit(renewal):
    quote = await ar.make_consent(2, 100, 11, 2)
    assert quote.confirmed_at is None and await balance() == 10000 and await policy() is None
    result = await ar.confirm(2, quote.token)
    assert result.state == "enabled" and result.monthly_cap == 2000 and result.per_charge_cap == 1000
    assert await balance() == 10000 and renewal.writes == []


async def test_consent_replay_cannot_reenable_after_cancel(renewal):
    quote = await enable(renewal)
    await ar.disable(2, 100)
    with pytest.raises(ValueError):
        await ar.confirm(2, quote.token)
    assert (await policy()).state == "off"


async def test_other_unused_quote_invalidated_by_cancel(renewal):
    await enable(renewal)
    quote = await ar.make_consent(2, 100, 11, 3)
    await ar.disable(2, 100)
    with pytest.raises(ValueError):
        await ar.confirm(2, quote.token)
    assert (await policy()).state == "off"


@pytest.mark.parametrize(
    "change",
    [
        "price",
        "duration",
        "storage",
        "cap_expired",
        "panel_url",
        "owner",
        "service_expire",
        "flag_off",
        "renew_off",
        "test_service",
    ],
)
async def test_confirmation_rechecks_all_terms(renewal, change):
    quote = await ar.make_consent(2, 100, 11, 2)
    async with Session() as session, session.begin():
        if change in ("price", "duration", "storage"):
            plan = await session.get(Plan, 11)
            setattr(plan, change, getattr(plan, change) + 1)
        elif change == "cap_expired":
            (await session.get(Consent, quote.token)).expires_at = 1
        elif change == "panel_url":
            (await session.get(Panels, 10)).base_url = "https://different.invalid"
        elif change == "owner":
            (await session.get(Service, 100)).id = 3
        elif change == "test_service":
            (await session.get(Service, 100)).is_test = True
    if change == "service_expire":
        renewal.current.expire += 1
    if change in ("flag_off", "renew_off"):
        config = await SettingsManager().get_settings()
        await SettingsManager().update_setting(
            config.id, **{"auto_renew_enabled" if change == "flag_off" else "tamdid_mode": False}
        )
    with pytest.raises(ValueError):
        await ar.confirm(2, quote.token)
    assert await balance() == 10000 and await policy() is None


async def test_forged_owner_plan_or_cap_rejected(renewal):
    for args in [(3, 100, 11, 2), (2, 100, 12, 2), (2, 100, 11, 0), (2, 100, 11, 4)]:
        with pytest.raises(ValueError):
            await ar.make_consent(*args)
    quote = await ar.make_consent(2, 100, 11, 2)
    with pytest.raises(ValueError):
        await ar.confirm(3, quote.token)
    await ar.confirm(2, quote.token)
    with pytest.raises(ValueError):
        await ar.disable(3, 100)
    assert (await policy()).state == "enabled"


@pytest.mark.parametrize(
    "field,value",
    [
        ("data_limit", 0),
        ("expire", None),
        ("status", "disabled"),
        ("data_limit_reset_strategy", "month"),
        ("next_plan", {"x": 1}),
    ],
)
async def test_unsupported_service_not_enrolled(renewal, field, value):
    setattr(renewal.current, field, value)
    with pytest.raises(ValueError):
        await ar.make_consent(2, 100, 11, 2)


async def test_no_advance_notice_no_charge(renewal):
    await enable(renewal)
    for _ in range(3):
        await ar.process_policy(100)
        renewal.clock[0] += 3600
    assert await balance() == 10000 and not await attempts()
    async with Session() as session:
        assert await session.scalar(select(func.count()).select_from(Notice)) == 1


async def test_notice_lead_then_once_preserving_time_volume_and_identity(renewal):
    old_expire = renewal.current.expire
    await advance_notice(renewal)
    renewal.clock[0] -= 1
    await ar.process_policy(100)
    assert await balance() == 10000
    renewal.clock[0] += 901
    await ar.process_policy(100)
    assert await balance() == 9000 and len(renewal.writes) == 1
    assert renewal.current.expire == old_expire + 30 * 86400
    assert renewal.current.data_limit == 11 * 1024**3
    assert renewal.current.used_traffic == 512 * 1024**2
    assert renewal.current.subscription_url.endswith("private") and renewal.current.group_ids == [1, 2]
    for _ in range(3):
        renewal.clock[0] += 3600
        await ar.process_policy(100)
    rows = await attempts()
    assert len(rows) == 1 and rows[0].status == "applied" and await balance() == 9000
    await ar.reconcile(rows[0].token)
    assert len(renewal.writes) == 1


@pytest.mark.parametrize("result", ["uncertain", "claimed", "cancelled"])
async def test_unconfirmed_notice_never_authorizes_debit(renewal, result):
    await enable(renewal)
    await ar.process_policy(100)
    async with Session() as session, session.begin():
        row = await session.scalar(select(Notice))
        row.state = result
    renewal.clock[0] += 10 * 3600
    await ar.process_policy(100)
    assert await balance() == 10000 and renewal.writes == []


async def test_delayed_notice_delivery_needs_full_lead(renewal):
    await enable(renewal)
    await ar.process_policy(100)
    renewal.clock[0] += 10 * 3600
    await notices.dispatch(AsyncMock())
    await ar.process_policy(100)
    assert await balance() == 10000
    renewal.clock[0] += 6 * 3600
    await ar.process_policy(100)
    assert await balance() == 9000


async def test_low_balance_warning_once_and_topup_then_renew(renewal):
    await advance_notice(renewal)
    async with Session() as session, session.begin():
        (await session.get(User, 2)).amount = 0
    for _ in range(3):
        await ar.process_policy(100)
        renewal.clock[0] += 3600
    async with Session() as session, session.begin():
        assert await session.scalar(select(func.count()).select_from(Notice).where(Notice.kind == "low_balance")) == 1
        (await session.get(User, 2)).amount = 1000
    await ar.process_policy(100)
    assert await balance() == 0 and len(renewal.writes) == 1


@pytest.mark.parametrize(
    "change",
    ["price", "storage", "owner", "expire", "panel_url", "flag_off", "renew_off", "banned", "disabled", "cancel"],
)
async def test_no_new_charge_after_permission_or_terms_change(renewal, change):
    await advance_notice(renewal)
    async with Session() as session, session.begin():
        if change in ("price", "storage"):
            plan = await session.get(Plan, 11)
            setattr(plan, change, getattr(plan, change) + 1)
        elif change == "owner":
            (await session.get(Service, 100)).id = 3
        elif change == "panel_url":
            (await session.get(Panels, 10)).base_url = "https://other.invalid"
        elif change == "banned":
            (await session.get(User, 2)).status = "ban"
    if change == "expire":
        renewal.current.expire += 1
    if change == "disabled":
        renewal.current.status = "disabled"
    if change == "cancel":
        await ar.disable(2, 100)
    if change in ("flag_off", "renew_off"):
        config = await SettingsManager().get_settings()
        await SettingsManager().update_setting(
            config.id, **{"auto_renew_enabled" if change == "flag_off" else "tamdid_mode": False}
        )
    await ar.process_policy(100)
    assert await balance() == 10000 and renewal.writes == []


async def test_monthly_budget_counts_unsettled_and_applied_not_refunds(renewal):
    await advance_notice(renewal, multiple=1)
    now = renewal.clock[0]
    async with Session() as session, session.begin():
        session.add(
            Attempt(
                token="a" * 32,
                user_id=2,
                service_code=100,
                policy_revision=1,
                cycle_expire=1,
                identity={},
                price=1000,
                month=ar.month_key(now),
                old_values={},
                target_values={},
                status="review",
                attempted=True,
                retry_count=1,
                next_retry_at=now + 86400,
                error="",
                created_at=now - 86400,
            )
        )
    await ar.process_policy(100)
    assert await balance() == 10000 and (await policy()).reason == "monthly_cap"
    async with Session() as session, session.begin():
        (await session.get(Attempt, "a" * 32)).status = "refunded"
    renewal.clock[0] += 3600
    await ar.process_policy(100)
    assert await balance() == 9000


async def test_calendar_month_is_tehran_and_remaining_time_not_lost(renewal):
    a = int(datetime(2026, 9, 30, 20, 29, tzinfo=UTC).timestamp())
    assert ar.month_key(a) == "2026-09" and ar.month_key(a + 120) == "2026-10"


async def test_too_late_stops_without_debit(renewal):
    await advance_notice(renewal)
    renewal.clock[0] = renewal.current.expire + ar.EXPIRED_GRACE + 1
    await ar.process_policy(100)
    assert (await policy()).reason == "too_late" and await balance() == 10000


async def test_response_lost_after_apply_reconciles_no_double_write_even_when_off(renewal, monkeypatch):
    await advance_notice(renewal)

    async def lost(panel, method, **kwargs):
        value = await renewal.call(panel, method, **kwargs)
        if method == "modify_user_by_id":
            raise TimeoutError("response lost")
        return value

    monkeypatch.setattr(ar, "panel_call", lost)
    await ar.process_policy(100)
    order = (await attempts())[0]
    assert order.status == "applying" and await balance() == 9000
    await ar.disable(2, 100)
    config = await SettingsManager().get_settings()
    await SettingsManager().update_setting(config.id, auto_renew_enabled=False)
    assert (await ar.reconcile(order.token)).status == "applied"
    assert await balance() == 9000 and len(renewal.writes) == 1 and (await policy()).state == "off"


async def test_database_finish_failure_does_not_repeat_external_renewal(renewal, monkeypatch):
    await advance_notice(renewal)
    real = ar._settle
    monkeypatch.setattr(ar, "_settle", AsyncMock(side_effect=RuntimeError("DB down")))
    await ar.process_policy(100)
    order = (await attempts())[0]
    assert await balance() == 9000 and order.status == "applying"
    monkeypatch.setattr(ar, "_settle", real)
    assert (await ar.reconcile(order.token)).status == "applied" and len(renewal.writes) == 1


@pytest.mark.parametrize("status", [400, 403, 404, 422, 500])
async def test_definitive_first_rejection_refunds_unknown_does_not(renewal, monkeypatch, status):
    await advance_notice(renewal)

    async def reject(panel, method, **kwargs):
        if method == "modify_user_by_id":
            raise httpx.HTTPStatusError(
                "synthetic", request=httpx.Request("PUT", "https://panel.invalid"), response=httpx.Response(status)
            )
        return renewal.current

    monkeypatch.setattr(ar, "panel_call", reject)
    await ar.process_policy(100)
    row = (await attempts())[0]
    assert row.status == ("applying" if status == 500 else "refunded")
    assert await balance() == (9000 if status == 500 else 10000)
    if status != 500:
        await ar.reconcile(row.token)
        assert await balance() == 10000


async def test_unknown_then_rejection_does_not_refund_blindly(renewal, monkeypatch):
    await advance_notice(renewal)
    n = 0

    async def call(panel, method, **kwargs):
        nonlocal n
        if method == "modify_user_by_id":
            n += 1
            if n == 1:
                raise TimeoutError()
            raise httpx.HTTPStatusError(
                "synthetic", request=httpx.Request("PUT", "https://panel.invalid"), response=httpx.Response(403)
            )
        return renewal.current

    monkeypatch.setattr(ar, "panel_call", call)
    await ar.process_policy(100)
    order = (await attempts())[0]
    await ar.reconcile(order.token)
    assert await balance() == 9000 and (await attempts())[0].status == "applying"


async def test_panel_drift_goes_to_manual_review_and_protects_cleanup(renewal, monkeypatch):
    await advance_notice(renewal)

    async def call(panel, method, **kwargs):
        if method == "modify_user_by_id":
            raise TimeoutError()
        return renewal.current

    monkeypatch.setattr(ar, "panel_call", call)
    await ar.process_policy(100)
    order = (await attempts())[0]
    renewal.current.data_limit += 123
    await ar.reconcile(order.token)
    assert (await attempts())[0].status == "review"
    await ar.disable(2, 100)
    assert await locking.protected(100)
    assert not await cleanup.retire_paid(100, renewal.clock[0] + 10 * 86400)
    assert await balance() == 9000


async def test_manual_writes_blocked_while_enabled_or_unsettled(renewal, monkeypatch):
    await advance_notice(renewal)
    with pytest.raises(locking.RenewalConflict):
        async with locking.manual_write(100):
            pytest.fail("must block")
    await ar.disable(2, 100)
    async with locking.manual_write(100):
        pass
    await enable(renewal)
    renewal.clock[0] += 3600
    await ar.process_policy(100)
    await notices.dispatch(AsyncMock())
    renewal.clock[0] += ar.NOTICE_LEAD

    async def unknown(panel, method, **kwargs):
        if method == "modify_user_by_id":
            raise TimeoutError()
        return renewal.current

    monkeypatch.setattr(ar, "panel_call", unknown)
    await ar.process_policy(100)
    await ar.disable(2, 100)
    with pytest.raises(locking.RenewalConflict):
        async with locking.manual_write(100):
            pass


async def test_paid_cleanup_rechecks_live_expiry_and_preserves_on_failure(renewal, monkeypatch):
    now = renewal.clock[0] + 10 * 86400
    # local row says expired but live panel was manually extended: do not remove.
    renewal.current.expire = now + 86400
    assert not await cleanup.retire_paid(100, now)
    renewal.current.expire = 1

    async def fail(panel, method, **kwargs):
        if method == "remove_user_by_id":
            raise TimeoutError()
        return renewal.current

    monkeypatch.setattr(ar, "panel_call", fail)
    with pytest.raises(TimeoutError):
        await cleanup.retire_paid(100, now)
    async with Session() as session:
        assert await session.get(Service, 100)


async def test_notice_replay_private_and_failed_delivery_no_retry(renewal):
    await enable(renewal)
    await ar.process_policy(100)
    send = AsyncMock(side_effect=TimeoutError())
    await notices.dispatch(send)
    await notices.dispatch(send)
    assert send.await_count == 1
    renewal.clock[0] += ar.NOTICE_LEAD
    await ar.process_policy(100)
    assert await balance() == 10000


async def test_notice_cancelled_before_delivery_after_unsubscribe(renewal):
    await enable(renewal)
    await ar.process_policy(100)
    await ar.disable(2, 100)
    send = AsyncMock()
    await notices.dispatch(send)
    send.assert_not_awaited()


async def test_real_second_period_can_renew_not_just_first(renewal):
    await advance_notice(renewal)
    await ar.process_policy(100)
    assert await balance() == 9000
    # enter next month's renewal window and get a NEW advance notice
    renewal.clock[0] = int(renewal.current.expire) - 20 * 3600
    await ar.process_policy(100)
    await notices.dispatch(AsyncMock())
    renewal.clock[0] += ar.NOTICE_LEAD
    await ar.process_policy(100)
    rows = await attempts()
    assert len(rows) == 2 and all(r.status == "applied" for r in rows)
    assert rows[0].cycle_expire != rows[1].cycle_expire and await balance() == 8000


async def test_reused_cycle_never_debits_even_after_manual_panel_rollback(renewal):
    await advance_notice(renewal)
    old = ar.panel_snapshot(renewal.current)
    await ar.process_policy(100)
    for k, v in old.items():
        setattr(renewal.current, k, v)
    async with Session() as session, session.begin():
        p = await session.get(Policy, 100)
        p.expected_values = old
        p.next_check_at = 0
    await ar.process_policy(100)
    assert (await policy()).reason == "cycle_already_attempted" and await balance() == 9000


async def test_unprivileged_callbacks_cannot_operate_others(renewal):
    from app.telegram.user.auto_renew import handlers

    await enable(renewal)
    event = SimpleNamespace(sender_id=3, is_private=True, data=b"ar:off:100", answer=AsyncMock(), respond=AsyncMock())
    for data in [b"ar:off:100", b"ar:view:100", b"ar:switch", b"ar:admin:0", b"ar:quote:100:11:2"]:
        event.data = data
        with pytest.raises(events.StopPropagation):
            await handlers.callback(event)
    assert (await policy()).state == "enabled" and await balance() == 10000


async def test_guard_covers_stale_transfer_and_bulk_delete(renewal, monkeypatch):
    from app.services.auto_renew.guards import telegram_write_guard
    from app.telegram import state

    await enable(renewal)
    handler = AsyncMock()
    wrapped = telegram_write_guard(handler)
    monkeypatch.setattr(state, "get_step", AsyncMock(return_value="whating_send_TransferConfig"))
    monkeypatch.setattr(state, "get_data", AsyncMock(return_value="100"))
    event = SimpleNamespace(sender_id=2, respond=AsyncMock())
    await wrapped(event)
    handler.assert_not_awaited()
    event.sender_id = 1
    event.data = b"BulkDeleteConfirm:both"
    monkeypatch.setattr(state, "get_data", AsyncMock(return_value='{"services":[{"code":100}]}'))
    await wrapped(event)
    handler.assert_not_awaited()


@pytest.mark.skipif(engine.dialect.name == "sqlite", reason="Real row locks required")
async def test_two_concurrent_reservations_one_debit(renewal):
    await advance_notice(renewal)
    ident = {k: getattr(await policy(), k) for k in ("panel_code", "panel_userid", "username", "panel_url")}
    results = await asyncio.gather(
        ar._reserve(100, renewal.current, ident, renewal.clock[0]),
        ar._reserve(100, renewal.current, ident, renewal.clock[0]),
        return_exceptions=True,
    )
    assert sum(isinstance(result, Attempt) for result in results) == 1
    assert await balance() == 9000 and len(await attempts()) == 1


@pytest.mark.skipif(engine.dialect.name == "sqlite", reason="Real row locks required")
async def test_concurrent_notice_dispatch_one_delivery(renewal):
    await enable(renewal)
    await ar.process_policy(100)
    send = AsyncMock()
    await asyncio.gather(notices.dispatch(send), notices.dispatch(send))
    assert send.await_count == 1


@pytest.mark.skipif(not os.getenv("TEST_REDIS_URL"), reason="Explicit Redis required")
async def test_real_redis_manual_and_auto_mutual_exclusion(renewal, monkeypatch):
    # recover the actual function patched by this test's fixture
    import importlib

    from redis.asyncio import Redis

    importlib.reload(locks)
    client = Redis.from_url(os.environ["TEST_REDIS_URL"], decode_responses=True)
    monkeypatch.setattr(locks, "get_redis", AsyncMock(return_value=client))
    await enable(renewal)
    try:
        async with locking.service_write_lock(100):
            # same task is reentrant; a child task inherits ContextVars but NOT ownership
            async with locking.service_write_lock(100):
                pass

            async def contender():
                async with locking.service_write_lock(100):
                    pytest.fail("child must not bypass lock")

            with pytest.raises(RuntimeError):
                await asyncio.create_task(contender())
        await ar.disable(2, 100)
        async with locking.manual_write(100):
            with pytest.raises(RuntimeError):
                await asyncio.create_task(ar.make_consent(2, 100, 11, 2))
    finally:
        await client.aclose()
        monkeypatch.setattr(locks, "distributed_lock", fake_lock)


async def test_month_rollover_releases_budget_without_ignoring_cycle_or_notice(renewal):
    renewal.clock[0] = int(datetime(2026, 9, 30, 9, tzinfo=UTC).timestamp())
    renewal.current.expire = renewal.clock[0] + 20 * 3600
    await advance_notice(renewal, multiple=1)
    now = renewal.clock[0]
    async with Session() as session, session.begin():
        session.add(
            Attempt(
                token="c" * 32,
                user_id=2,
                service_code=100,
                policy_revision=1,
                cycle_expire=1,
                identity={},
                price=1000,
                month="2026-09",
                old_values={},
                target_values={},
                status="applied",
                attempted=True,
                retry_count=0,
                next_retry_at=0,
                error="",
                created_at=now - 86400,
            )
        )
    await ar.process_policy(100)
    assert await balance() == 10000 and (await policy()).reason == "monthly_cap"
    renewal.clock[0] = int(datetime(2026, 9, 30, 21, tzinfo=UTC).timestamp())
    await ar.process_policy(100)
    assert await balance() == 9000
    rows = await attempts()
    assert any(r.month == "2026-10" and r.cycle_expire != 1 for r in rows)


async def test_no_replay_of_old_charge_after_local_owner_changes(renewal, monkeypatch):
    await advance_notice(renewal)

    async def fail(panel, method, **kwargs):
        if method == "modify_user_by_id":
            raise TimeoutError()
        return renewal.current

    monkeypatch.setattr(ar, "panel_call", fail)
    await ar.process_policy(100)
    order = (await attempts())[0]
    async with Session() as session, session.begin():
        (await session.get(Service, 100)).id = 3
    await ar.reconcile(order.token)
    assert (await attempts())[0].status == "review" and renewal.writes == [] and await balance() == 9000


async def test_sender_cancel_after_notice_send_still_prevents_charge(renewal):
    await enable(renewal)
    await ar.process_policy(100)

    async def sender(row, text):
        await ar.disable(2, 100)

    await notices.dispatch(sender)
    renewal.clock[0] += ar.NOTICE_LEAD
    await ar.process_policy(100)
    assert await balance() == 10000 and not await attempts()


async def test_budget_only_reserves_after_warning_in_the_renewal_window(renewal):
    renewal.current.expire = renewal.clock[0] + 40 * 3600
    await advance_notice(renewal)
    await ar.process_policy(100)
    assert await balance() == 10000  # 34h left, despite a confirmed six-hour-old notice
    renewal.clock[0] += 11 * 3600
    await ar.process_policy(100)
    assert await balance() == 9000


async def test_attempt_retry_limit_leaves_funds_for_review(renewal, monkeypatch):
    await advance_notice(renewal)

    async def fail(panel, method, **kwargs):
        if method == "modify_user_by_id":
            raise TimeoutError()
        return renewal.current

    monkeypatch.setattr(ar, "panel_call", fail)
    await ar.process_policy(100)
    order = (await attempts())[0]
    for _ in range(9):
        await ar.reconcile(order.token)
    row = (await attempts())[0]
    assert row.status == "review" and row.retry_count == 10 and await balance() == 9000
    async with Session() as session:
        assert await session.scalar(select(func.count()).select_from(Notice).where(Notice.kind == "review")) == 1


async def test_insufficient_balance_past_grace_never_auto_charges_late_topup(renewal):
    await advance_notice(renewal)
    async with Session() as session, session.begin():
        (await session.get(User, 2)).amount = 0
    await ar.process_policy(100)
    renewal.clock[0] = renewal.current.expire + ar.EXPIRED_GRACE + 1
    async with Session() as session, session.begin():
        (await session.get(User, 2)).amount = 10000
    await ar.process_policy(100)
    assert await balance() == 10000 and (await policy()).state == "paused"


async def test_manual_guard_does_not_hide_runtime_error_from_original_handler(renewal):
    from app.services.auto_renew.guards import telegram_write_guard

    func = AsyncMock(side_effect=RuntimeError("original financial error"))
    event = SimpleNamespace(sender_id=2, data=b"ConfirmDelete:100", respond=AsyncMock())
    with pytest.raises(RuntimeError, match="original financial"):
        await telegram_write_guard(func)(event)


@pytest.mark.skipif(engine.dialect.name == "sqlite", reason="Real row locks required")
async def test_cancel_and_reserve_race_no_new_debit_after_cancel(renewal):
    await advance_notice(renewal)
    p = await policy()
    ident = {k: getattr(p, k) for k in ("panel_code", "panel_userid", "username", "panel_url")}
    results = await asyncio.gather(
        ar.disable(2, 100), ar._reserve(100, renewal.current, ident, renewal.clock[0]), return_exceptions=True
    )
    assert not any(isinstance(result, Exception) for result in results)
    assert (await policy()).state == "off"
    rows = await attempts()
    assert len(rows) <= 1
    after = await balance()
    assert after in (9000, 10000)
    assert await ar._reserve(100, renewal.current, ident, renewal.clock[0]) is None
    assert await balance() == after


async def test_old_panel_only_delete_preview_resolves_newly_enrolled_service(renewal, monkeypatch):
    from app.services.auto_renew.guards import telegram_write_guard
    from app.telegram import state

    await enable(renewal)
    monkeypatch.setattr(
        state, "get_data", AsyncMock(return_value='{"services":[{"code":null,"in_panel":10,"panel_userid":222}]}')
    )
    handler = AsyncMock()
    event = SimpleNamespace(sender_id=1, data=b"BulkDeleteConfirm:panel", respond=AsyncMock())
    await telegram_write_guard(handler)(event)
    handler.assert_not_awaited()


async def test_panel_address_changed_after_apply_requires_review_not_false_success(renewal, monkeypatch):
    await advance_notice(renewal)

    async def changed(panel, method, **kwargs):
        result = await renewal.call(panel, method, **kwargs)
        if method == "modify_user_by_id":
            async with Session() as session, session.begin():
                (await session.get(Panels, 10)).base_url = "https://replaced.invalid"
        return result

    monkeypatch.setattr(ar, "panel_call", changed)
    await ar.process_policy(100)
    assert (await attempts())[0].status == "review" and await balance() == 9000


async def test_paid_cleanup_cursor_moves_past_unavailable_oldest_service(renewal, monkeypatch):
    from app.services.auto_renew import cleanup

    cursor = [None]

    async def get(key):
        return cursor[0]

    async def set_(key, value, **kwargs):
        cursor[0] = value

    monkeypatch.setattr(cleanup, "get_redis", AsyncMock(return_value=SimpleNamespace(get=get, set=set_)))
    monkeypatch.setattr(cleanup, "PAGE_SIZE", 1)
    monkeypatch.setattr(cleanup, "BATCHES", 1)
    retire = AsyncMock(return_value=False)
    monkeypatch.setattr(cleanup, "retire_paid", retire)
    now = renewal.clock[0]
    async with Session() as session, session.begin():
        (await session.get(Service, 100)).expiration_time = now - 4 * 86400
        session.add(
            Service(
                code=101,
                id=2,
                username="old",
                in_panel=10,
                panel_userid=223,
                is_test=False,
                expiration_time=now - 4 * 86400,
            )
        )
    for _ in range(4):
        await cleanup.cleanup_paid([10], now)
    assert [call.args[0] for call in retire.await_args_list] == [100, 101, 100]


async def test_paid_cleanup_keeps_local_record_after_unconfirmed_panel_delete(renewal, monkeypatch):
    from app.services.auto_renew import cleanup

    now = renewal.clock[0]
    renewal.current.expire = now - 4 * 86400
    async with Session() as session, session.begin():
        (await session.get(Service, 100)).expiration_time = renewal.current.expire

    async def fail(panel, method, **kwargs):
        if method == "remove_user_by_id":
            raise TimeoutError()
        return renewal.current

    monkeypatch.setattr(ar, "panel_call", fail)
    with pytest.raises(TimeoutError):
        await cleanup.retire_paid(100, now)
    async with Session() as session:
        assert await session.get(Service, 100) is not None


async def test_disabled_plan_after_funding_still_recovers_exact_paid_target(renewal, monkeypatch):
    from app.services.auto_renew.plans import invalidate_renewals_for_plan

    await advance_notice(renewal)

    async def lost(panel, method, **kwargs):
        result = await renewal.call(panel, method, **kwargs)
        if method == "modify_user_by_id":
            raise TimeoutError()
        return result

    monkeypatch.setattr(ar, "panel_call", lost)
    await ar.process_policy(100)
    order = (await attempts())[0]
    async with Session() as session, session.begin():
        (await session.get(Plan, 11)).enabled = False
        await invalidate_renewals_for_plan(session, 11)
    await ar.reconcile(order.token)
    assert (await attempts())[0].status == "applied" and await balance() == 9000
    assert len(renewal.writes) == 1 and (await policy()).state == "paused"
