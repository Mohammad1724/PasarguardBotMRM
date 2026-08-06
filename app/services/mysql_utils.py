"""Shared MySQL/MariaDB connection utilities for backup and restore.

Extracted to avoid duplication between app.services.backup and app.services.restore.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

from config import SQLALCHEMY_DATABASE_URL


@dataclass(frozen=True)
class MysqlConnection:
    """Parsed MariaDB/MySQL connection parameters."""

    host: str
    port: int
    user: str
    password: str
    database: str


def parse_mysql_url(database_url: str = SQLALCHEMY_DATABASE_URL) -> MysqlConnection:
    """Parse a SQLAlchemy-style database URL into a MysqlConnection."""
    parsed = urlparse(database_url)
    scheme = (parsed.scheme or "").split("+", 1)[0].lower()
    if scheme not in {"mysql", "mariadb"}:
        raise ValueError("Only MariaDB/MySQL is supported.")
    if not parsed.hostname or not parsed.path or parsed.path == "/":
        raise ValueError("Invalid database URL.")
    return MysqlConnection(
        host=parsed.hostname,
        port=parsed.port or 3306,
        user=unquote(parsed.username or ""),
        password=unquote(parsed.password or ""),
        database=unquote(parsed.path.lstrip("/")),
    )


def resolve_mysql_client_binary() -> str:
    """Find the mariadb or mysql client binary on the system."""
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
    raise FileNotFoundError("mariadb/mysql client not found. On native installs, install the mariadb-client package.")


def default_native_socket() -> Path | None:
    """Return the native Unix socket path if it exists, else None."""
    config_dir = os.environ.get("PASARGUARDBOT_CONFIG_DIR", "/opt/pasarguardbot")
    sock = Path(config_dir) / "data" / "mariadb" / "pasarguardbot.sock"
    return sock if sock.is_socket() else None


def build_mysql_cmd_args(
    conn: MysqlConnection,
    *,
    include_database: bool = False,
    extra_args: list[str] | None = None,
) -> tuple[list[str], dict[str, str]]:
    """Build the common command-line arguments for the mysql/mariadb client.

    Returns (cmd_args, env_overrides) where env_overrides should be merged
    into os.environ for the subprocess.
    """
    mysql_bin = resolve_mysql_client_binary()
    cmd = [mysql_bin, f"--user={conn.user}"]

    if extra_args:
        cmd.extend(extra_args)

    socket_path = default_native_socket()
    if socket_path is not None and conn.host in {"127.0.0.1", "localhost"}:
        cmd.append(f"--socket={socket_path}")
    else:
        cmd.extend([f"--host={conn.host}", f"--port={conn.port}"])

    if include_database:
        cmd.append(conn.database)

    env = os.environ.copy()
    if conn.password:
        env["MYSQL_PWD"] = conn.password

    return cmd, env


def escape_mysql_identifier(name: str) -> str:
    """Escape a MySQL identifier (database/table name) to prevent injection.

    Backticks are escaped by doubling them.
    """
    return name.replace("`", "``")


def escape_mysql_string(value: str) -> str:
    """Escape a MySQL string literal value to prevent injection.

    Single quotes are escaped by doubling them (MySQL standard).
    """
    return value.replace("'", "''")
