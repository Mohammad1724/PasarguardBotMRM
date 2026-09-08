"""Admin Mini App security and real database behavior; Telegram is synthetic."""

import asyncio
import hashlib
import hmac
import json
import secrets
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from urllib.parse import urlencode

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.db.base import AsyncSessionLocal as Session, engine
from app.db.crud.plans import PlanManager
from app.db.models.admin_app import AdminChange, AdminGrant, AdminState
from app.db.models.auto_renew import AutoRenewAttempt, AutoRenewPolicy
from app.db.models.customer_experience import SupportTicket, TicketMessage
from app.db.models.panels import Panels
from app.db.models.plans import Plan
from app.db.models.services import Service
from app.db.models.settings import Settings, resolve_settings_update_kwargs
from app.db.models.user import User
from app.routers import api_app
from app.services.admin_app import auth, configuration as cfg


class MemoryRedis:
    def __init__(self):
        self.data = {}
        self.counters = {}

    async def set(self, key, value, *, nx=False, ex=None):
        if nx and key in self.data:
            return False
        self.data[key] = value
        return True

    async def get(self, key):
        return self.data.get(key)

    async def delete(self, key):
        return self.data.pop(key, None)

    async def eval(self, script, n, key):
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]


def signed(uid=1, *, date=None, **extra):
    data = {
        "user": json.dumps({"id": uid, "first_name": "Synthetic"}, ensure_ascii=False, separators=(",", ":")),
        "auth_date": str(int(time.time()) if date is None else date),
        "query_id": secrets.token_hex(10),
        **extra,
    }
    check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", auth.BOT_TOKEN.encode(), hashlib.sha256).digest()
    data["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(data)


@pytest.fixture
async def web(users, monkeypatch):
    redis = MemoryRedis()

    async def get_redis():
        return redis

    monkeypatch.setattr(auth, "get_redis", get_redis)
    monkeypatch.setattr(auth, "ADMIN_MINI_APP_URL", "https://bot.invalid/admin")
    async with Session() as session, session.begin():
        session.add(AdminState(id=1, layout=[]))
        session.add(
            Panels(
                code=10,
                name="Synthetic",
                base_url="https://panel.invalid",
                username="private",
                password="DO-NOT-LEAK",
                cookie="COOKIE-SECRET",
            )
        )
        session.add(
            Plan(
                id=1,
                price=1000,
                storage=10,
                duration=30,
                panel_code=10,
                plan_type="volume",
                data_limit_reset_strategy="no_reset",
                ip_limit=1,
            )
        )
        session.add(
            Service(
                code=100,
                id=2,
                username="service_100",
                in_panel=10,
                panel_userid=222,
                is_test=False,
                expiration_time=1234,
            )
        )
        session.add(SupportTicket(id=1, user_id=2, topic="connection", status="open", created_at=1, updated_at=1))
        session.add(
            TicketMessage(
                ticket_id=1,
                author_id=2,
                kind="customer",
                text="Private ticket text",
                file_id="PRIVATE-FILE-REF",
                created_at=1,
            )
        )
        settings = await session.scalar(select(Settings))
        for k, v in resolve_settings_update_kwargs(settings, zarinpal_merchant="MERCHANT-SECRET").items():
            setattr(settings, k, v)
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api_app),
        base_url="https://bot.invalid",
        headers={"Origin": "https://bot.invalid"},
    )

    async def login(uid=1, raw=None):
        response = await client.post("/admin/api/auth", json={"init_data": raw or signed(uid)})
        assert response.status_code == 200, response.text
        return {"Authorization": "Bearer " + response.json()["token"]}

    owner = await login()
    yield SimpleNamespace(client=client, redis=redis, owner=owner, login=login)
    await client.aclose()


async def grant(uid, permissions):
    async with Session() as session, session.begin():
        row = await session.get(AdminGrant, uid)
        if not row:
            row = AdminGrant(user_id=uid, name="Limited", updated_at=1)
            session.add(row)
        row.permissions = permissions


async def quote(web, entity, target, value, *, headers=None):
    headers = headers or web.owner
    current = await web.client.get(f"/admin/api/document/{entity}/{target}", headers=headers)
    assert current.status_code == 200, current.text
    return await web.client.post(
        "/admin/api/changes",
        headers=headers,
        json={
            "entity": entity,
            "target": str(target),
            "value": value,
            "version": current.json()["version"],
            "reason": "Synthetic change",
        },
    )


async def publish(web, token, headers=None):
    return await web.client.post(
        f"/admin/api/changes/{token}/publish", headers=headers or web.owner, json={"confirm": True}
    )


def test_signed_telegram_data_valid_and_no_unsigned_user_override():
    assert auth.validate_init_data(signed(123))[0] == 123
    with pytest.raises(HTTPException):
        auth.validate_init_data(signed(123).replace("123", "124"))


@pytest.mark.parametrize("date", [-301, 31, -10000])
def test_stale_or_future_init_data(date):
    with pytest.raises(HTTPException):
        auth.validate_init_data(signed(date=int(time.time()) + date))


@pytest.mark.parametrize("raw", [None, "", "user=1&hash=wrong", "not-a-query", "x" * 8193])
def test_invalid_init_data(raw):
    with pytest.raises(HTTPException):
        auth.validate_init_data(raw)


def test_duplicate_signed_fields_rejected():
    with pytest.raises(HTTPException):
        auth.validate_init_data(signed() + "&auth_date=1")


async def test_unauthorized_no_session_and_feature_default_off(web, monkeypatch):
    r = await web.client.get("/admin/api/list/users")
    assert r.status_code == 401
    monkeypatch.setattr(auth, "ADMIN_MINI_APP_URL", "")
    r = await web.client.get("/admin/api/me", headers=web.owner)
    assert r.status_code == 503


async def test_replayed_login_rejected_and_logout_invalidates_session(web):
    raw = signed()
    headers = await web.login(raw=raw)
    r = await web.client.post("/admin/api/auth", json={"init_data": raw})
    assert r.status_code == 401
    await web.client.post("/admin/api/logout", headers=headers, json={})
    assert (await web.client.get("/admin/api/me", headers=headers)).status_code == 401


async def test_role_change_and_ban_invalidate_live_access(web):
    await grant(3, ["tickets.view"])
    headers = await web.login(3)
    assert (await web.client.get("/admin/api/list/tickets", headers=headers)).status_code == 200
    assert (await web.client.get("/admin/api/list/finance", headers=headers)).status_code == 403
    await grant(3, [])
    assert (await web.client.get("/admin/api/me", headers=headers)).status_code == 403
    await grant(3, ["tickets.view"])
    async with Session() as session, session.begin():
        (await session.get(User, 3)).status = "ban"
    assert (await web.client.get("/admin/api/me", headers=headers)).status_code == 403


async def test_unknown_user_cannot_login(web):
    r = await web.client.post("/admin/api/auth", json={"init_data": signed(2)})
    assert r.status_code == 403


async def test_cross_origin_and_bearer_not_cookie(web):
    r = await web.client.post("/admin/api/logout", json={}, headers={**web.owner, "Origin": "https://evil.invalid"})
    assert r.status_code == 403
    r = await web.client.get("/admin/api/me", headers={"Cookie": "token=" + web.owner["Authorization"][7:]})
    assert r.status_code == 401


async def test_rate_limit_and_redis_outage_fail_closed(web, monkeypatch):
    web.redis.counters[auth.PREFIX + "requests:1"] = 180
    assert (await web.client.get("/admin/api/me", headers=web.owner)).status_code == 429

    async def no_redis():
        return None

    monkeypatch.setattr(auth, "get_redis", no_redis)
    assert (await web.client.get("/admin/api/me", headers=web.owner)).status_code == 503


async def test_secrets_and_telegram_file_references_not_exposed(web):
    for path in ["/me", "/list/panels", "/document/settings/payment_settings", "/tickets/1"]:
        r = await web.client.get("/admin/api" + path, headers=web.owner)
        assert r.status_code == 200, r.text
        assert not any(s in r.text for s in ("MERCHANT-SECRET", "COOKIE-SECRET", "DO-NOT-LEAK", "PRIVATE-FILE-REF"))


async def test_body_limit_unknown_fields_and_malformed_json(web):
    r = await web.client.post("/admin/api/changes", headers=web.owner, content=b"x" * 65537)
    assert r.status_code == 413
    r = await web.client.post("/admin/api/changes", headers=web.owner, content=b"bad json")
    assert r.status_code == 422
    r = await quote(web, "settings", "payment_settings", {"zarinpal_merchant": "attacker"})
    assert r.status_code == 422


async def test_preview_does_not_modify_and_publish_is_idempotent(web):
    r = await quote(web, "plans", "1", {"price": 2000})
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    async with Session() as session:
        assert (await session.get(Plan, 1)).price == 1000
    assert (await publish(web, token)).status_code == 200
    r = await publish(web, token)
    assert r.status_code == 200 and r.json()["replayed"]
    async with Session() as session:
        assert (await session.get(Plan, 1)).price == 2000
        assert (
            await session.scalar(select(func.count()).select_from(AdminChange).where(AdminChange.status == "published"))
            == 1
        )


async def test_expired_discarded_and_other_admin_drafts_rejected(web):
    r = await quote(web, "plans", "1", {"price": 2000})
    token = r.json()["token"]
    await grant(3, ["plans.manage"])
    limited = await web.login(3)
    assert (await publish(web, token, limited)).status_code == 404
    async with Session() as session, session.begin():
        (await session.get(AdminChange, token)).created_at = 1
    assert (await publish(web, token)).status_code == 409
    r = await quote(web, "plans", "1", {"price": 2100})
    token = r.json()["token"]
    await web.client.post(f"/admin/api/changes/{token}/discard", headers=web.owner, json={"confirm": True})
    assert (await publish(web, token)).status_code == 409


async def test_concurrent_external_change_conflicts_not_lost_update(web):
    r = await quote(web, "plans", "1", {"price": 2000})
    token = r.json()["token"]
    await PlanManager().update_plan(1, new_storage=20)
    assert (await publish(web, token)).status_code == 409
    async with Session() as session:
        p = await session.get(Plan, 1)
        assert p.price == 1000 and p.storage == 20


async def test_publish_rechecks_revoked_permission(web):
    await grant(3, ["plans.manage"])
    limited = await web.login(3)
    r = await quote(web, "plans", "1", {"price": 2000}, headers=limited)
    assert r.status_code == 200
    await grant(3, ["tickets.view"])
    assert (await publish(web, r.json()["token"], limited)).status_code == 403


@pytest.mark.parametrize(
    "bad",
    [
        {"price": -1},
        {"price": 1.2},
        {"price": True},
        {"duration": -1},
        {"storage": -2},
        {"ip_limit": 10001},
        {"panel_code": 11},
        {"button_style": "#123456"},
        {"button_icon": "9223372036854775808"},
        {"enabled": "false"},
        {"bot_token": "secret"},
    ],
)
async def test_plan_validation(web, bad):
    assert (await quote(web, "plans", "1", bad)).status_code == 422


async def test_disabled_plan_hidden_and_recurring_new_consent_rejected(web):
    r = await quote(web, "plans", "1", {"enabled": False})
    assert (await publish(web, r.json()["token"])).status_code == 200
    assert await PlanManager().get_plan(1) is None
    assert await PlanManager().get_all_plans(10) == []
    from app.services.auto_renew.service import plan_snapshot

    async with Session() as session:
        with pytest.raises(ValueError):
            plan_snapshot(await session.get(Plan, 1))


async def test_plan_create_and_restore_do_not_replay_transactions(web):
    data = {"price": 2000, "storage": 20, "duration": 30, "ip_limit": 2, "panel_code": 10}
    r = await quote(web, "plans", "new", data)
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    a = await publish(web, token)
    b = await publish(web, token)
    assert a.status_code == b.status_code == 200 and a.json()["result"]["id"] == b.json()["result"]["id"]
    r = await quote(web, "plans", "1", {"price": 3000})
    token = r.json()["token"]
    await publish(web, token)
    r = await web.client.post(f"/admin/api/changes/{token}/restore", headers=web.owner, json={"confirm": True})
    assert r.status_code == 200, r.text
    await publish(web, r.json()["token"])
    async with Session() as session:
        assert (await session.get(Plan, 1)).price == 1000


@pytest.mark.parametrize(
    "rows",
    [
        [["bt.menu_support"]],
        [["bt.menu_admin_panel"]],
        [["bt.menu_my_services", "bt.menu_my_services"]],
        [["unregistered"]],
        [["bt.menu_my_services", "bt.menu_add_balance", "bt.menu_support", "bt.menu_help"]],
    ],
)
async def test_layout_invalid_or_privilege_escalating(web, rows):
    assert (await quote(web, "layout", "home", {"rows": rows})).status_code == 422


async def test_layout_only_reorders_existing_authorized_buttons(web):
    from telethon.tl.types import KeyboardButton as Button

    from app.services.admin_app.layout import arrange_home

    rows = [["bt.menu_add_balance"], ["bt.menu_my_services", "bt.menu_get_trial"]]
    r = await quote(web, "layout", "home", {"rows": rows})
    await publish(web, r.json()["token"])
    # Trial absent from old eligibility builder: layout cannot resurrect it.
    result = await arrange_home(
        [
            [Button(cfg.KEYBOARD_BUTTON_DEFAULTS["bt.menu_my_services"])],
            [Button(cfg.KEYBOARD_BUTTON_DEFAULTS["bt.menu_add_balance"])],
            [Button(cfg.KEYBOARD_BUTTON_DEFAULTS["bt.menu_admin_panel"])],
        ]
    )
    assert [b.text for row in result for b in row] == [
        cfg.KEYBOARD_BUTTON_DEFAULTS[k] for k in ["bt.menu_add_balance", "bt.menu_my_services", "bt.menu_admin_panel"]
    ]


async def test_button_style_and_icon_can_be_explicitly_cleared(web):
    from app.db.crud.keyboards import KeyboardButtonCRUD
    from app.telegram.keyboards.common import _get_keyboard_button_config

    r = await quote(web, "buttons", "bt.menu_buy_service", {"text": "خرید جدید", "style": "success", "icon": None})
    assert r.status_code == 200, r.text
    await publish(web, r.json()["token"])
    text, style = await _get_keyboard_button_config(
        KeyboardButtonCRUD(), "bt.menu_buy_service", "fallback", default_style="primary", default_icon=123456
    )
    assert text == "خرید جدید" and style.bg_success and style.icon is None


async def test_duplicate_button_and_unsafe_template_rejected(web):
    r = await quote(
        web, "buttons", "bt.menu_buy_service", {"text": cfg.KEYBOARD_BUTTON_DEFAULTS["bt.menu_my_services"]}
    )
    assert r.status_code == 422
    r = await quote(web, "texts", "start_message", {"text": "{user.__class__}"})
    assert r.status_code == 422


async def test_user_manager_cannot_change_wallet_or_ban_owner(web):
    await grant(3, ["users.view", "users.manage"])
    limited = await web.login(3)
    r = await quote(web, "users", "2", {"amount": 100000}, headers=limited)
    assert r.status_code == 422
    r = await quote(web, "users", "1", {"status": "ban"}, headers=limited)
    assert r.status_code == 422
    r = await web.client.get("/admin/api/list/users", headers=limited)
    assert "amount" not in r.json()["items"][0]
    r = await quote(web, "users", "2", {"status": "ban"}, headers=limited)
    await publish(web, r.json()["token"], limited)
    async with Session() as session:
        assert (await session.get(User, 2)).status == "ban"


async def test_owner_only_grants_and_permissions_normalized(web):
    await grant(3, ["tickets.view"])
    limited = await web.login(3)
    assert (await web.client.get("/admin/api/list/grants", headers=limited)).status_code == 403
    r = await quote(web, "grants", "3", {"name": "Support", "permissions": ["tickets.manage"]})
    assert r.status_code == 200
    await publish(web, r.json()["token"])
    async with Session() as session:
        assert (await session.get(AdminGrant, 3)).permissions == ["tickets.manage", "tickets.view"]
    r = await quote(web, "grants", "3", {"name": "Root", "permissions": ["owner"]})
    assert r.status_code == 422


async def test_ticket_reply_is_atomic_idempotent_and_audited(web):
    await grant(3, ["tickets.view", "tickets.manage"])
    limited = await web.login(3)
    r = await quote(
        web, "tickets", "1", {"reply": "Test reply", "status": "waiting", "assign_me": True}, headers=limited
    )
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    await publish(web, token, limited)
    await publish(web, token, limited)
    async with Session() as session:
        assert (
            await session.scalar(select(func.count()).select_from(TicketMessage).where(TicketMessage.kind == "staff"))
            == 1
        )
        row = await session.get(SupportTicket, 1)
        assert row.status == "waiting" and row.assigned_to == 3
        assert (await session.get(AdminChange, token)).status == "published"


async def test_ticket_customer_message_after_preview_conflicts(web):
    r = await quote(web, "tickets", "1", {"status": "closed"})
    async with Session() as session, session.begin():
        session.add(TicketMessage(ticket_id=1, author_id=2, kind="customer", text="New message", created_at=2))
    assert (await publish(web, r.json()["token"])).status_code == 409


async def test_support_and_auditor_do_not_get_finance_settings_or_private_audit(web):
    r = await quote(web, "tickets", "1", {"reply": "Private reply"})
    token = r.json()["token"]
    await publish(web, token)
    await grant(3, ["audit.view"])
    headers = await web.login(3)
    assert (await web.client.get("/admin/api/list/audit", headers=headers)).status_code == 200
    assert (await web.client.get("/admin/api/audit/" + token, headers=headers)).status_code == 403
    assert (await web.client.get("/admin/api/document/settings/payment_settings", headers=headers)).status_code == 403


async def test_cancel_auto_renew_keeps_paid_attempt_and_wallet(web, monkeypatch):
    from app.services import locks as locking

    @asynccontextmanager
    async def unlocked(*args, **kwargs):
        yield

    monkeypatch.setattr(locking, "distributed_lock", unlocked)
    async with Session() as session, session.begin():
        session.add(
            AutoRenewPolicy(
                service_code=100,
                user_id=2,
                panel_code=10,
                panel_userid=222,
                username="service_100",
                panel_url="https://panel.invalid",
                plan_id=1,
                plan_snapshot={},
                expected_values={},
                per_charge_cap=1000,
                monthly_cap=1000,
                revision=1,
                state="enabled",
                reason="",
                next_check_at=1,
                created_at=1,
                updated_at=1,
            )
        )
        session.add(
            AutoRenewAttempt(
                token="a" * 32,
                service_code=100,
                active_service_code=100,
                user_id=2,
                policy_revision=1,
                cycle_expire=1,
                identity={},
                price=1000,
                month="2026-09",
                old_values={},
                target_values={},
                status="review",
                attempted=True,
                retry_count=1,
                next_retry_at=1,
                error="",
                created_at=1,
            )
        )
    r = await quote(web, "services", "100", {"state": "off"})
    assert r.status_code == 200, r.text
    r = await publish(web, r.json()["token"])
    assert r.status_code == 200, r.text
    async with Session() as session:
        assert (await session.get(AutoRenewPolicy, 100)).state == "off"
        assert (await session.get(AutoRenewAttempt, "a" * 32)).status == "review"
        assert (await session.get(User, 2)).amount == 0


async def test_headers_pagination_and_demo_not_exposed(web):
    r = await web.client.get("/admin")
    assert r.status_code == 200
    assert "frame-ancestors" in r.headers["Content-Security-Policy"] and r.headers["Cache-Control"] == "no-store"
    assert "demo.js" not in r.text
    assert (await web.client.get("/admin/assets/demo.js")).status_code == 404
    assert (await web.client.get("/admin/api/list/users?page=0", headers=web.owner)).status_code == 422
    r = await web.client.get("/admin/api/list/services?q=%25", headers=web.owner)
    assert r.status_code == 200 and r.json()["total"] == 0


@pytest.mark.skipif(engine.dialect.name == "sqlite", reason="Real row locks required")
async def test_two_simultaneous_publications_only_one_plan_created(web):
    r = await quote(web, "plans", "new", {"price": 1000, "storage": 10, "duration": 30, "panel_code": 10})
    token = r.json()["token"]
    result = await asyncio.gather(publish(web, token), publish(web, token))
    assert [r.status_code for r in result] == [200, 200]
    assert sorted(r.json()["replayed"] for r in result) == [False, True]
    async with Session() as session:
        assert await session.scalar(select(func.count()).select_from(Plan)) == 2


async def test_appearance_role_cannot_rewrite_payment_confirmation(web):
    await grant(3, ["appearance.manage"])
    limited = await web.login(3)
    r = await quote(web, "buttons", "in.buy.confirm", {"text": "خروج"}, headers=limited)
    assert r.status_code == 403
    r = await quote(web, "texts", "start_message", {"text": "Welcome"}, headers=limited)
    assert r.status_code == 200


async def test_first_custom_text_can_restore_absent_override(web):
    from app.db.models.bot_text import BotText

    r = await quote(web, "texts", "start_message", {"text": "Welcome"})
    token = r.json()["token"]
    await publish(web, token)
    r = await web.client.post(f"/admin/api/changes/{token}/restore", headers=web.owner, json={"confirm": True})
    assert r.status_code == 200, r.text
    assert (await publish(web, r.json()["token"])).status_code == 200
    async with Session() as session:
        assert await session.scalar(select(BotText).where(BotText.key == "start_message")) is None


async def test_commit_failure_rolls_back_change_and_audit(web, monkeypatch):
    r = await quote(web, "plans", "1", {"price": 2000})
    token = r.json()["token"]
    original = cfg.apply

    async def interrupted(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("synthetic commit interruption")

    monkeypatch.setattr(cfg, "apply", interrupted)
    with pytest.raises(RuntimeError):
        await publish(web, token)
    async with Session() as session:
        assert (await session.get(Plan, 1)).price == 1000
        assert (await session.get(AdminChange, token)).status == "draft"


async def test_disabled_plan_remains_editable_by_legacy_admin(web):
    async with Session() as session, session.begin():
        (await session.get(Plan, 1)).enabled = False
    assert await PlanManager().get_plan(1) is None
    assert (await PlanManager().get_plan(1, enabled_only=False)).enabled is False


async def test_initial_consent_json_requires_boolean_confirmation(web):
    r = await quote(web, "plans", "1", {"price": 2000})
    token = r.json()["token"]
    r = await web.client.post(f"/admin/api/changes/{token}/publish", headers=web.owner, json={"confirm": 1})
    assert r.status_code == 422


async def test_plan_term_edits_pause_old_consent_even_after_price_restored(web):
    async with Session() as session, session.begin():
        session.add(
            AutoRenewPolicy(
                service_code=100,
                user_id=2,
                panel_code=10,
                panel_userid=222,
                username="service_100",
                panel_url="https://panel.invalid",
                plan_id=1,
                plan_snapshot={},
                expected_values={},
                per_charge_cap=1000,
                monthly_cap=1000,
                revision=1,
                state="enabled",
                reason="",
                next_check_at=1,
                created_at=1,
                updated_at=1,
            )
        )
    r = await quote(web, "plans", "1", {"price": 2000})
    res = await publish(web, r.json()["token"])
    assert res.json()["result"]["paused_renewals"] == 1
    r = await quote(web, "plans", "1", {"price": 1000})
    await publish(web, r.json()["token"])
    async with Session() as session:
        p = await session.get(AutoRenewPolicy, 100)
        assert p.state == "paused" and p.revision == 2


async def test_wallet_totals_do_not_lose_precision_in_browser_json(web):
    async with Session() as session, session.begin():
        (await session.get(User, 2)).amount = 9007199254740993
    r = await web.client.get("/admin/api/dashboard", headers=web.owner)
    assert r.json()["counts"]["wallet_liability"] == "9007199254740993"


@pytest.mark.skipif(not __import__("os").getenv("TEST_REDIS_URL"), reason="Real Redis required")
async def test_real_redis_one_time_login_and_logout(web, monkeypatch):
    import os

    from redis.asyncio import Redis

    redis = Redis.from_url(os.environ["TEST_REDIS_URL"], decode_responses=True)

    async def get_real():
        return redis

    monkeypatch.setattr(auth, "get_redis", get_real)
    raw = signed()
    results = await asyncio.gather(
        web.client.post("/admin/api/auth", json={"init_data": raw}),
        web.client.post("/admin/api/auth", json={"init_data": raw}),
    )
    assert sorted(r.status_code for r in results) == [200, 401]
    response = next(r for r in results if r.status_code == 200)
    headers = {"Authorization": "Bearer " + response.json()["token"]}
    assert (await web.client.get("/admin/api/me", headers=headers)).status_code == 200
    await web.client.post("/admin/api/logout", headers=headers, json={})
    assert (await web.client.get("/admin/api/me", headers=headers)).status_code == 401
    await redis.aclose()


async def test_revoked_and_regranted_role_never_revives_old_session(web):
    await grant(3, ["tickets.view"])
    old = await web.login(3)
    for permissions in ([], ["tickets.view"]):
        r = await quote(web, "grants", "3", {"name": "Limited", "permissions": permissions})
        assert r.status_code == 200
        assert (await publish(web, r.json()["token"])).status_code == 200
    assert (await web.client.get("/admin/api/me", headers=old)).status_code == 401
    fresh = await web.login(3)
    assert (await web.client.get("/admin/api/me", headers=fresh)).status_code == 200


async def test_disabled_plan_retained_for_historical_display_not_legacy_checkout(web):
    async with Session() as session, session.begin():
        (await session.get(Plan, 1)).enabled = False
    assert await PlanManager().get_plan_by_volume_for_display(10, 10) is not None
    assert await PlanManager().get_plan_by_volume_for_display(10, 10, enabled_only=True) is None


async def test_inflight_write_rechecks_session_generation_inside_transaction(web):
    await grant(3, ["plans.manage"])
    limited = await web.login(3)
    actor = (await web.client.get("/admin/api/me", headers=limited)).json()["actor"]
    q = await quote(web, "plans", "1", {"price": 2000}, headers=limited)
    r = await quote(web, "grants", "3", {"name": "Limited", "permissions": ["plans.manage"]})
    await publish(web, r.json()["token"])
    from app.services.admin_app.service import publish as publish_inflight

    with pytest.raises(HTTPException) as exc:
        await publish_inflight(actor, q.json()["token"])
    assert exc.value.status_code == 401
    async with Session() as session:
        assert (await session.get(Plan, 1)).price == 1000
