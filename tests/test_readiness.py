"""Readiness probes must fail closed without making any payment or Telegram RPC."""

import asyncio
import os
import stat
from unittest.mock import AsyncMock

import pytest

from app.services import readiness as r
from healthcheck import probe


async def test_dependencies_require_completed_startup_and_telegram_connection(db, monkeypatch):
    redis = AsyncMock(return_value=object())
    monkeypatch.setattr(r, "get_redis", redis)
    monkeypatch.setattr(r, "telegram_started", False)
    monkeypatch.setattr(r.Kenzo, "is_connected", lambda: True)
    assert not await r.dependencies_ready()
    redis.assert_not_called()
    monkeypatch.setattr(r, "telegram_started", True)
    assert await r.dependencies_ready()
    monkeypatch.setattr(r.Kenzo, "is_connected", lambda: False)
    assert not await r.dependencies_ready()


async def test_redis_and_sql_failures_cannot_report_ready(db, monkeypatch):
    monkeypatch.setattr(r, "telegram_started", True)
    monkeypatch.setattr(r.Kenzo, "is_connected", lambda: True)
    monkeypatch.setattr(r, "get_redis", AsyncMock(return_value=None))
    assert not await r.dependencies_ready()
    monkeypatch.setattr(r, "Session", lambda: (_ for _ in ()).throw(RuntimeError("synthetic SQL failure")))
    with pytest.raises(RuntimeError, match="SQL failure"):
        await r.dependencies_ready()


@pytest.mark.skipif(os.name == "nt", reason="Unix socket deployment")
async def test_private_socket_api_gate_dependency_failure_shutdown_and_reopen(tmp_path, monkeypatch):
    path = tmp_path / "ready.sock"
    gate = {"ready": False}
    check = AsyncMock(return_value=True)
    monkeypatch.setattr(r, "dependencies_ready", check)
    server = await r.ReadinessServer(lambda: gate["ready"], path).start()
    try:
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert not await asyncio.to_thread(probe, path)
        check.assert_not_awaited()
        gate["ready"] = True
        assert await asyncio.to_thread(probe, path)
        check.side_effect = RuntimeError("synthetic private credential must not appear in reply")
        assert not await asyncio.to_thread(probe, path)
        check.side_effect = None
        check.return_value = False
        assert not await asyncio.to_thread(probe, path)
    finally:
        await server.close()
    assert not path.exists() and not await asyncio.to_thread(probe, path)
    replacement = await r.ReadinessServer(lambda: True, path).start()
    await replacement.close()


@pytest.mark.skipif(os.name == "nt", reason="Unix socket deployment")
async def test_second_instance_cannot_remove_active_socket(tmp_path, monkeypatch):
    path = tmp_path / "ready.sock"
    monkeypatch.setattr(r, "dependencies_ready", AsyncMock(return_value=True))
    first = await r.ReadinessServer(lambda: True, path).start()
    try:
        with pytest.raises(BlockingIOError):
            await r.ReadinessServer(lambda: True, path).start()
        assert path.exists() and await asyncio.to_thread(probe, path)
    finally:
        await first.close()


@pytest.mark.skipif(os.name == "nt", reason="Unix socket deployment")
async def test_non_socket_path_and_lock_symlink_are_not_deleted(tmp_path):
    path = tmp_path / "ready.sock"
    path.write_text("preserve")
    with pytest.raises(RuntimeError, match="non-socket"):
        await r.ReadinessServer(lambda: True, path).start()
    assert path.read_text() == "preserve"
    lock = tmp_path / "ready.sock.lock"
    lock.unlink()
    lock.symlink_to(path)
    with pytest.raises(OSError):
        await r.ReadinessServer(lambda: True, path).start()
    assert path.read_text() == "preserve"


@pytest.mark.skipif(os.name == "nt", reason="Unix socket deployment")
async def test_stale_socket_and_timeout(tmp_path, monkeypatch):
    import socket

    path = tmp_path / "ready.sock"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stale:
        stale.bind(str(path))

    async def blocked():
        await asyncio.sleep(60)
        return True

    monkeypatch.setattr(r, "dependencies_ready", blocked)
    server = await r.ReadinessServer(lambda: True, path).start()
    try:
        assert not await asyncio.to_thread(probe, path, 0.1)
    finally:
        await server.close()
    assert not server.clients
