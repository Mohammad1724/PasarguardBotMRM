import asyncio
import io
import os
import zipfile
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from app.db import redis as redis_module
from app.routers import webhook
from app.routers.webhook import processor
from app.services import locks, restore
from app.utils.security import secrets_cache


async def test_redis_failed_client_not_cached(monkeypatch):
    import redis.asyncio

    bad = SimpleNamespace(ping=AsyncMock(side_effect=OSError("offline")), aclose=AsyncMock())
    good = SimpleNamespace(ping=AsyncMock(return_value=True), aclose=AsyncMock())
    clients = iter([bad, good])
    monkeypatch.setattr(redis_module, "redis_client", None)
    monkeypatch.setattr(redis.asyncio.Redis, "from_url", lambda *args, **kwargs: next(clients))
    assert await redis_module.get_redis() is None
    assert redis_module.redis_client is None
    bad.aclose.assert_awaited_once()
    assert await redis_module.get_redis() is good


async def test_distributed_lock_refuses_without_redis(monkeypatch):
    monkeypatch.setattr(locks, "get_redis", AsyncMock(return_value=None))
    with pytest.raises(RuntimeError, match="unavailable"):
        async with locks.distributed_lock("test"):
            pytest.fail("Must not enter")


async def test_lock_release_checks_owner(monkeypatch):
    client = SimpleNamespace(set=AsyncMock(return_value=True), eval=AsyncMock(return_value=1))
    monkeypatch.setattr(locks, "get_redis", AsyncMock(return_value=client))
    async with locks.distributed_lock("test"):
        pass
    args = client.eval.call_args.args
    assert "get" in args[0] and "del" in args[0]
    assert args[-1] == client.set.call_args.args[1]


async def test_restore_live_process_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(restore, "_drop_all_tables", AsyncMock())
    result = await restore.restore_from_zip(tmp_path / "anything.zip")
    assert not result.ok
    restore._drop_all_tables.assert_not_awaited()


@pytest.mark.parametrize("filename", ["../outside", "/outside", "nested/file", "unexpected"])
def test_restore_zip_rejects_unsafe_entries(tmp_path, filename):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr(filename, "data")
    data.seek(0)
    with zipfile.ZipFile(data) as archive, pytest.raises(ValueError):
        restore._safe_extractall(archive, tmp_path)


def test_atomic_env_failure_preserves_original(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("original")

    def fail(*args):
        raise OSError("bind mount")

    monkeypatch.setattr(restore.os, "replace", fail)
    with pytest.raises(OSError):
        restore._atomic_write_text(path, "new")
    assert path.read_text() == "original"
    assert list(tmp_path.glob("*.tmp")) == []


async def test_sql_import_uses_backpressure(monkeypatch, tmp_path):
    sql = tmp_path / "database.sql"
    sql.write_bytes(b"a" * (2 * 1024**2 + 1))
    writes = []

    class Writer:
        def write(self, chunk):
            writes.append(len(chunk))

        drain = AsyncMock()

        def close(self):
            pass

    writer = Writer()
    process = SimpleNamespace(
        stdin=writer, stderr=SimpleNamespace(read=AsyncMock(return_value=b"")), returncode=0, wait=AsyncMock()
    )
    monkeypatch.setattr(restore, "build_mysql_cmd_args", lambda *a, **k: (["synthetic"], {}))
    monkeypatch.setattr(restore.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    await restore._import_sql(None, sql)
    assert len(writes) == writer.drain.await_count == 3


@pytest.mark.parametrize("payload,status", [(None, 400), ([], 400), ([123], 400), ([{}] * 101, 400), ({}, 400)])
async def test_webhook_invalid_requests(payload, status, monkeypatch):
    secrets_cache._cache["webhook_secret"] = "test-secret"
    app = FastAPI()
    app.include_router(webhook.webhook_router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/webhook", json=payload, headers={"x-webhook-secret": "test-secret"})
    assert response.status_code == status


async def test_webhook_failures_return_retryable_status(monkeypatch):
    secrets_cache._cache["webhook_secret"] = "test-secret"
    monkeypatch.setattr(webhook, "process_webhook_events", AsyncMock(side_effect=RuntimeError("synthetic")))
    app = FastAPI()
    app.include_router(webhook.webhook_router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/webhook", json={"action": "user_created"}, headers={"x-webhook-secret": "test-secret"}
        )
        denied = await client.post("/webhook", json={}, headers={"x-webhook-secret": "wrong"})
    assert response.status_code == 503 and "synthetic" not in response.text
    assert denied.status_code == 403


async def test_webhook_retry_skips_successful_batch_prefix(db, monkeypatch):
    @asynccontextmanager
    async def fake_lock(*a, **k):
        yield

    monkeypatch.setattr(locks, "distributed_lock", fake_lock)
    # Keep schema validation in its own HTTP tests; here exercise durable receipts.
    monkeypatch.setattr(processor, "WebhookEvent", lambda **data: SimpleNamespace(**data))
    handler = AsyncMock(side_effect=[None, RuntimeError("temporary"), None])
    monkeypatch.setattr(processor, "handle_event", handler)
    payload = [{"action": "test", "id": 1}, {"action": "test", "id": 2}]
    with pytest.raises(RuntimeError):
        await processor.process_webhook_events(payload)
    await processor.process_webhook_events(payload)
    assert handler.await_count == 3


async def test_main_critical_task_failure_propagates(monkeypatch):
    import main
    from app.db.crud import secrets

    monkeypatch.setattr(main, "ENABLE_FASTAPI", False)
    monkeypatch.setattr(main.signal, "signal", lambda *args: None)
    monkeypatch.setattr(secrets, "ensure_secrets", AsyncMock())
    monkeypatch.setattr(main, "run_telethon", AsyncMock(side_effect=RuntimeError("startup failed")))
    with pytest.raises(RuntimeError, match="startup failed"):
        await asyncio.wait_for(main.main(), timeout=2)


async def test_lock_loss_interrupts_operation(monkeypatch):
    client = SimpleNamespace(set=AsyncMock(return_value=True), eval=AsyncMock(return_value=0))
    monkeypatch.setattr(locks, "get_redis", AsyncMock(return_value=client))
    with pytest.raises(RuntimeError, match="lock lost"):
        async with locks.distributed_lock("lost", ttl=0.03):
            await asyncio.sleep(0.2)


@pytest.mark.skipif(not os.getenv("TEST_REDIS_URL"), reason="Explicit disposable Redis URL required")
async def test_real_redis_lock_renewal(monkeypatch):
    from redis.asyncio import Redis

    client = Redis.from_url(os.environ["TEST_REDIS_URL"], decode_responses=True)
    monkeypatch.setattr(locks, "get_redis", AsyncMock(return_value=client))
    try:
        async with locks.distributed_lock("integration-lock", ttl=1):
            await asyncio.sleep(1.4)
            with pytest.raises(RuntimeError, match="progress"):
                async with locks.distributed_lock("integration-lock", ttl=1):
                    pytest.fail("renewal must hold lock beyond original TTL")
        async with locks.distributed_lock("integration-lock", ttl=1):
            pass
    finally:
        await client.aclose()
