"""Destructive migration/backup integration; ONLY a fresh loopback *_test database.

CX_MIGRATION_TEST_URL=mysql+asyncmy://...@127.0.0.1:33306/cx_migration_test \
TEST_REDIS_URL=redis://127.0.0.1:36379 uv run --no-sync python tests/integration/customer_experience_roundtrip.py
"""

# ruff: noqa: E402
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

url = os.environ.get("CX_MIGRATION_TEST_URL", "")
parsed = urlparse(url)
if parsed.hostname not in ("localhost", "127.0.0.1") or not parsed.path.endswith("_test"):
    raise SystemExit("Only a fresh loopback database ending in _test is allowed")
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
from app.db.models.customer_experience import SupportTicket, TicketMessage, TrialConversion, TrialJourney
from app.db.models.user import User
from app.db.redis import close_redis
from app.services.backup import create_backup_zip
from app.services.restore import restore_from_zip


async def migrate(*args, expect_success=True):
    await engine.dispose()
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "alembic", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    if expect_success:
        assert process.returncode == 0, (stdout + stderr).decode()
    else:
        assert process.returncode != 0 and b"Resolve all funded" in stderr


def schema_differences(connection):
    def include_object(obj, name, type_, reflected, compare_to):
        table = obj if type_ == "table" else getattr(obj, "table", None)
        return table is not None and table.name.startswith("cx_")

    ctx = MigrationContext.configure(connection, opts={"include_object": include_object})
    return compare_metadata(ctx, Base.metadata)


async def main():
    async with engine.connect() as connection:
        assert not await connection.run_sync(lambda conn: inspect(conn).get_table_names()), "Database must be EMPTY"
    await migrate("upgrade", "f19c8d42a601")
    async with Session() as session, session.begin():
        session.add(User(id=777, amount=9000))
    await SettingsManager().add_default_settings()
    await migrate("upgrade", "head")
    config = await SettingsManager().get_settings()
    assert config and not config.cx_followup_enabled and not config.cx_conversion_enabled
    async with engine.connect() as connection:
        differences = await connection.run_sync(schema_differences)
        assert not differences, differences
    print("PASS: previous safety head -> new migration; all CX tables match ORM; features default OFF")
    async with Session() as session, session.begin():
        ticket = SupportTicket(
            user_id=777, service_code=123, topic="connection", status="open", created_at=10, updated_at=10
        )
        session.add(ticket)
        await session.flush()
        tid = ticket.id
        session.add(
            TicketMessage(
                ticket_id=tid,
                author_id=777,
                kind="customer",
                text="متن پشتیبانی آزمایشی",
                file_id="synthetic-reference",
                created_at=10,
            )
        )
        session.add(
            TrialJourney(
                service_code=123,
                user_id=777,
                panel_code=1,
                created_at=1,
                expires_at=20,
                ended_at=20,
                retain_until=604820,
                opted_in=True,
                consented_at=2,
                experiment_group="message",
                followup_status="claimed",
                attempted_at=21,
            )
        )
        session.add(
            TrialConversion(
                token="a" * 32,
                user_id=777,
                service_code=123,
                active_service_code=123,
                panel_code=1,
                panel_userid=222,
                username="test_123",
                plan_id=1,
                price=1000,
                plan_snapshot={"duration": 30},
                old_values={"data_limit": 100},
                target_values={"data_limit": 200},
                status="applying",
                attempted=True,
                created_at=10,
                expires_at=900,
            )
        )
    with tempfile.TemporaryDirectory(prefix="cx-backup-") as temp:
        root = Path(temp)
        backup = await create_backup_zip(root / "current")
        async with Session() as session, session.begin():
            (await session.get(User, 777)).amount = 123
            (await session.get(SupportTicket, tid)).status = "closed"
            (await session.get(TrialConversion, "a" * 32)).status = "applied"
        result = await restore_from_zip(backup, offline=True, safety_dir=root / "safety")
        assert result.ok, result.message
        async with Session() as session:
            assert (await session.get(User, 777)).amount == 9000
            assert (await session.get(SupportTicket, tid)).status == "open"
            assert (await session.get(TrialJourney, 123)).followup_status == "claimed"
            assert (await session.get(TrialConversion, "a" * 32)).status == "applying"
        print(
            "PASS: real SQL backup/restore preserves ticket, attachment reference, consent, claim, funded intent and wallet"
        )
        await migrate("downgrade", "f19c8d42a601", expect_success=False)
        async with engine.connect() as connection:
            assert (
                await connection.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar() == "c82a1d9e740b"
        print("PASS: downgrade refuses to erase an unresolved funded intent BEFORE dropping any CX table")
        # Synthetic fixture resolution only; not a production refund procedure.
        async with Session() as session, session.begin():
            (await session.get(TrialConversion, "a" * 32)).status = "refunded"
            (await session.get(User, 777)).amount = 10000
        await migrate("downgrade", "f19c8d42a601")
        await migrate("upgrade", "head")
        async with Session() as session:
            assert (await session.get(User, 777)).amount == 10000
        async with engine.connect() as connection:
            assert not await connection.run_sync(schema_differences)
        print(
            "PASS: resolved-intent downgrade / re-upgrade keeps pre-existing user balance; recreated CX schema matches"
        )
    await close_redis()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
