"""Destructive isolated migration/backup test; use a fresh loopback *_test database ONLY.

ADMIN_MIGRATION_TEST_URL=mysql+asyncmy://...@127.0.0.1:33306/admin_migration_test \
TEST_REDIS_URL=redis://127.0.0.1:36379 uv run --no-sync python tests/integration/admin_app_roundtrip.py
"""

# ruff: noqa: E402
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

url = os.environ.get("ADMIN_MIGRATION_TEST_URL", "")
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
from app.db.models.admin_app import AdminChange, AdminGrant, AdminState
from app.db.models.plans import Plan
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
        assert p.returncode != 0 and b"Disabled plans would become sellable" in err
    else:
        assert p.returncode == 0, (out + err).decode()


def differences(conn):
    def include(obj, name, kind, reflected, compare_to):
        table = obj if kind == "table" else getattr(obj, "table", None)
        return table is not None and (table.name.startswith("admin_app_") or table.name == "plans")

    return compare_metadata(MigrationContext.configure(conn, opts={"include_object": include}), Base.metadata)


async def main():
    async with engine.connect() as conn:
        assert not await conn.run_sync(lambda c: inspect(c).get_table_names()), "Requires EMPTY database"
    await migrate("upgrade", "d93b2e5f860c")
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO plans (id,price,storage,duration,panel_code,plan_type,data_limit_reset_strategy,ip_limit) VALUES (1,1000,10,30,10,'volume','no_reset',1)"
            )
        )
    async with Session() as session, session.begin():
        session.add(User(id=777, amount=9000))
    await migrate("upgrade", "head")
    async with engine.connect() as conn:
        assert not await conn.run_sync(differences)
    async with Session() as session:
        assert (await session.get(Plan, 1)).enabled is True
        assert (await session.get(AdminState, 1)).layout == []
        assert await session.get(AdminGrant, 777) is None
    print(
        "PASS: v2 -> v3 migration matches ORM; existing plan stays enabled, layout unchanged, no delegated access seeded"
    )
    layout = [["bt.menu_my_services", "bt.menu_add_balance"]]
    async with Session() as session, session.begin():
        (await session.get(Plan, 1)).enabled = False
        (await session.get(AdminState, 1)).layout = layout
        session.add(AdminGrant(user_id=777, name="Support", permissions=["tickets.view"], updated_at=1))
        session.add(
            AdminChange(
                token="a" * 32,
                actor_id=777,
                entity="settings",
                target="core_settings",
                before={"sale_mode": False},
                after={"sale_mode": True},
                reason="Synthetic",
                status="published",
                created_at=1,
                published_at=2,
                result={"saved": True},
            )
        )
        session.add(
            AdminChange(
                token="b" * 32,
                actor_id=777,
                entity="plans",
                target="1",
                before={"price": 1000},
                after={"price": 2000},
                reason="Synthetic draft",
                status="draft",
                created_at=1,
            )
        )
    with tempfile.TemporaryDirectory(prefix="admin-backup-") as directory:
        root = Path(directory)
        backup = await create_backup_zip(root / "current")
        async with Session() as session, session.begin():
            (await session.get(User, 777)).amount = 0
            (await session.get(Plan, 1)).enabled = True
            (await session.get(AdminState, 1)).layout = []
            (await session.get(AdminGrant, 777)).permissions = []
            (await session.get(AdminChange, "b" * 32)).status = "cancelled"
        result = await restore_from_zip(backup, offline=True, safety_dir=root / "safety")
        assert result.ok, result.message
        async with Session() as session:
            assert (await session.get(User, 777)).amount == 9000
            assert (await session.get(Plan, 1)).enabled is False
            assert (await session.get(AdminState, 1)).layout == layout
            assert (await session.get(AdminGrant, 777)).permissions == ["tickets.view"]
            assert (await session.get(AdminChange, "a" * 32)).status == "published"
            assert (await session.get(AdminChange, "b" * 32)).status == "draft"
        print("PASS: SQL backup/restore preserves wallet, disabled plans, layout, grants, audit and unpublished drafts")
        await migrate("downgrade", "d93b2e5f860c", refuse=True)
        async with engine.connect() as conn:
            assert (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar() == "e04c3f6a971d"
        print("PASS: downgrade refuses to silently re-enable disabled plans before dropping v3 data")
        async with Session() as session, session.begin():
            (await session.get(Plan, 1)).enabled = True
        await migrate("downgrade", "d93b2e5f860c")
        async with engine.connect() as conn:
            names = await conn.run_sync(lambda c: inspect(c).get_table_names())
            assert "auto_renew_attempts" in names and "admin_app_changes" not in names
        await migrate("upgrade", "head")
        async with Session() as session:
            assert (await session.get(User, 777)).amount == 9000
        async with engine.connect() as conn:
            assert not await conn.run_sync(differences)
        print("PASS: explicit resolved downgrade/re-upgrade preserves v2 tables and wallet")
    await close_redis()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
