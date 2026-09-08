"""Explicit destructive integration test; ONLY a disposable loopback *_test DB.

RESTORE_TEST_DATABASE_URL=mysql+asyncmy://...@127.0.0.1:33306/restore_test \
TEST_REDIS_URL=redis://127.0.0.1:36379 uv run python tests/integration/restore_roundtrip.py
Create an EMPTY database first. Never supply production credentials.
"""

# ruff: noqa: E402
import asyncio
import os
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlparse

url = os.environ.get("RESTORE_TEST_DATABASE_URL", "")
parsed = urlparse(url)
if parsed.hostname not in ("127.0.0.1", "localhost") or not parsed.path.endswith("_test"):
    raise SystemExit("Refusing non-test/non-loopback database")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.update(
    SQLALCHEMY_DATABASE_URL=url,
    API_ID="12345",
    API_HASH="0" * 32,
    BOT_TOKEN="12345:synthetic",
    ADMIN_ID="1",
    TELETHON_SESSION_PATH=":memory:",
    REDIS_URL=os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:36379"),
    LOG_TO_FILE="False",
)

from sqlalchemy import select

from app.db.base import AsyncSessionLocal, engine
from app.db.models.secrets import Secret
from app.db.models.user import User
from app.db.redis import close_redis
from app.services.backup import create_backup_zip
from app.services.restore import _migrate_restored_database, restore_from_zip


async def read_key():
    async with AsyncSessionLocal() as session:
        return await session.scalar(select(Secret.value).where(Secret.name == "crypto_key"))


async def main():
    await _migrate_restored_database("CRYPTO_KEY=original-test-key\nWEBHOOK_SECRET=test-webhook\n")
    key = await read_key()
    async with AsyncSessionLocal() as session, session.begin():
        session.add(User(id=777, amount=10))
    with tempfile.TemporaryDirectory(prefix="restore-integration-") as temp:
        root = Path(temp)
        current = await create_backup_zip(root / "current")
        async with AsyncSessionLocal() as session, session.begin():
            (await session.get(User, 777)).amount = 999
        result = await restore_from_zip(current, offline=True, safety_dir=root / "safety")
        assert result.ok, result.message
        assert await read_key() == key
        async with AsyncSessionLocal() as session:
            assert (await session.get(User, 777)).amount == 10
        assert list((root / "safety").rglob("*.zip"))
        print("PASS: current backup -> modified database -> restore, safety backup and original secrets")

        # Restore an actual pre-secrets-schema database with its legacy .env key.
        await engine.dispose()
        process = await asyncio.create_subprocess_exec(sys.executable, "-m", "alembic", "downgrade", "6dbb6a2ebe16")
        assert await process.wait() == 0
        legacy = await create_backup_zip(root / "legacy")
        with zipfile.ZipFile(legacy, "a") as archive:
            archive.writestr(".env", "CRYPTO_KEY=legacy-test-key\nWEBHOOK_SECRET=legacy-webhook\n")
        await _migrate_restored_database("CRYPTO_KEY=intermediate-key\n")
        result = await restore_from_zip(legacy, offline=True, safety_dir=root / "safety")
        assert result.ok, result.message
        assert await read_key() == "legacy-test-key"
        print("PASS: legacy backup -> migrations -> restored legacy crypto key in secrets table")

        # Real SQL failure after destructive DROP must not report success.
        bad = root / "bad.zip"
        with zipfile.ZipFile(bad, "w") as archive:
            archive.writestr("database.sql", "CREATE TABLE `secrets` (id INT);\nTHIS IS NOT SQL;\n")
        result = await restore_from_zip(bad, offline=True, safety_dir=root / "safety")
        assert not result.ok and result.errors
        assert len(list((root / "safety").rglob("*.zip"))) >= 3
        print("PASS: real import failure returns failure and retains pre-drop safety backups")
        # Recover disposable DB using known-good backup, never start a broken bot.
        result = await restore_from_zip(legacy, offline=True, safety_dir=root / "safety")
        assert result.ok, result.message
        print("PASS: recovery after failed import")
    await close_redis()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
