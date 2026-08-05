"""Restore MariaDB from a backup ZIP (database.sql + .env)."""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlparse

from app.db.crud.secrets import ensure_secrets
from app.logger import LogTag, get_logger
from config import SQLALCHEMY_DATABASE_URL

logger = get_logger(__name__)


@dataclass
class RestoreResult:
    ok: bool
    message: str
    crypto_key_restored: bool = False
    tables_imported: int = 0
    errors: list[str] = field(default_factory=list)


def _resolve_mysql_binary() -> str:
    """Find the mariadb/mysql client binary."""
    for name in ("mariadb", "mysql"):
        path = shutil.which(name)
        if path:
            return path
    for path in (
        "/usr/bin/mariadb",
        "/usr/bin/mysql",
        "/usr/local/bin/mariadb",
        "/usr/local/bin/mysql",
    ):
        if Path(path).is_file() and os.access(path, os.X_OK):
            return path
    raise FileNotFoundError(
        "mariadb/mysql client not found. On native installs, install the mariadb-client package."
    )


def _default_native_socket() -> Path | None:
    config_dir = os.environ.get("PASARGUARDBOT_CONFIG_DIR", "/opt/pasarguardbot")
    sock = Path(config_dir) / "data" / "mariadb" / "pasarguardbot.sock"
    return sock if sock.is_socket() else None


@dataclass(frozen=True)
class MysqlConnection:
    host: str
    port: int
    user: str
    password: str
    database: str


def parse_mysql_url(database_url: str = SQLALCHEMY_DATABASE_URL) -> MysqlConnection:
    parsed = urlparse(database_url)
    scheme = (parsed.scheme or "").split("+", 1)[0].lower()
    if scheme not in {"mysql", "mariadb"}:
        raise ValueError("Restore is only supported for MariaDB/MySQL.")
    if not parsed.hostname or not parsed.path or parsed.path == "/":
        raise ValueError("Invalid database URL.")
    return MysqlConnection(
        host=parsed.hostname,
        port=parsed.port or 3306,
        user=unquote(parsed.username or ""),
        password=unquote(parsed.password or ""),
        database=unquote(parsed.path.lstrip("/")),
    )


def _extract_crypto_key_from_env(env_content: str) -> str | None:
    """Extract CRYPTO_KEY value from .env file content."""
    for line in env_content.splitlines():
        line = line.strip()
        if line.startswith("CRYPTO_KEY=") and not line.startswith("#"):
            value = line.split("=", 1)[1].strip()
            # Remove surrounding quotes if present
            if value and value[0] in ('"', "'") and value[-1] == value[0]:
                value = value[1:-1]
            return value if value else None
    return None


def _extract_webhook_secret_from_env(env_content: str) -> str | None:
    """Extract WEBHOOK_SECRET value from .env file content."""
    for line in env_content.splitlines():
        line = line.strip()
        if line.startswith("WEBHOOK_SECRET=") and not line.startswith("#"):
            value = line.split("=", 1)[1].strip()
            if value and value[0] in ('"', "'") and value[-1] == value[0]:
                value = value[1:-1]
            return value if value else None
    return None


def _update_env_crypto_key(env_path: Path, crypto_key: str) -> bool:
    """Update CRYPTO_KEY in the current .env file."""
    if not env_path.is_file():
        logger.warning("%s .env file not found at %s", LogTag.JOB, env_path)
        return False

    content = env_path.read_text(encoding="utf-8")
    lines = content.splitlines()
    found = False
    new_lines = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("CRYPTO_KEY=") and not stripped.startswith("#"):
            new_lines.append(f"CRYPTO_KEY={crypto_key}")
            found = True
        else:
            new_lines.append(line)

    if not found:
        new_lines.append(f"CRYPTO_KEY={crypto_key}")

    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return True


def _find_env_file() -> Path | None:
    """Find the active .env file path."""
    config_dir = os.environ.get("PASARGUARDBOT_CONFIG_DIR", "/opt/pasarguardbot")
    candidates = (
        Path(".env"),
        Path(__file__).resolve().parents[2] / ".env",
        Path(config_dir) / ".env",
        Path("/app/.env"),
    )
    for path in candidates:
        if path.is_file():
            return path
    return None


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
                info["crypto_key"] = _extract_crypto_key_from_env(env_content)
    except (zipfile.BadZipFile, Exception) as exc:
        logger.error("%s Invalid backup ZIP: %s", LogTag.JOB, exc)

    return info


async def _drop_all_tables(conn: MysqlConnection) -> int:
    """Drop all tables in the database. Returns count of dropped tables."""
    mysql_bin = _resolve_mysql_binary()

    # First, get list of tables
    list_cmd = [
        mysql_bin,
        f"--user={conn.user}",
        "--batch",
        "--skip-column-names",
        "-e",
        f"SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA='{conn.database}';",
    ]

    socket_path = _default_native_socket()
    if socket_path is not None and conn.host in {"127.0.0.1", "localhost"}:
        list_cmd.append(f"--socket={socket_path}")
    else:
        list_cmd.extend([f"--host={conn.host}", f"--port={conn.port}"])

    env = os.environ.copy()
    if conn.password:
        env["MYSQL_PWD"] = conn.password

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

    tables = [t.strip() for t in stdout.decode("utf-8").strip().split("\n") if t.strip()]

    if not tables:
        return 0

    # Disable foreign key checks and drop all tables
    drop_statements = "SET FOREIGN_KEY_CHECKS=0;\n"
    for table in tables:
        drop_statements += f"DROP TABLE IF EXISTS `{table}`;\n"
    drop_statements += "SET FOREIGN_KEY_CHECKS=1;\n"

    drop_cmd = [
        mysql_bin,
        f"--user={conn.user}",
        conn.database,
    ]

    if socket_path is not None and conn.host in {"127.0.0.1", "localhost"}:
        drop_cmd.append(f"--socket={socket_path}")
    else:
        drop_cmd.extend([f"--host={conn.host}", f"--port={conn.port}"])

    process = await asyncio.create_subprocess_exec(
        *drop_cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, stderr = await process.communicate(input=drop_statements.encode("utf-8"))

    if process.returncode != 0:
        err = (stderr or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Failed to drop tables: {err}")

    return len(tables)


async def _import_sql(conn: MysqlConnection, sql_path: Path) -> None:
    """Import SQL file into the database."""
    mysql_bin = _resolve_mysql_binary()

    cmd = [
        mysql_bin,
        f"--user={conn.user}",
        "--max-allowed-packet=256M",
    ]

    socket_path = _default_native_socket()
    if socket_path is not None and conn.host in {"127.0.0.1", "localhost"}:
        cmd.append(f"--socket={socket_path}")
    else:
        cmd.extend([f"--host={conn.host}", f"--port={conn.port}"])

    # The SQL dump uses CREATE DATABASE, so we don't need to specify database
    # But we add it for safety
    cmd.append(conn.database)

    env = os.environ.copy()
    if conn.password:
        env["MYSQL_PWD"] = conn.password

    sql_content = await asyncio.to_thread(sql_path.read_bytes)

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, stderr = await process.communicate(input=sql_content)

    if process.returncode != 0:
        err = (stderr or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"SQL import failed: {err}")


async def restore_from_zip(
    zip_path: Path,
    *,
    drop_existing: bool = True,
    update_crypto_key: bool = True,
) -> RestoreResult:
    """
    Restore the database from a backup ZIP file.

    Steps:
    1. Extract ZIP to temp directory
    2. Validate database.sql exists
    3. Drop all existing tables (if requested)
    4. Import SQL dump
    5. Update CRYPTO_KEY in .env (if found in backup)
    6. Reload secrets from DB
    """
    result = RestoreResult(ok=False, message="")
    temp_dir = None

    try:
        # Validate ZIP
        info = await validate_backup_zip(zip_path)

        if not info["has_sql"]:
            result.message = "❌ فایل بکاپ نامعتبر است: فایل `database.sql` پیدا نشد."
            result.errors.append("Missing database.sql in ZIP")
            return result

        # Extract ZIP
        temp_dir = Path(await asyncio.to_thread(tempfile.mkdtemp, "pasarguardbot-restore-"))
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(temp_dir)

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
                logger.info("%s Restore: dropped %d existing tables", LogTag.JOB, dropped)
            except Exception as exc:
                result.message = f"❌ حذف جدول‌های قبلی ناموفق بود: {exc}"
                result.errors.append(f"Drop tables failed: {exc}")
                return result

        # Step 2: Import SQL
        try:
            await _import_sql(conn, sql_path)
            logger.info("%s Restore: SQL import completed", LogTag.JOB)
        except Exception as exc:
            result.message = f"❌ ایمپورت دیتابیس ناموفق بود: {exc}"
            result.errors.append(f"SQL import failed: {exc}")
            return result

        # Step 3: Update CRYPTO_KEY in .env
        if update_crypto_key and info["crypto_key"]:
            env_path = _find_env_file()
            if env_path:
                if _update_env_crypto_key(env_path, info["crypto_key"]):
                    result.crypto_key_restored = True
                    logger.info("%s Restore: CRYPTO_KEY updated in .env", LogTag.JOB)

                    # Also update WEBHOOK_SECRET if present
                    with zipfile.ZipFile(zip_path, "r") as zf:
                        env_content = zf.read(".env").decode("utf-8", errors="replace")
                    webhook_secret = _extract_webhook_secret_from_env(env_content)
                    if webhook_secret:
                        # Update webhook_secret in .env too
                        content = env_path.read_text(encoding="utf-8")
                        lines = content.splitlines()
                        found_ws = False
                        new_lines = []
                        for line in lines:
                            stripped = line.strip()
                            if stripped.startswith("WEBHOOK_SECRET=") and not stripped.startswith("#"):
                                new_lines.append(f"WEBHOOK_SECRET={webhook_secret}")
                                found_ws = True
                            else:
                                new_lines.append(line)
                        if not found_ws:
                            new_lines.append(f"WEBHOOK_SECRET={webhook_secret}")
                        env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
                        logger.info("%s Restore: WEBHOOK_SECRET updated in .env", LogTag.JOB)
            else:
                logger.warning("%s Restore: .env not found, CRYPTO_KEY not updated", LogTag.JOB)
                result.errors.append("Could not find .env to update CRYPTO_KEY")

        # Step 4: Try to reload secrets from DB (if the secrets table exists in the restored data)
        try:
            await ensure_secrets()
            logger.info("%s Restore: secrets reloaded from database", LogTag.JOB)
        except Exception as exc:
            logger.warning("%s Restore: could not reload secrets: %s", LogTag.JOB, exc)
            # Not fatal — secrets will be reloaded on next restart

        result.ok = True
        sql_size_mb = sql_path.stat().st_size / (1024 * 1024)
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
        if temp_dir:
            await asyncio.to_thread(shutil.rmtree, temp_dir, True)
