"""Serialize manual service writers with enrollment/renewal, including stale Telegram states."""

import json
from contextlib import AsyncExitStack
from functools import wraps
from inspect import signature

from sqlalchemy import select

from app.db.base import AsyncSessionLocal as Session
from app.db.models.auto_renew import AutoRenewPolicy
from app.db.models.services import Service
from app.services.auto_renew.locking import RenewalConflict, ServiceLockUnavailable, manual_write


async def callback_code(event, data):
    from app.telegram.state import get_data

    for prefix in ("ChangeSub:", "ChangeLink:"):
        if data.startswith(prefix):
            return int(data.split(":")[2])
    if data.startswith(("upgSize@", "upgTime@")):
        return int(data.split("@")[1])
    if data.startswith(("confirm_purchase_tamdid_", "Confirm_buy_tamdid")):
        value = await get_data(event.sender_id, "ConfigID")
        return int(value) if value else None
    if data.startswith(
        (
            "ConfirmDelete:",
            "TransferConfig:",
            "DeleteServiceAdmin_confirm:",
            "AdminConfigToggle:",
            "AdminConfigVolume:",
            "AdminConfigTime:",
        )
    ):
        return int(data.split(":")[1])
    return None


def telegram_write_guard(func):
    @wraps(func)
    async def wrapper(event, *args, **kwargs):
        from app.telegram.state import get_data, get_step

        try:
            codes = []
            data = kwargs.get("data") or (args[0] if args and isinstance(args[0], str) else None)
            if data is None and getattr(event, "data", None):
                data = event.data.decode("utf-8", "ignore")
            if data:
                code = await callback_code(event, data)
                if code is not None:
                    codes = [code]
                if data.startswith("BulkDeleteConfirm:"):
                    payload = await get_data(event.sender_id, "AdminBulkDeleteData")
                    if payload:
                        records = json.loads(payload).get("services", [])
                        codes = [int(s["code"]) for s in records if s.get("code")]
                        # The preview may predate an import/enrollment and contain no DB code.
                        # Resolve live aliases so stale "panel-only" deletion cannot bypass the lock.
                        async with Session() as session:
                            for record in records:
                                if record.get("in_panel") and record.get("panel_userid"):
                                    codes.extend(
                                        (
                                            await session.scalars(
                                                select(Service.code).where(
                                                    Service.in_panel == int(record["in_panel"]),
                                                    Service.panel_userid == int(record["panel_userid"]),
                                                )
                                            )
                                        ).all()
                                    )
                                    codes.extend(
                                        (
                                            await session.scalars(
                                                select(AutoRenewPolicy.service_code).where(
                                                    AutoRenewPolicy.panel_code == int(record["in_panel"]),
                                                    AutoRenewPolicy.panel_userid == int(record["panel_userid"]),
                                                )
                                            )
                                        ).all()
                                    )
            else:
                step = await get_step(event.sender_id)
                key = {
                    "whating_send_TransferConfig": "TransferConfig",
                    "AdminConfigVolumeInput": "AdminConfigVolumeInputCode",
                    "AdminConfigTimeInput": "AdminConfigTimeInputCode",
                }.get(step)
                if key:
                    value = await get_data(event.sender_id, key)
                    if value:
                        codes = [int(value)]
            if not codes:
                return await func(event, *args, **kwargs)
            from config import ADMIN_ID

            async with Session() as session:
                for code in codes:
                    service = await session.get(Service, code)
                    if service and service.id != event.sender_id and event.sender_id not in ADMIN_ID:
                        raise RenewalConflict("سرویس متعلق به شما نیست.")
            async with AsyncExitStack() as stack:
                for code in sorted(set(codes)):
                    await stack.enter_async_context(manual_write(code))
                return await func(event, *args, **kwargs)
        except (RenewalConflict, ServiceLockUnavailable) as exc:
            text = (
                str(exc)
                if isinstance(exc, RenewalConflict)
                else "عملیات دیگری در حال انجام است یا قفل ایمنی در دسترس نیست؛ کمی بعد تلاش کنید."
            )
            await event.respond(text, parse_mode=None)
            return None

    return wrapper


def service_argument_guard(func):
    """For backend functions taking a service argument; never bypasses existing caller auth."""
    sig = signature(func)

    @wraps(func)
    async def wrapper(*args, **kwargs):
        bound = sig.bind(*args, **kwargs)
        async with manual_write(bound.arguments["service"].code):
            return await func(*args, **kwargs)

    return wrapper


def bulk_write_guard(func):
    sig = signature(func)

    @wraps(func)
    async def wrapper(*args, **kwargs):
        bound = sig.bind(*args, **kwargs)
        async with AsyncExitStack() as stack:
            for code in sorted({s.code for s in bound.arguments["services"]}):
                await stack.enter_async_context(manual_write(code))
            return await func(*args, **kwargs)

    return wrapper
