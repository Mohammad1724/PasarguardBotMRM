"""Private customer portal and the owner-only, audited wallet adjustment flow."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select
from telethon import TelegramClient, events
from telethon.tl.types import ReplyInlineMarkup
from test_admin_app import grant, publish, quote, signed, web as shared_web

from app.db.base import AsyncSessionLocal as Session, engine
from app.db.models.admin_app import AdminChange
from app.db.models.services import Service
from app.db.models.transaction import Transaction
from app.db.models.user import User
from app.services import customer_app as customer
from app.services.admin_app import auth, configuration as cfg
from app.telegram.user.customer_app import module

web = shared_web


async def customer_login(web, uid=2, *, raw=None):
    r = await web.client.post("/admin/account/api/auth", json={"init_data": raw or signed(uid)})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


async def wallet(web, amount="1500"):
    return await quote(web, "wallet", "2", {"amount": amount})


async def balance(uid=2):
    async with Session() as session:
        return (await session.get(User, uid)).amount


async def test_wallet_credit_debit_atomic_audit_and_replay(web):
    for amount, delta in [("1500", 1500), ("800", -700)]:
        draft = await wallet(web, amount)
        assert draft.status_code == 200
        token = draft.json()["token"]
        before = await balance()
        assert before == int(draft.json()["before"]["amount"])
        first, replay = await publish(web, token), await publish(web, token)
        assert first.status_code == replay.status_code == 200
        assert first.json()["replayed"] is False and replay.json()["replayed"] is True
        result = first.json()["result"]
        assert result == replay.json()["result"] and result["delta"] == str(delta)
        assert await balance() == int(amount)
        async with Session() as session:
            tx = await session.get(Transaction, int(result["transaction_id"]))
            audit = await session.get(AdminChange, token)
            assert tx.user_id == 2 and tx.amount == delta and tx.method == "admin_adjustment"
            assert tx.status == "approved" and tx.completed_at
            assert audit.actor_id == 1 and audit.reason == "Synthetic change" and audit.status == "published"
    async with Session() as session:
        assert await session.scalar(select(func.count()).select_from(Transaction)) == 2


@pytest.mark.parametrize(
    "value", ["-1", "", "1.5", "nan", "1e3", "۱۲۳", "9" * 20, str(2**63), True, 123, None, [], "0"]
)
async def test_wallet_invalid_values_never_write(web, value):
    r = await wallet(web, value)
    assert r.status_code == 422
    assert await balance() == 0


async def test_wallet_requires_explicit_reason_and_only_known_fields(web):
    doc = (await web.client.get("/admin/api/document/wallet/2", headers=web.owner)).json()
    body = {"entity": "wallet", "target": "2", "value": {"amount": "100"}, "version": doc["version"]}
    for reason in (None, "", "  "):
        r = await web.client.post("/admin/api/changes", headers=web.owner, json={**body, "reason": reason})
        assert r.status_code == 422
    for value in ({"amount": "100", "currency": "USD"}, {"amount": "100", "user_id": 3}):
        r = await quote(web, "wallet", "2", value)
        assert r.status_code == 422
    assert await balance() == 0


async def test_wallet_permission_not_inherited_from_user_or_finance_access(web):
    await grant(2, ["users.view", "users.manage", "finance.view", "payments.manage", "audit.view"])
    staff = await web.login(2)
    r = await web.client.get("/admin/api/document/wallet/3", headers=staff)
    assert r.status_code == 403
    draft = (await wallet(web)).json()
    r = await publish(web, draft["token"], staff)
    assert r.status_code == 404
    assert await balance() == 0


async def test_wallet_stale_balance_conflict_and_deleted_account(web):
    draft = (await wallet(web)).json()
    async with Session() as session, session.begin():
        (await session.get(User, 2)).amount = 700
    r = await publish(web, draft["token"])
    assert r.status_code == 409 and await balance() == 700
    async with Session() as session, session.begin():
        (await session.get(User, 2)).status = "DeleteAccount"
    assert (await wallet(web)).status_code == 422


async def test_wallet_no_automatic_undo_and_lossless_large_amount(web):
    draft = (await wallet(web, "9007199254740993")).json()
    assert (await publish(web, draft["token"])).status_code == 200
    doc = (await web.client.get("/admin/api/document/wallet/2", headers=web.owner)).json()
    assert doc["value"]["amount"] == "9007199254740993"
    r = await web.client.post(
        "/admin/api/changes/" + draft["token"] + "/restore", headers=web.owner, json={"confirm": True}
    )
    assert r.status_code == 422


async def test_wallet_database_failure_rolls_back_balance_ledger_and_audit(web, monkeypatch):
    draft = (await wallet(web)).json()
    original = cfg.apply

    async def fail(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("synthetic failure before commit")

    monkeypatch.setattr(cfg, "apply", fail)
    with pytest.raises(RuntimeError, match="synthetic failure"):
        await publish(web, draft["token"])
    assert await balance() == 0
    async with Session() as session:
        assert await session.scalar(select(func.count()).select_from(Transaction)) == 0
        assert (await session.get(AdminChange, draft["token"])).status == "draft"


@pytest.mark.skipif(engine.dialect.name == "sqlite", reason="Real row locks required")
async def test_wallet_parallel_publish_applies_once(web):
    draft = (await wallet(web)).json()
    results = await asyncio.gather(publish(web, draft["token"]), publish(web, draft["token"]))
    assert all(r.status_code == 200 for r in results)
    assert sorted(r.json()["replayed"] for r in results) == [False, True]
    assert await balance() == 1500
    async with Session() as session:
        assert await session.scalar(select(func.count()).select_from(Transaction)) == 1


async def test_customer_sees_only_own_balance_services_and_no_panel_secrets(web):
    headers = await customer_login(web)
    async with Session() as session, session.begin():
        (await session.get(User, 2)).amount = 9007199254740993
        session.add(Service(code=200, id=3, username="OTHER-CUSTOMER-SECRET", in_panel=10, panel_userid=333))
    me = await web.client.get("/admin/account/api/me?user_id=3", headers=headers)
    assert me.status_code == 200
    assert me.json()["user_id"] == "2" and me.json()["balance"] == "9007199254740993"
    assert me.json()["services_count"] == 1
    services = await web.client.get("/admin/account/api/services?user_id=3", headers=headers)
    assert [r["code"] for r in services.json()["items"]] == ["100"]
    assert (await web.client.get("/admin/account/api/services/100", headers=headers)).status_code == 200
    assert (await web.client.get("/admin/account/api/services/200", headers=headers)).status_code == 404
    text = json.dumps([me.json(), services.json()])
    for secret in (
        "OTHER-CUSTOMER-SECRET",
        "DO-NOT-LEAK",
        "COOKIE-SECRET",
        "MERCHANT-SECRET",
        "base_url",
        "panel_userid",
    ):
        assert secret not in text


async def test_customer_session_cannot_access_admin_even_for_owner(web):
    for uid in (1, 2):
        token = await customer_login(web, uid)
        for path in ("/admin/api/me", "/admin/api/list/users", "/admin/api/document/wallet/2"):
            assert (await web.client.get(path, headers=token)).status_code == 401
        r = await web.client.post(
            "/admin/api/changes", headers=token, json={"entity": "wallet", "target": "2", "value": {"amount": "900"}}
        )
        assert r.status_code == 401
    assert (await web.client.get("/admin/account/api/me", headers=web.owner)).status_code == 401


@pytest.mark.parametrize("state", ["ban", "BlockedBot", "DeleteAccount"])
async def test_customer_blocked_accounts_cannot_login_or_keep_session(web, state):
    token = await customer_login(web)
    async with Session() as session, session.begin():
        (await session.get(User, 2)).status = state
    assert (await web.client.get("/admin/account/api/me", headers=token)).status_code == 403
    assert (await web.client.post("/admin/account/api/auth", json={"init_data": signed(2)})).status_code == 403


async def test_customer_auth_rejects_forgery_expiry_wrong_origin_and_replay(web):
    raw = signed(2)
    assert (await web.client.post("/admin/account/api/auth", json={"init_data": raw + "x"})).status_code == 401
    assert (await web.client.post("/admin/account/api/auth", json={"init_data": signed(2, date=1)})).status_code == 401
    assert (
        await web.client.post(
            "/admin/account/api/auth", json={"init_data": raw}, headers={"Origin": "https://wrong.invalid"}
        )
    ).status_code == 403
    token = await customer_login(web, raw=raw)
    assert (await web.client.post("/admin/account/api/auth", json={"init_data": raw})).status_code == 401
    assert (await web.client.post("/admin/account/api/logout", headers=token)).status_code == 200
    assert (await web.client.get("/admin/account/api/me", headers=token)).status_code == 401
    assert (await web.client.post("/admin/account/api/auth", json={"init_data": signed(999)})).status_code == 403


async def test_customer_permission_failure_on_old_admin_link_does_not_consume_login(web):
    raw = signed(2)
    assert (await web.client.post("/admin/api/auth", json={"init_data": raw})).status_code == 403
    assert (await web.client.post("/admin/account/api/auth", json={"init_data": raw})).status_code == 200


async def test_customer_pagination_detail_bounds_and_transfer(web):
    token = await customer_login(web)
    async with Session() as session, session.begin():
        for code in range(300, 330):
            session.add(Service(code=code, id=2, username="mine-" + str(code)))
    a = await web.client.get("/admin/account/api/services", headers=token)
    b = await web.client.get("/admin/account/api/services?page=2", headers=token)
    assert a.json()["total"] == 31 and len(a.json()["items"]) == 25 and len(b.json()["items"]) == 6
    assert (await web.client.get("/admin/account/api/services?page=0", headers=token)).status_code == 422
    assert (await web.client.get("/admin/account/api/services/" + str(2**64), headers=token)).status_code == 422
    async with Session() as session, session.begin():
        (await session.get(Service, 100)).id = 3
    assert (await web.client.get("/admin/account/api/services/100", headers=token)).status_code == 404


async def test_customer_assets_readonly_api_csp_and_request_limits(web):
    for path in (
        "/admin/account",
        "/admin/account/",
        "/admin/account/assets/app.js",
        "/admin/account/assets/style.css",
    ):
        r = await web.client.get(path)
        assert r.status_code == 200
        assert "no-store" in r.headers["Cache-Control"] and "frame-ancestors" in r.headers["Content-Security-Policy"]
    assert (await web.client.get("/admin/account/assets/demo.js")).status_code == 404
    r = await web.client.post("/admin/account/api/auth", content=b"x" * 66000)
    assert r.status_code == 413
    token = await customer_login(web)
    for path in ("/admin/account/api/me", "/admin/account/api/services/100"):
        assert (await web.client.post(path, headers=token, json={})).status_code == 405
    assert (await web.client.get("/admin/account/api/me")).status_code == 401


async def test_customer_redis_outage_and_expired_session_fail_closed(web, monkeypatch):
    token = await customer_login(web)
    web.redis.data.clear()
    assert (await web.client.get("/admin/account/api/me", headers=token)).status_code == 401
    monkeypatch.setattr(auth, "get_redis", AsyncMock(return_value=None))
    assert (await web.client.post("/admin/account/api/auth", json={"init_data": signed(2)})).status_code == 503


async def test_customer_telegram_button_is_signed_inline_and_keeps_custom_port(web, monkeypatch):
    monkeypatch.setattr(auth, "ADMIN_MINI_APP_URL", "https://bot.invalid:8443/admin")
    e = SimpleNamespace(is_private=True, sender_id=2, raw_text=customer.CUSTOMER_APP_LABEL, respond=AsyncMock())
    assert module.matches(e)
    with pytest.raises(events.StopPropagation):
        await module.open_app(e)
    buttons = e.respond.call_args.kwargs["buttons"]
    markup = TelegramClient.build_reply_markup(buttons)
    assert isinstance(markup, ReplyInlineMarkup)
    assert buttons[0][0].url == "https://bot.invalid:8443/admin/account"
    assert "/admin/account" in customer.url()
    e.is_private = False
    assert not module.matches(e)


async def test_customer_home_entry_survives_old_custom_layout(web):
    from app.db.models.admin_app import AdminState
    from app.telegram.keyboards.home import bhome_buttons

    async with Session() as session, session.begin():
        (await session.get(AdminState, 1)).layout = [["bt.menu_my_services"], ["bt.menu_add_balance"]]
    rows = await bhome_buttons(2, "fa")
    assert customer.CUSTOMER_APP_LABEL in [b.text for row in rows.rows for b in row.buttons]


def test_customer_client_no_storage_and_same_origin_admin_fallback():
    root = Path(__file__).resolve().parents[1]
    js = (root / "app/assets/customer_app/app.js").read_text()
    assert "localStorage" not in js and "sessionStorage" not in js
    assert "/admin/account/api" in js and 'credentials: "omit"' in js
    admin = (root / "app/assets/admin_app/app.js").read_text()
    assert 'location.replace("/admin/account" + location.hash)' in admin
    assert "edit-wallet" in admin


async def test_owner_customer_portal_is_not_global_dashboard(web):
    headers = await customer_login(web, 1)
    result = (await web.client.get("/admin/account/api/me", headers=headers)).json()
    assert result["user_id"] == "1" and result["services_count"] == 0
    assert (await web.client.get("/admin/account/api/services", headers=headers)).json()["items"] == []


async def test_wallet_owner_authority_rechecked_before_publish(web, monkeypatch):
    draft = (await wallet(web)).json()
    monkeypatch.setattr(auth, "ADMIN_ID", [])
    assert (await publish(web, draft["token"])).status_code == 403
    assert await balance() == 0


async def test_customer_real_redis_nonce_is_single_use_and_session_expires(web, monkeypatch):
    import os
    import secrets

    import redis.asyncio

    address = os.getenv("TEST_REDIS_URL")
    if not address:
        pytest.skip("Explicit disposable Redis required")
    client = redis.asyncio.Redis.from_url(address, decode_responses=True)
    prefix = "portal-test:" + secrets.token_hex(8) + ":"
    monkeypatch.setattr(auth, "get_redis", AsyncMock(return_value=client))
    monkeypatch.setattr(auth, "PREFIX", prefix)
    monkeypatch.setattr(customer, "PREFIX", prefix + "customer:")
    try:
        raw = signed(2)
        requests = [web.client.post("/admin/account/api/auth", json={"init_data": raw}) for _ in range(2)]
        results = await asyncio.gather(*requests)
        assert sorted(r.status_code for r in results) == [200, 401]
        token = next(r for r in results if r.status_code == 200).json()["token"]
        keys = [k async for k in client.scan_iter(match=customer.PREFIX + "session:*")]
        assert len(keys) == 1 and 0 < await client.ttl(keys[0]) <= 1800
        headers = {"Authorization": "Bearer " + token}
        assert (await web.client.get("/admin/account/api/me", headers=headers)).status_code == 200
        await client.delete(keys[0])
        assert (await web.client.get("/admin/account/api/me", headers=headers)).status_code == 401
    finally:
        keys = [k async for k in client.scan_iter(match=prefix + "*")]
        if keys:
            await client.delete(*keys)
        await client.aclose()
