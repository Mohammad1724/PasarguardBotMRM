"""Fail-closed, renewable Redis locks with ownership-checked release."""

import asyncio
import contextlib
import secrets
from contextlib import asynccontextmanager

from app.db.redis import get_redis
from config import REDIS_NAMESPACE_PREFIX

_RENEW = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('expire', KEYS[1], ARGV[2]) else return 0 end"
_RELEASE = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"


@asynccontextmanager
async def distributed_lock(name: str, ttl: int = 120):
    redis = await get_redis()
    if redis is None:
        raise RuntimeError("Redis unavailable; operation refused for safety")
    key = f"{REDIS_NAMESPACE_PREFIX}:safety:{name}"
    token = secrets.token_hex(24)
    if not await redis.set(key, token, nx=True, ex=ttl):
        raise RuntimeError("Operation already in progress; retry shortly")
    owner = asyncio.current_task()
    lost = False

    async def renew():
        nonlocal lost
        try:
            while True:
                await asyncio.sleep(ttl / 3)
                if not await redis.eval(_RENEW, 1, key, token, ttl):
                    raise RuntimeError("Lock ownership lost")
        except asyncio.CancelledError:
            raise
        except Exception:
            lost = True
            owner.cancel()

    task = asyncio.create_task(renew())
    try:
        yield
    except asyncio.CancelledError:
        if lost:
            raise RuntimeError("Distributed lock lost; reconciliation required") from None
        raise
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        with contextlib.suppress(Exception):
            await redis.eval(_RELEASE, 1, key, token)
