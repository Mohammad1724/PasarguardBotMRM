"""Destructive isolated migration/backup test; use a fresh loopback *_test database ONLY.

AR_MIGRATION_TEST_URL=mysql+asyncmy://...@127.0.0.1:33306/ar_migration_test \
TEST_REDIS_URL=redis://127.0.0.1:36379 uv run --no-sync python tests/integration/wallet_auto_renew_roundtrip.py
"""

# ruff: noqa: E402
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

url = os.environ.get("AR_MIGRATION_TEST_URL", "")
parsed = urlparse(url)
if parsed.hostname not in ("localhost", "127.0.0.1") or not parsed.path.endswith("_test"):
    raise SystemExit("A fresh loopback *_test database is required")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.update(
    SQLALCHEMY_DATABASE_URL=url,
    API_ID="12345",
    API_HASH="0" * 32,
    BOT_TOKEN="12345:synthetic",
    ADMIN_ID="1",
    TELETHON_SESSION_PATH=":memory:",
    LOG_TO_FILE="False",
    REDIS_URL=os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:36379"),
)

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text

from app.db.base import AsyncSessionLocal as Session, Base, engine
from app.db.crud.settings import SettingsManager
from app.db.models.auto_renew import (
    AutoRenewAttempt as Attempt,
    AutoRenewConsent as Consent,
    AutoRenewNotice as Notice,
    AutoRenewPolicy as Policy,
)
from app.db.models.settings import Settings
from app.db.models.user import User
from app.db.redis import close_redis
from app.services.backup import create_backup_zip
from app.services.restore import restore_from_zip


async def migrate(*args, refuse=False):
    await engine.dispose()
    p = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "alembic", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await p.communicate()
    if refuse:
        assert p.returncode != 0 and b"Resolve funded auto-renew" in err
    else:
        assert p.returncode == 0, (out + err).decode()


def differences(conn):
    def include(obj, name, kind, reflected, compare_to):
        table = obj if kind == "table" else getattr(obj, "table", None)
        return table is not None and table.name.startswith("auto_renew_")

    return compare_metadata(MigrationContext.configure(conn, opts={"include_object": include}), Base.metadata)


async def main():
    async with engine.connect() as conn:
        assert not await conn.run_sync(lambda c: inspect(c).get_table_names()), "Requires EMPTY database"
    await migrate("upgrade", "c82a1d9e740b")
    async with Session() as session, session.begin():
        session.add(User(id=777, amount=9000))
        session.add(
            Settings(
                core_settings={},
                payment_settings={},
                purchase_settings={},
                service_tools_settings={},
                reseller_settings={},
            )
        )
    await migrate("upgrade", "head")
    config = await SettingsManager().get_settings()
    assert not config.auto_renew_enabled
    async with engine.connect() as conn:
        assert not await conn.run_sync(differences)
    print("PASS: v1 -> v2 migration, four tables match ORM; legacy settings default OFF; old wallet preserved")
    async with Session() as session, session.begin():
        session.add(
            Policy(
                service_code=123,
                user_id=777,
                panel_code=1,
                panel_userid=222,
                username="paid_123",
                panel_url="https://panel.invalid",
                plan_id=1,
                plan_snapshot={"price": 1000},
                expected_values={"expire": 100},
                per_charge_cap=1000,
                monthly_cap=2000,
                revision=1,
                state="enabled",
                reason="processing",
                next_check_at=1,
                created_at=1,
                updated_at=1,
            )
        )
        session.add(
            Consent(
                token="b" * 32,
                user_id=777,
                service_code=123,
                revision=0,
                snapshot={"plan": {"price": 1000}},
                monthly_cap=2000,
                created_at=1,
                expires_at=900,
                confirmed_at=2,
            )
        )
        session.add(
            Attempt(
                token="a" * 32,
                user_id=777,
                service_code=123,
                active_service_code=123,
                policy_revision=1,
                cycle_expire=100,
                identity={"panel_userid": 222},
                price=1000,
                month="2026-09",
                old_values={"expire": 100},
                target_values={"expire": 200},
                status="review",
                attempted=True,
                retry_count=1,
                next_retry_at=999,
                error="panel_drift",
                created_at=10,
            )
        )
        session.add(
            Notice(
                key="123:1:100:upcoming",
                user_id=777,
                service_code=123,
                revision=1,
                cycle_expire=100,
                kind="upcoming",
                payload={"price": 1000},
                state="claimed",
                created_at=1,
            )
        )
    with tempfile.TemporaryDirectory(prefix="ar-backup-") as directory:
        root = Path(directory)
        backup = await create_backup_zip(root / "current")
        async with Session() as session, session.begin():
            (await session.get(User, 777)).amount = 123
            (await session.get(Attempt, "a" * 32)).status = "applied"
            (await session.get(Policy, 123)).monthly_cap = 9999
        result = await restore_from_zip(backup, offline=True, safety_dir=root / "safety")
        assert result.ok, result.message
        async with Session() as session:
            assert (await session.get(User, 777)).amount == 9000
            assert (await session.get(Attempt, "a" * 32)).status == "review"
            assert (await session.get(Policy, 123)).monthly_cap == 2000
            assert (await session.get(Consent, "b" * 32)).confirmed_at == 2
            assert (await session.get(Notice, "123:1:100:upcoming")).state == "claimed"
        print(
            "PASS: real backup/restore preserves wallet, explicit consent, caps, billing cycle, unsettled funds and notice claim"
        )
        for unresolved in ("applying", "review"):
            async with Session() as session, session.begin():
                (await session.get(Attempt, "a" * 32)).status = unresolved
            await migrate("downgrade", "c82a1d9e740b", refuse=True)
        async with engine.connect() as conn:
            assert (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar() == "d93b2e5f860c"
        print("PASS: downgrade refuses to erase review/applying funds before any table is dropped")
        async with Session() as session, session.begin():
            # Synthetic fixture resolution, not a production refund procedure.
            (await session.get(Attempt, "a" * 32)).status = "refunded"
            (await session.get(Policy, 123)).state = "off"
            (await session.get(User, 777)).amount = 10000
        await migrate("downgrade", "c82a1d9e740b")
        async with engine.connect() as conn:
            names = await conn.run_sync(lambda c: inspect(c).get_table_names())
            assert "cx_tickets" in names and "auto_renew_policies" not in names
        await migrate("upgrade", "head")
        async with Session() as session:
            assert (await session.get(User, 777)).amount == 10000
        async with engine.connect() as conn:
            assert not await conn.run_sync(differences)
        print("PASS: resolved downgrade/re-upgrade keeps v1 tables and wallet balance; recreated v2 schema matches")
    await close_redis()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
