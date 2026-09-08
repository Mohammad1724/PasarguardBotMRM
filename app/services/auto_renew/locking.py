"""Service write lock shared with legacy manual writers. Context is reentrant within one task only."""

import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar

from sqlalchemy import select

from app.db.base import AsyncSessionLocal as Session
from app.db.models.auto_renew import AutoRenewAttempt, AutoRenewPolicy

_held = ContextVar("service_write_locks", default=())


class ServiceLockUnavailable(RuntimeError):
    pass


class RenewalConflict(ValueError):
    pass


@asynccontextmanager
async def service_write_lock(code):
    from app.services.locks import distributed_lock

    code = int(code)
    owner = asyncio.current_task()
    if (code, owner) in _held.get():
        yield
        return
    entered = False
    try:
        async with distributed_lock(f"service-write:{code}"):
            entered = True
            token = _held.set((*_held.get(), (code, owner)))
            try:
                yield
            finally:
                _held.reset(token)
    except RuntimeError as exc:
        if not entered:
            raise ServiceLockUnavailable("Service lock unavailable") from exc
        raise


async def protected(code):
    async with Session() as session:
        policy = await session.get(AutoRenewPolicy, int(code))
        if policy and policy.state == "enabled":
            return True
        return bool(
            await session.scalar(
                select(AutoRenewAttempt.token)
                .where(
                    AutoRenewAttempt.active_service_code == int(code),
                )
                .limit(1)
            )
        )


@asynccontextmanager
async def manual_write(code):
    async with service_write_lock(code):
        if await protected(code):
            raise RenewalConflict(
                "ابتدا تمدید خودکار این سرویس را خاموش کنید و درخواست پرداخت‌شده ناتمام را تعیین تکلیف کنید."
            )
        yield
