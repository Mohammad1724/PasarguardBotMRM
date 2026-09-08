"""Low-level Redis connection management."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from app.logger import get_logger
from config import REDIS_URL

logger = get_logger(__name__)

redis_client: Any | None = None
_connection_lock = asyncio.Lock()


async def get_redis():
    """Return the shared async Redis client, or None if unavailable."""
    global redis_client
    async with _connection_lock:
        candidate = redis_client
        try:
            if candidate is None:
                from redis.asyncio import Redis

                candidate = Redis.from_url(REDIS_URL, decode_responses=True, socket_connect_timeout=3, socket_timeout=5)
            await candidate.ping()
            redis_client = candidate
            return candidate
        except Exception as exc:
            redis_client = None
            if candidate is not None:
                with contextlib.suppress(Exception):
                    await candidate.aclose()
            logger.warning("Redis unavailable: %s", exc)
            return None


async def close_redis() -> None:
    """Close the shared Redis client."""
    global redis_client
    if redis_client is None:
        return
    try:
        await redis_client.aclose()
    except Exception as exc:
        logger.warning("Redis close error: %s", exc)
    finally:
        redis_client = None


class RedisManager:
    """Thin wrapper around module-level Redis helpers."""

    @staticmethod
    async def get_client():
        return await get_redis()

    @staticmethod
    async def close() -> None:
        await close_redis()
