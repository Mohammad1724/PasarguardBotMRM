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
import os
import shutil
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
        Path(config_dir) / ".env",            # Most reliable (native + Docker real path)
        Path("/app/.env"),                     # Docker symlink target
        Path(__file__).resolve().parents[2] / ".env",  # Project root
        Path(".env"),                          # CWD fallback
    )
    for path in candidates:
        if path.is_file():
            return path
    return None


def _atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    """Write text to a file atomically (write to temp, then rename).

    Prevents corruption if the process is killed mid-write.
    """
    fd = None
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp", prefix=".env-")
        with os.fdopen(fd, "w", encoding=encoding) as f:
            fd = None  # os.fdopen takes ownership of fd
            f.write(content)
        os.replace(tmp_path, str(path))  # Atomic on POSIX
        tmp_path = None  # Successfully replaced
    finally:
        if fd is not None:
            os.close(fd)
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


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
            raise ValueError(
                f"Zip entry attempts path traversal: {member!r}"
            )
    zf.extractall(dest)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

async def validate_backup_zip(zip_path: Path) -> dict:
    """Validate a backup ZIP and return its contents info."""
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
                env_content = zf.read(".env").decode("utf-8", errors="replace")
                info["crypto_key"] = _extract_env_var(env_content, "CRYPTO_KEY")
    except (zipfile.BadZipFile, KeyError, OSError) as exc:
        logger.error("%s Invalid backup ZIP: %s", LogTag.JOB, exc)

    return info


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
            (
                f"SELECT TABLE_NAME, TABLE_TYPE FROM information_schema.TABLES "
                f"WHERE TABLE_SCHEMA='{safe_db}';"
            ),
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
    stdout, stderr = await process.communicate(input=drop_statements.encode("utf-8"))

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
        extra_args=["--max-allowed-packet=256M"],
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
            with open(sql_path, "rb") as f:
                while True:
                    chunk = await asyncio.to_thread(f.read, 1024 * 1024)  # 1MB chunks
                    if not chunk:
                        break
                    process.stdin.write(chunk)
        except BrokenPipeError:
            pass  # Process died; we'll catch the error from returncode
        except ConnectionResetError:
            pass  # Process died; we'll catch the error from returncode
        finally:
            try:
                process.stdin.close()
            except Exception:
                pass

    async def _drain_stderr():
        assert process.stderr is not None
        return await process.stderr.read()

    _, stderr_bytes = await asyncio.gather(_stream_file(), _drain_stderr())
    await process.wait()

    if process.returncode != 0:
        err = (stderr_bytes or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"SQL import failed: {err}")


# ---------------------------------------------------------------------------
# Main restore function
# ---------------------------------------------------------------------------

async def restore_from_zip(
    zip_path: Path,
    *,
    drop_existing: bool = True,
    update_crypto_key: bool = True,
) -> RestoreResult:
    """
    Restore the database from a backup ZIP file.

    Steps:
    1. Acquire distributed lock (prevent concurrent restores)
    2. Extract ZIP to temp directory (with Zip Slip protection)
    3. Validate database.sql exists
    4. Drop all existing tables (if requested)
    5. Import SQL dump (streamed to avoid OOM)
    6. Update CRYPTO_KEY in .env (atomic write)
    7. Reload secrets from DB
    """
    result = RestoreResult(ok=False, message="")
    temp_dir = None
    lock_acquired = False

    # ── Step 0: Distributed lock ──
    from app.db.redis import get_redis
    redis = await get_redis()
    if redis:
        lock_acquired = bool(await redis.set("pasarguardbot:restore_lock", "1", nx=True, ex=600))
        if not lock_acquired:
            return RestoreResult(
                ok=False,
                message="❌ عملیات ریستور دیگری در حال انجام است. لطفاً صبر کنید.",
                errors=["Another restore is in progress (Redis lock active)"],
            )

    try:
        # Validate ZIP
        info = await validate_backup_zip(zip_path)

        if not info["has_sql"]:
            result.message = "❌ فایل بکاپ نامعتبر است: فایل `database.sql` پیدا نشد."
            result.errors.append("Missing database.sql in ZIP")
            return result

        # Extract ZIP (with Zip Slip protection)
        temp_dir = Path(await asyncio.to_thread(tempfile.mkdtemp, "pasarguardbot-restore-"))
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                await asyncio.to_thread(_safe_extractall, zf, temp_dir)
        except ValueError as exc:
            result.message = f"❌ فایل بکاپ حاوی مسیر نامعتبر است: {exc}"
            result.errors.append(f"Zip Slip detected: {exc}")
            return result

        sql_path = temp_dir / "database.sql"
        if not sql_path.is_file():
            result.message = "❌ فایل `database.sql` در بکاپ پیدا نشد."
            result.errors.append("SQL file missing after extraction")
            return result

        sql_size_mb = sql_path.stat().st_size / (1024 * 1024)
        logger.info(
            "%s Restore: SQL file %.2f MB, crypto_key=%s",
            LogTag.JOB,
            sql_size_mb,
            "found" if info["crypto_key"] else "not found",
        )

        conn = parse_mysql_url()

        # Step 1: Drop existing tables
        if drop_existing:
            try:
                dropped = await _drop_all_tables(conn)
                logger.info("%s Restore: dropped %d existing tables/views", LogTag.JOB, dropped)
            except Exception as exc:
                result.message = f"❌ حذف جدول‌های قبلی ناموفق بود: {exc}"
                result.errors.append(f"Drop tables failed: {exc}")
                return result

        # Step 2: Import SQL (streamed)
        try:
            await _import_sql(conn, sql_path)
            logger.info("%s Restore: SQL import completed", LogTag.JOB)
        except Exception as exc:
            result.message = f"❌ ایمپورت دیتابیس ناموفق بود: {exc}"
            result.errors.append(f"SQL import failed: {exc}")
            return result

        # Step 3: Update CRYPTO_KEY in .env (atomic write)
        if update_crypto_key and info["crypto_key"]:
            env_path = _find_env_file()
            if env_path:
                if _update_env_var(env_path, "CRYPTO_KEY", info["crypto_key"]):
                    result.crypto_key_restored = True
                    logger.info("%s Restore: CRYPTO_KEY updated in .env", LogTag.JOB)

                    # Also update WEBHOOK_SECRET if present (read from extracted .env)
                    backup_env_path = temp_dir / ".env"
                    if backup_env_path.is_file():
                        backup_env_content = await asyncio.to_thread(
                            backup_env_path.read_text, encoding="utf-8"
                        )
                        webhook_secret = _extract_env_var(backup_env_content, "WEBHOOK_SECRET")
                        if webhook_secret:
                            _update_env_var(env_path, "WEBHOOK_SECRET", webhook_secret)
                            logger.info("%s Restore: WEBHOOK_SECRET updated in .env", LogTag.JOB)
            else:
                logger.warning("%s Restore: .env not found, CRYPTO_KEY not updated", LogTag.JOB)
                result.errors.append("Could not find .env to update CRYPTO_KEY")

        # Step 4: Dispose stale connection pool and reload secrets from DB
        try:
            from app.db.base import engine
            await engine.dispose()
        except Exception:
            pass  # Not fatal

        try:
            await ensure_secrets()
            logger.info("%s Restore: secrets reloaded from database", LogTag.JOB)
        except Exception as exc:
            logger.warning("%s Restore: could not reload secrets: %s", LogTag.JOB, exc)
            # Not fatal — secrets will be reloaded on next restart

        result.ok = True
        crypto_msg = (
            "🔑 `CRYPTO_KEY` از بکاپ بازیابی و در `.env` جایگزین شد."
            if result.crypto_key_restored
            else "⚠️ `CRYPTO_KEY` در بکاپ نبود — مقدار فعلی دست‌نخورده باقی ماند."
        )
        result.message = (
            f"✅ ریستور دیتابیس با موفقیت انجام شد!\n"
            f"💾 حجم فایل SQL: `{sql_size_mb:.2f}` MB\n"
            f"{crypto_msg}\n\n"
            f"⚠️ **برای اعمال کامل تغییرات، ربات را ری‌استارت کنید.**"
        )
        return result

    except Exception as exc:
        logger.error("%s Restore failed: %s", LogTag.JOB, exc, exc_info=True)
        result.message = f"❌ ریستور ناموفق بود: {exc}"
        result.errors.append(str(exc))
        return result

    finally:
        # Release lock
        if redis and lock_acquired:
            try:
                await redis.delete("pasarguardbot:restore_lock")
            except Exception:
                pass
        # Clean up temp dir
        if temp_dir:
            await asyncio.to_thread(shutil.rmtree, temp_dir, True)
