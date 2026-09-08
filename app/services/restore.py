"""Restore MariaDB from a backup ZIP (database.sql + .env).

Security measures:
- Zip Slip protection via path validation on extraction
- SQL injection prevention via identifier escaping
- Distributed Redis lock to prevent concurrent restores
- Streaming SQL import to avoid OOM on large files
- Atomic .env file writes to prevent corruption on crash
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from app.db.crud.secrets import ensure_secrets
from app.logger import LogTag, get_logger
from app.services.mysql_utils import (
    build_mysql_cmd_args,
    escape_mysql_identifier,
    escape_mysql_string,
    parse_mysql_url,
)

logger = get_logger(__name__)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class RestoreResult:
    ok: bool
    message: str
    crypto_key_restored: bool = False
    tables_imported: int = 0
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# .env file helpers (atomic writes)
# ---------------------------------------------------------------------------


def _find_env_file() -> Path | None:
    """Find the active .env file path (prioritize config dir)."""
    config_dir = os.environ.get("PASARGUARDBOT_CONFIG_DIR", "/opt/pasarguardbot")
    candidates = (
        Path(config_dir) / ".env",  # Most reliable (native + Docker real path)
        Path("/app/.env"),  # Docker symlink target
        Path(__file__).resolve().parents[2] / ".env",  # Project root
        Path(".env"),  # CWD fallback
    )
    for path in candidates:
        if path.is_file():
            return path
    return None


def _atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    """Write text to a file atomically (write to temp, then rename).

    Prevents corruption if the process is killed mid-write.
    Refuses non-atomic writes on file bind mounts; mount a directory instead.
    """
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp", prefix=".env-")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_path)


def _update_env_var(env_path: Path, var_name: str, value: str) -> bool:
    """Update or add a variable in the .env file (atomic write)."""
    if not env_path.is_file():
        logger.warning("%s .env file not found at %s", LogTag.JOB, env_path)
        return False

    content = env_path.read_text(encoding="utf-8")
    lines = content.splitlines()
    found = False
    new_lines = []
    prefix = f"{var_name}="

    for line in lines:
        stripped = line.strip()
        if stripped.startswith(prefix) and not stripped.startswith("#"):
            new_lines.append(f"{var_name}={value}")
            found = True
        else:
            new_lines.append(line)

    if not found:
        new_lines.append(f"{var_name}={value}")

    _atomic_write_text(env_path, "\n".join(new_lines) + "\n")
    return True


def _extract_env_var(env_content: str, var_name: str) -> str | None:
    """Extract a variable value from .env file content."""
    prefix = f"{var_name}="
    for line in env_content.splitlines():
        line = line.strip()
        if line.startswith(prefix) and not line.startswith("#"):
            value = line.split("=", 1)[1].strip()
            # Remove surrounding quotes if present
            if value and len(value) >= 2 and value[0] in ('"', "'") and value[-1] == value[0]:
                value = value[1:-1]
            return value if value else None
    return None


# ---------------------------------------------------------------------------
# Zip Slip protection
# ---------------------------------------------------------------------------


def _safe_extractall(zf: zipfile.ZipFile, dest: Path) -> None:
    """Extract ZIP entries only if they don't escape the destination directory.

    Prevents Zip Slip / path traversal attacks via crafted ZIP files.
    Note: Python's zipfile.extractall does NOT create symlinks — symlink entries
    in ZIPs are extracted as regular files containing the symlink target path.
    """
    entries = zf.infolist()
    if len(entries) > 20 or sum(e.file_size for e in entries) > 10 * 1024**3:
        raise ValueError("Backup exceeds extraction limits")
    if len({e.filename for e in entries}) != len(entries):
        raise ValueError("Duplicate ZIP entries")
    if any(e.filename not in {"database.sql", ".env"} for e in entries):
        raise ValueError("Backup may only contain database.sql and .env")
    dest_resolved = dest.resolve()
    for member in zf.namelist():
        # Skip directory entries and the root marker
        if not member or member in (".", "/"):
            continue
        # Resolve the target path for this entry
        member_path = (dest / member).resolve()
        # Ensure it's strictly within the destination directory
        try:
            member_path.relative_to(dest_resolved)
        except ValueError:
            raise ValueError(f"Zip entry attempts path traversal: {member!r}") from None
    zf.extractall(dest)


# ---------------------------------------------------------------------------
# Sync helpers (called via asyncio.to_thread from async context)
# ---------------------------------------------------------------------------


def _validate_zip_sync(zip_path: Path) -> dict:
    """Synchronous ZIP validation — called via asyncio.to_thread."""
    info: dict = {
        "has_sql": False,
        "has_env": False,
        "sql_size": 0,
        "crypto_key": None,
        "zip_size": 0,
    }

    if not zip_path.is_file():
        return info

    info["zip_size"] = zip_path.stat().st_size

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            if "database.sql" in names:
                info["has_sql"] = True
                info["sql_size"] = zf.getinfo("database.sql").file_size
            if ".env" in names:
                info["has_env"] = True
                if zf.getinfo(".env").file_size > 1024 * 1024:
                    raise ValueError("Backup .env is too large")
                env_content = zf.read(".env").decode("utf-8", errors="replace")
                info["crypto_key"] = _extract_env_var(env_content, "CRYPTO_KEY")
    except (zipfile.BadZipFile, KeyError, OSError) as exc:
        logger.error("%s Invalid backup ZIP: %s", LogTag.JOB, exc)

    return info


async def validate_backup_zip(zip_path: Path) -> dict:
    """Validate a backup ZIP and return its contents info."""
    return await asyncio.to_thread(_validate_zip_sync, zip_path)


# ---------------------------------------------------------------------------
# Database operations
# ---------------------------------------------------------------------------


async def _drop_all_tables(conn) -> int:
    """Drop all tables and views in the database. Returns count of dropped objects."""
    # Use escaped database name in SQL to prevent injection
    safe_db = escape_mysql_string(conn.database)

    list_cmd, env = build_mysql_cmd_args(
        conn,
        extra_args=[
            "--batch",
            "--skip-column-names",
            "-e",
            (f"SELECT TABLE_NAME, TABLE_TYPE FROM information_schema.TABLES WHERE TABLE_SCHEMA='{safe_db}';"),
        ],
    )

    process = await asyncio.create_subprocess_exec(
        *list_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        err = (stderr or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Failed to list tables: {err}")

    tables = []
    views = []
    for line in stdout.decode("utf-8").strip().split("\n"):
        parts = line.strip().split("\t")
        if len(parts) == 2:
            name, table_type = parts
            if table_type == "VIEW":
                views.append(name)
            else:
                tables.append(name)

    if not tables and not views:
        return 0

    # Disable foreign key checks and drop all views then tables
    drop_statements = "SET FOREIGN_KEY_CHECKS=0;\n"
    # Drop views first (they may reference tables)
    for view in views:
        safe_view = escape_mysql_identifier(view)
        drop_statements += f"DROP VIEW IF EXISTS `{safe_view}`;\n"
    for table in tables:
        safe_table = escape_mysql_identifier(table)
        drop_statements += f"DROP TABLE IF EXISTS `{safe_table}`;\n"
    drop_statements += "SET FOREIGN_KEY_CHECKS=1;\n"

    drop_cmd, drop_env = build_mysql_cmd_args(conn, include_database=True)

    process = await asyncio.create_subprocess_exec(
        *drop_cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=drop_env,
    )
    _, stderr = await process.communicate(input=drop_statements.encode("utf-8"))

    if process.returncode != 0:
        err = (stderr or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Failed to drop tables/views: {err}")

    return len(tables) + len(views)


async def _import_sql(conn, sql_path: Path) -> None:
    """Import SQL file into the database using streaming to avoid OOM.

    Uses stdout=DEVNULL to prevent pipe-buffer deadlock: if stdout were PIPE,
    the mysql process could block writing to a full stdout pipe while we're
    blocked writing to stdin, causing a deadlock.
    """
    cmd, env = build_mysql_cmd_args(
        conn,
        include_database=True,
        extra_args=["--max-allowed-packet=256M", "--binary-mode=1"],
    )

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )

    # Stream file in 1MB chunks and drain stderr concurrently to avoid deadlock.
    async def _stream_file():
        assert process.stdin is not None
        try:
            loop = asyncio.get_running_loop()
            with open(sql_path, "rb") as f:  # noqa: ASYNC230
                while True:
                    chunk = await loop.run_in_executor(None, f.read, 1024 * 1024)
                    if not chunk:
                        break
                    process.stdin.write(chunk)
                    await process.stdin.drain()
        except BrokenPipeError:
            pass  # Process died; we'll catch the error from returncode
        except ConnectionResetError:
            pass  # Process died; we'll catch the error from returncode
        finally:
            with contextlib.suppress(Exception):
                process.stdin.close()

    async def _drain_stderr():
        assert process.stderr is not None
        return await process.stderr.read()

    try:
        _, stderr_bytes = await asyncio.gather(_stream_file(), _drain_stderr())
        await process.wait()
    except BaseException:
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise

    if process.returncode != 0:
        err = (stderr_bytes or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"SQL import failed: {err}")


# ---------------------------------------------------------------------------
# Sync file helpers (called via asyncio.to_thread)
# ---------------------------------------------------------------------------


def _path_is_file(path: Path) -> bool:
    return path.is_file()


def _path_stat_size(path: Path) -> int:
    return path.stat().st_size


# ---------------------------------------------------------------------------
# Main restore function
# ---------------------------------------------------------------------------


OFFLINE_RESTORE_MESSAGE = (
    "برای جلوگیری از خرابی داده، ریستور در رباتِ در حال اجرا مجاز نیست. "
    "روی سرور از دستور pasarguardbot restore /path/to/backup.zip استفاده کنید. "
    "این مسیر ربات را متوقف می‌کند، بکاپ ایمنی می‌گیرد و migrationها را اجرا می‌کند."
)


def _has_secrets_schema(sql_path: Path) -> bool:
    pattern = re.compile(rb"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?`?secrets`?\s*\(", re.I)
    with sql_path.open("rb") as handle:
        previous = b""
        while chunk := handle.read(1024 * 1024):
            if pattern.search(previous + chunk):
                return True
            previous = chunk[-512:]
    return False


def _validate_dump_database(path: Path, database: str) -> None:
    pattern = re.compile(rb"^USE\s+`((?:``|[^`])+)`\s*;", re.I | re.M)
    with path.open("rb") as handle:
        previous = b""
        while chunk := handle.read(1024 * 1024):
            for match in pattern.finditer(previous + chunk):
                if match.group(1).decode().replace("``", "`") != database:
                    raise ValueError("Backup selects a different database; use an isolated matching database first")
            previous = chunk[-512:]


async def _migrate_restored_database(legacy_env: str) -> None:
    env = os.environ.copy()
    for key in ("CRYPTO_KEY", "WEBHOOK_SECRET"):
        value = _extract_env_var(legacy_env, key)
        env[key] = value or ""
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "alembic", "upgrade", "head", cwd=str(_PROJECT_ROOT), env=env
    )
    try:
        await process.wait()
    except BaseException:
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise
    if process.returncode:
        raise RuntimeError("Restored database migration failed; keep the bot stopped")


async def restore_from_zip(
    zip_path: Path,
    *,
    drop_existing: bool = True,
    update_crypto_key: bool = True,
    offline: bool = False,
    safety_dir: Path | None = None,
) -> RestoreResult:
    """Only the offline CLI may invoke destructive restore. Never run beside a live bot."""
    if not offline:
        return RestoreResult(ok=False, message=OFFLINE_RESTORE_MESSAGE)
    if not drop_existing or not update_crypto_key:
        return RestoreResult(ok=False, message="Partial restore is not supported; use a complete trusted backup")
    from sqlalchemy import select

    from app.db.base import AsyncSessionLocal, engine
    from app.db.models.secrets import Secret
    from app.services.backup import create_backup_zip
    from app.services.locks import distributed_lock

    safety_backup = None
    try:
        async with distributed_lock("restore", ttl=120):
            info = await validate_backup_zip(zip_path)
            if not info["has_sql"]:
                raise ValueError("Missing database.sql")
            with tempfile.TemporaryDirectory(prefix="pasarguardbot-restore-") as temp:
                directory = Path(temp)
                with zipfile.ZipFile(zip_path) as archive:
                    await asyncio.to_thread(_safe_extractall, archive, directory)
                sql_path = directory / "database.sql"
                legacy_env = await asyncio.to_thread((directory / ".env").read_text) if info["has_env"] else ""
                if not await asyncio.to_thread(_has_secrets_schema, sql_path) and not info["crypto_key"]:
                    raise ValueError("Legacy backup has no secrets table or CRYPTO_KEY; refusing unsafe restore")
                conn = parse_mysql_url()
                await asyncio.to_thread(_validate_dump_database, sql_path, conn.database)
                safety_root = safety_dir or Path("logs/restore-safety")
                await asyncio.to_thread(safety_root.mkdir, parents=True, exist_ok=True, mode=0o700)
                safety_work = Path(await asyncio.to_thread(tempfile.mkdtemp, prefix="before-", dir=safety_root))
                safety_backup = await create_backup_zip(safety_work)
                logger.warning("Safety backup saved at %s; retain until restore is verified", safety_backup)
                await engine.dispose()
                await _drop_all_tables(conn)
                await _import_sql(conn, sql_path)
                await _migrate_restored_database(legacy_env)
                await engine.dispose()
                async with AsyncSessionLocal() as session:
                    crypto = await session.scalar(select(Secret.value).where(Secret.name == "crypto_key"))
                    if not crypto or not crypto.strip():
                        raise ValueError("Restored crypto_key is missing; refusing to generate a replacement")
                await ensure_secrets()  # Any failure is fatal, never reported as success.
            return RestoreResult(
                ok=True,
                crypto_key_restored=True,
                message=f"ریستور و migration کامل شد. کلیدها از جدول secrets بارگذاری شدند. بکاپ ایمنی: {safety_backup}",
            )
    except Exception as exc:
        logger.exception("Offline restore failed")
        return RestoreResult(
            ok=False,
            message=f"ریستور ناموفق است؛ ربات را روشن نکنید. بکاپ ایمنی: {safety_backup}. خطا: {exc}",
            errors=[str(exc)],
        )
