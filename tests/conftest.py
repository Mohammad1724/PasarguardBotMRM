# ruff: noqa: E402
"""Tests use synthetic credentials, no Telegram session file and disposable SQLite."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for key, value in {
    "API_ID": "12345",
    "API_HASH": "00000000000000000000000000000000",
    "BOT_TOKEN": "12345:synthetic-test-token",
    "ADMIN_ID": "1",
    "SQLALCHEMY_DATABASE_URL": os.getenv("TEST_DATABASE_URL", "sqlite+aiosqlite:///:memory:"),
    "TELETHON_SESSION_PATH": ":memory:",
    "LOG_TO_FILE": "False",
    "REDIS_URL": "redis://127.0.0.1:1",
}.items():
    os.environ[key] = value

if os.getenv("TEST_DATABASE_URL"):
    from urllib.parse import urlparse

    parsed = urlparse(os.environ["TEST_DATABASE_URL"])
    if parsed.hostname not in ("127.0.0.1", "localhost") or not parsed.path.endswith("_test"):
        raise RuntimeError("Tests may only use a loopback database ending in _test")

import pytest_asyncio

from app.db import models  # noqa: F401
from app.db.base import Base, engine


@pytest_asyncio.fixture
async def db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


from app.db.base import AsyncSessionLocal
from app.db.crud.settings import SettingsManager
from app.db.models.user import User


@pytest_asyncio.fixture
async def users(db):
    async with AsyncSessionLocal() as session, session.begin():
        session.add_all([User(id=1, amount=0), User(id=2, ref=1, amount=0), User(id=3, amount=0)])
    await SettingsManager().add_setting(
        referral_enabled=True, referral_percent=10, referral_first_bonus=100, referral_min_deposit=0, gift_mode=True
    )
