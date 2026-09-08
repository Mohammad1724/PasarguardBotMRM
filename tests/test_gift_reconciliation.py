from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.db.crud.gift_codes import GiftCodeCRUD
from app.db.crud.services import ServiceCRUD
from app.services import gifts, locks


@asynccontextmanager
async def fake_lock(*args, **kwargs):
    yield


async def test_panel_timeout_reconciles_without_second_add(users, monkeypatch):
    gift = await GiftCodeCRUD().create("VOLUME", "volume", 1)
    panel = SimpleNamespace(code=1)
    service = SimpleNamespace(code=99, panel_userid=222)
    current = SimpleNamespace(data_limit=1024**3)
    writes = []

    async def call(panel, method, **kwargs):
        if method == "get_user_by_id":
            return current
        writes.append(kwargs["user"].data_limit)
        current.data_limit = kwargs["user"].data_limit
        raise TimeoutError("Response lost after the panel applied the update")

    monkeypatch.setattr(locks, "distributed_lock", fake_lock)
    monkeypatch.setattr(gifts, "panel_call", call)
    monkeypatch.setattr(ServiceCRUD, "update_service", AsyncMock(return_value=(True, "ok")))
    with pytest.raises(TimeoutError):
        await gifts.apply_service_gift(gift, service, panel, 2, "a" * 32)
    assert (await gifts.get_use("a" * 32)).status == "applying"
    assert (await gifts.apply_service_gift(gift, service, panel, 2, "a" * 32)).status == "applied"
    assert writes == [2 * 1024**3]
    assert (await GiftCodeCRUD().get_by_code("VOLUME")).times_used == 1


async def test_gift_sync_failure_does_not_reapply_panel(users, monkeypatch):
    gift = await GiftCodeCRUD().create("VOLUME", "volume", 1)
    service = SimpleNamespace(code=99, panel_userid=222)
    current = SimpleNamespace(data_limit=1024**3)
    writes = []

    async def call(panel, method, **kwargs):
        if method == "get_user_by_id":
            return current
        current.data_limit = kwargs["user"].data_limit
        writes.append(current.data_limit)
        return None

    monkeypatch.setattr(locks, "distributed_lock", fake_lock)
    monkeypatch.setattr(gifts, "panel_call", call)
    monkeypatch.setattr(ServiceCRUD, "update_service", AsyncMock(side_effect=[(False, "db down"), (True, "ok")]))
    with pytest.raises(RuntimeError, match="sync"):
        await gifts.apply_service_gift(gift, service, None, 2, "b" * 32)
    await gifts.apply_service_gift(gift, service, None, 2, "b" * 32)
    assert len(writes) == 1


async def test_admin_auto_code_is_not_awaited(monkeypatch):
    from telethon import events

    from app.telegram.admin.gift_codes import messages

    data = {"gift_type": "balance", "gift_value": 100, "gift_uses": 1, "gift_per_user": 1}
    monkeypatch.setattr(messages, "get_step", AsyncMock(return_value="gift_note"))
    monkeypatch.setattr(messages, "get_data", AsyncMock(side_effect=lambda user, key: data.get(key)))
    for name in ("set_data", "clear_user", "set_step"):
        monkeypatch.setattr(messages, name, AsyncMock())
    monkeypatch.setattr(messages.service, "create_gift_with_log", AsyncMock(return_value="CREATED"))
    event = SimpleNamespace(sender_id=1, message=SimpleNamespace(text="-"), respond=AsyncMock())
    with pytest.raises(events.StopPropagation):
        await messages.message_handler_gift_admin(event)
    messages.service.create_gift_with_log.assert_awaited_once()
    assert len(messages.service.create_gift_with_log.call_args.kwargs["code"]) == 8
