"""Readiness on a private Unix socket, including Telegram startup, SQL and Redis.

No Telegram RPC or financial action is performed by a probe. This does not prove
that every VPN panel/payment provider is available or that a message was delivered.
"""

import asyncio
import os
import stat
from pathlib import Path

from sqlalchemy import text

from app import Kenzo
from app.db.base import AsyncSessionLocal as Session
from app.db.redis import get_redis
from healthcheck import SOCKET_PATH

telegram_started = False


async def dependencies_ready():
    if not telegram_started or not Kenzo.is_connected():
        return False
    async with Session() as session:
        await session.execute(text("SELECT 1"))
    return await get_redis() is not None


class ReadinessServer:
    def __init__(self, gate, path=SOCKET_PATH):
        self.gate = gate
        self.path = Path(path)
        self.server = None
        self.lock_fd = None
        self.owns_socket = False
        self.closing = False
        self.clients = set()

    async def start(self):
        # Linux native/container feature. Windows bot execution remains supported.
        if os.name == "nt":
            return self
        import fcntl

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_fd = os.open(str(self.path) + ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(self.lock_fd).st_mode):
                raise RuntimeError("Readiness lock is not a regular file")
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.fchmod(self.lock_fd, 0o600)
            if self.path.exists() or self.path.is_symlink():
                if not stat.S_ISSOCK(self.path.lstat().st_mode):
                    raise RuntimeError("Refusing to replace a non-socket readiness path")
                self.path.unlink()  # stale socket; the exclusive lock proves no cooperating runtime owns it
            self.server = await asyncio.start_unix_server(self.handle, path=str(self.path))
            self.owns_socket = True
            self.path.chmod(0o600)
        except BaseException:
            await self.close()
            raise
        return self

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.clients.add(task)
        try:
            ready = False
            try:
                async with asyncio.timeout(3):
                    ready = not self.closing and self.gate() and await dependencies_ready()
            except Exception:
                ready = False
            if self.closing:
                ready = False
            writer.write(b"ready\n" if ready else b"not-ready\n")
            await writer.drain()
        except ConnectionError, OSError:
            pass
        finally:
            writer.close()
            self.clients.discard(task)

    async def close(self):
        self.closing = True
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        clients = tuple(self.clients)
        for task in clients:
            task.cancel()
        if clients:
            await asyncio.gather(*clients, return_exceptions=True)
        if self.owns_socket:
            self.path.unlink(missing_ok=True)
            self.owns_socket = False
        if self.lock_fd is not None:
            os.close(self.lock_fd)
            self.lock_fd = None
