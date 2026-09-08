"""Wallet auto-renew v2. No card charging; no recurring defaults or silent plan changes."""

import asyncio
import secrets
import time
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from httpx import HTTPStatusError
from pasarguard import UserModify
from sqlalchemy import func, select

from app.db.base import AsyncSessionLocal as Session
from app.db.models.auto_renew import (
    AutoRenewAttempt as Attempt,
    AutoRenewConsent as Consent,
    AutoRenewNotice as Notice,
    AutoRenewPolicy as Policy,
)
from app.db.models.customer_experience import CustomerExperienceAudit, TrialConversion
from app.db.models.gift_codes import GiftCodeUse
from app.db.models.panels import Panels
from app.db.models.plans import Plan
from app.db.models.services import Service
from app.db.models.user import User
from app.services.auto_renew.locking import RenewalConflict, service_write_lock
from app.services.customer_experience.common import owned_service, require_user, settings
from app.services.customer_experience.conversion import panel_snapshot, snapshot as volume_snapshot
from app.services.gifts import panel_call

NOTICE_WINDOW = 48 * 3600
RENEW_WINDOW = 24 * 3600
NOTICE_LEAD = 6 * 3600
EXPIRED_GRACE = 48 * 3600
PENDING = ("applying", "review")


def now_ts():
    return int(time.time())


def month_key(now):
    return datetime.fromtimestamp(now, UTC).astimezone(ZoneInfo("Asia/Tehran")).strftime("%Y-%m")


def plan_snapshot(plan):
    if plan is not None and getattr(plan, "enabled", True) is False:
        raise ValueError("پلن غیرفعال است؛ پلن دیگری انتخاب کنید.")
    try:
        data = volume_snapshot(plan)
    except ValueError, TypeError, OverflowError:
        raise ValueError(
            "تمدید خودکار فعلاً فقط پلن حجمی زمان‌دار، با قیمت صحیح و بدون ریست دوره‌ای را پشتیبانی می‌کند."
        ) from None
    if not (
        2 <= data["duration"] <= 3650
        and 0 <= data["ip_limit"] <= 10000
        and data["price"] <= 10**12
        and data["storage"] <= 10**6
    ):
        raise ValueError("پلن خارج از محدوده تمدید خودکار است؛ مدت باید حداقل ۲ روز باشد.")
    return data


def identity(service, panel):
    return {
        "panel_code": service.in_panel,
        "panel_userid": service.panel_userid,
        "username": service.username,
        "panel_url": panel.base_url,
    }


def validate_live(user, ident):
    if int(user.id) != ident["panel_userid"] or user.username != ident["username"]:
        raise RenewalConflict("هویت سرویس پنل تغییر کرده است.")
    status = str(getattr(user.status, "value", user.status))
    values = panel_snapshot(user)
    if (
        status not in ("active", "expired", "limited")
        or getattr(user, "next_plan", None)
        or not values["expire"]
        or values["data_limit"] <= 0
        or values["data_limit_reset_strategy"] != "no_reset"
    ):
        raise RenewalConflict("سرویس غیرفعال، نامحدود، دارای برنامه بعدی یا ریست دوره‌ای قابل تمدید خودکار نیست.")
    return values


async def api_call(panel, method, **kwargs):
    async with asyncio.timeout(12):
        return await panel_call(panel, method, **kwargs)


async def foreign_pending(session, code):
    gift = await session.scalar(
        select(GiftCodeUse.id)
        .where(
            GiftCodeUse.service_code == str(code),
            GiftCodeUse.status == "applying",
        )
        .limit(1)
    )
    trial = await session.scalar(
        select(TrialConversion.token)
        .where(
            TrialConversion.service_code == code,
            TrialConversion.status == "applying",
        )
        .limit(1)
    )
    return bool(gift or trial)


async def pending(session, code):
    return await session.scalar(select(Attempt).where(Attempt.active_service_code == code))


async def _available(session, user_id, code):
    await require_user(session, user_id)
    service = await owned_service(session, user_id, code)
    config = await settings(session)
    panel = await session.get(Panels, service.in_panel)
    if not (config.auto_renew_enabled and config.bot_mode and config.tamdid_mode):
        raise RenewalConflict("تمدید خودکار یا امکان تمدید از سمت مدیر خاموش است.")
    from app.services.panels.settings import panel_button_enabled

    if (
        service.is_test is True
        or not service.panel_userid
        or not panel
        or not panel.enable
        or not panel_button_enabled(panel, "btn_tamdid")
    ):
        raise RenewalConflict("این سرویس/پنل برای تمدید خودکار در دسترس نیست.")
    if await foreign_pending(session, code):
        raise RenewalConflict("ابتدا درخواست هدیه/تبدیل ناتمام همین سرویس را پیگیری کنید.")
    return service, panel


async def make_consent(user_id, code, plan_id, monthly_multiple):
    if monthly_multiple not in (1, 2, 3):
        raise ValueError("سقف ماهانه نامعتبر است.")
    async with service_write_lock(code):
        async with Session() as session:
            service, panel = await _available(session, user_id, code)
            if await pending(session, code):
                raise RenewalConflict("تمدید پرداخت‌شده ناتمام دارید؛ همان درخواست را پیگیری کنید.")
            plan = plan_snapshot(await session.get(Plan, plan_id))
            if plan["panel_code"] != service.in_panel:
                raise ValueError("پلن باید متعلق به همان پنل باشد.")
            ident = identity(service, panel)
        current = validate_live(await api_call(panel, "get_user_by_id", user_id=service.panel_userid), ident)
        now = now_ts()
        if current["expire"] < now - EXPIRED_GRACE:
            raise ValueError("بیش از ۴۸ ساعت از انقضا گذشته؛ ابتدا به صورت دستی تمدید کنید.")
        async with Session() as session, session.begin():
            await require_user(session, user_id, lock=True)
            service, panel = await _available(session, user_id, code)
            if identity(service, panel) != ident or plan_snapshot(await session.get(Plan, plan_id)) != plan:
                raise ValueError("اطلاعات تغییر کرده؛ دوباره تلاش کنید.")
            count = await session.scalar(
                select(func.count())
                .select_from(Consent)
                .where(
                    Consent.user_id == user_id,
                    Consent.created_at > now - 900,
                )
            )
            if count >= 10:
                raise ValueError("تأییدیه‌های زیادی ساخته‌اید؛ از قبلی استفاده کنید یا ۱۵ دقیقه بعد تلاش کنید.")
            policy = await session.get(Policy, code)
            quote = Consent(
                token=secrets.token_hex(16),
                user_id=user_id,
                service_code=code,
                revision=policy.revision if policy else 0,
                snapshot={"identity": ident, "plan": plan, "plan_id": plan_id, "values": current},
                monthly_cap=plan["price"] * monthly_multiple,
                created_at=now,
                expires_at=now + 900,
            )
            session.add(quote)
            return quote


async def confirm(user_id, token):
    async with Session() as session:
        quote = await session.get(Consent, token)
        if not quote or quote.user_id != user_id:
            raise ValueError("تأییدیه متعلق به شما نیست یا پیدا نشد.")
        code = quote.service_code
    async with service_write_lock(code):
        async with Session() as session:
            service, panel = await _available(session, user_id, code)
            ident = identity(service, panel)
        current = validate_live(await api_call(panel, "get_user_by_id", user_id=service.panel_userid), ident)
        now = now_ts()
        async with Session() as session, session.begin():
            await require_user(session, user_id, lock=True)
            quote = await session.get(Consent, token, with_for_update=True)
            policy = await session.get(Policy, code, with_for_update=True)
            service, panel = await _available(session, user_id, code)
            if quote.confirmed_at is not None:
                raise ValueError(
                    "این تأییدیه قبلاً استفاده شده؛ وضعیت فعلی را ببینید. برای فعال‌سازی دوباره تأییدیه جدید لازم است."
                )
            if (
                quote.expires_at <= now
                or quote.revision != (policy.revision if policy else 0)
                or quote.snapshot["identity"] != identity(service, panel)
                or ident != identity(service, panel)
                or quote.snapshot["plan"] != plan_snapshot(await session.get(Plan, quote.snapshot["plan_id"]))
                or current != quote.snapshot["values"]
                or current["expire"] < now - EXPIRED_GRACE
            ):
                raise ValueError("قیمت، سرویس یا تأییدیه تغییر کرده؛ دوباره انتخاب و تأیید کنید.")
            if await pending(session, code):
                raise RenewalConflict("یک تمدید پرداخت‌شده ناتمام وجود دارد.")
            if await session.scalar(
                select(Attempt.token)
                .where(Attempt.service_code == code, Attempt.cycle_expire == current["expire"])
                .limit(1)
            ):
                raise ValueError("این دوره قبلاً درخواست داشته؛ رسید را بررسی و در صورت لزوم دستی تمدید کنید.")
            if not policy:
                policy = Policy(service_code=code, created_at=now)
                session.add(policy)
            policy.user_id = user_id
            for k, value in ident.items():
                setattr(policy, k, value)
            policy.plan_id = quote.snapshot["plan_id"]
            policy.plan_snapshot = quote.snapshot["plan"]
            policy.expected_values = current
            policy.per_charge_cap = quote.snapshot["plan"]["price"]
            policy.monthly_cap = quote.monthly_cap
            policy.revision = quote.revision + 1
            policy.state, policy.reason = "enabled", ""
            policy.updated_at, policy.next_check_at = now, now
            quote.confirmed_at = now
            session.add(
                CustomerExperienceAudit(
                    actor_id=user_id,
                    action="auto_renew_consent",
                    detail=f"service={code};revision={policy.revision};price={policy.per_charge_cap};monthly_cap={policy.monthly_cap};token={token}",
                    created_at=now,
                )
            )
            return policy


async def disable(actor_id, code):
    from config import ADMIN_ID

    # User -> policy is also reservation lock ordering. No network lock needed:
    # cancellation wins before debit, or stops FUTURE debits after a funded reservation.
    async with Session() as session:
        owner = await session.scalar(select(Policy.user_id).where(Policy.service_code == code))
    if owner is None or (owner != actor_id and actor_id not in ADMIN_ID):
        raise ValueError("برنامه پیدا نشد یا اجازه لغو ندارید.")
    async with Session() as session, session.begin():
        await session.get(User, owner, with_for_update=True)
        policy = await session.get(Policy, code, with_for_update=True)
        if policy.user_id != actor_id and actor_id not in ADMIN_ID:
            raise ValueError("دسترسی ندارید.")
        policy.state, policy.reason = "off", "cancelled"
        policy.revision += 1
        policy.updated_at = now_ts()
        session.add(
            CustomerExperienceAudit(
                actor_id=actor_id,
                action="auto_renew_off",
                detail=f"service={code};revision={policy.revision}",
                created_at=now_ts(),
            )
        )
        return bool(await pending(session, code))


async def get_policy(user_id, code):
    async with Session() as session:
        await require_user(session, user_id)
        await owned_service(session, user_id, code)
        policy = await session.get(Policy, code)
        if policy and policy.user_id != user_id:
            policy = None  # a transferred service never inherits recurring consent
        return policy, await pending(session, code)


def notice_key(code, revision, cycle, kind):
    return f"{code}:{revision}:{cycle}:{kind}"


async def enqueue(session, policy, cycle, kind, now, **payload):
    key = notice_key(policy.service_code, policy.revision, cycle, kind)
    if not await session.get(Notice, key):
        session.add(
            Notice(
                key=key,
                user_id=policy.user_id,
                service_code=policy.service_code,
                revision=policy.revision,
                cycle_expire=cycle,
                kind=kind,
                payload=payload,
                state="pending",
                created_at=now,
            )
        )


async def pause(session, policy, reason, now):
    policy.state, policy.reason, policy.updated_at = "paused", reason, now
    await enqueue(session, policy, policy.expected_values["expire"], "paused", now, reason=reason)


async def _reserve(code, current, ident, now):
    """SQL wallet debit + immutable attempt in one transaction; caller owns service-write lock."""
    async with Session() as session:
        owner = await session.scalar(select(Policy.user_id).where(Policy.service_code == code))
    if owner is None:
        return None
    async with Session() as session, session.begin():
        user = await session.get(User, owner, with_for_update=True)
        policy = await session.get(Policy, code, with_for_update=True)
        if not policy or policy.state != "enabled" or policy.user_id != owner:
            return None
        policy.next_check_at = now + 900
        if not user or user.status in ("ban", "BlockedBot", "DeleteAccount"):
            await pause(session, policy, "account_unavailable", now)
            return None
        try:
            service, panel = await _available(session, owner, code)
        except ValueError:
            return None  # global/panel switches can resume; no debit
        if identity(service, panel) != ident or ident != {k: getattr(policy, k) for k in ident}:
            await pause(session, policy, "identity_changed", now)
            return None
        if await pending(session, code):
            return None
        try:
            live_plan = plan_snapshot(await session.get(Plan, policy.plan_id))
        except ValueError:
            live_plan = None
        if live_plan != policy.plan_snapshot:
            await pause(session, policy, "plan_changed", now)
            return None
        try:
            values = validate_live(current, ident)
        except ValueError:
            await pause(session, policy, "unsupported_service", now)
            return None
        if values != policy.expected_values:
            await pause(session, policy, "service_changed", now)
            return None
        cycle = values["expire"]
        if cycle < now - EXPIRED_GRACE:
            await pause(session, policy, "too_late", now)
            return None
        if cycle > now + NOTICE_WINDOW:
            policy.next_check_at = min(now + 6 * 3600, cycle - NOTICE_WINDOW)
            return None
        await enqueue(
            session, policy, cycle, "upcoming", now, price=policy.per_charge_cap, monthly_cap=policy.monthly_cap
        )
        await session.flush()
        notice = await session.get(Notice, notice_key(code, policy.revision, cycle, "upcoming"))
        if notice.state in ("uncertain", "cancelled"):
            await pause(session, policy, "notice_unconfirmed", now)
            return None
        if (
            notice.state != "sent"
            or not notice.sent_at
            or now < notice.sent_at + NOTICE_LEAD
            or cycle > now + RENEW_WINDOW
        ):
            return None
        price = live_plan["price"]
        if price > policy.per_charge_cap:
            await pause(session, policy, "charge_cap", now)
            return None
        already = await session.scalar(
            select(Attempt.token).where(Attempt.service_code == code, Attempt.cycle_expire == cycle)
        )
        if already:
            await pause(session, policy, "cycle_already_attempted", now)
            return None
        spent = await session.scalar(
            select(func.coalesce(func.sum(Attempt.price), 0)).where(
                Attempt.service_code == code,
                Attempt.user_id == owner,
                Attempt.month == month_key(now),
                Attempt.status != "refunded",
            )
        )
        if spent + price > policy.monthly_cap:
            policy.reason, policy.next_check_at = "monthly_cap", now + 3600
            await enqueue(session, policy, cycle, "budget", now, price=price, monthly_cap=policy.monthly_cap)
            return None
        if int(user.amount or 0) < price:
            policy.reason, policy.next_check_at = "low_balance", now + 3600
            await enqueue(session, policy, cycle, "low_balance", now, price=price)
            return None
        target = {
            **values,
            "data_limit": max(values["data_limit"], int(current.used_traffic or 0))
            + int(live_plan["storage"] * 1024**3),
            "expire": max(cycle, now) + live_plan["duration"] * 86400,
            "hwid_limit": live_plan["ip_limit"],
        }
        UserModify(**target, status="active")
        order = Attempt(
            token=secrets.token_hex(16),
            service_code=code,
            user_id=owner,
            active_service_code=code,
            policy_revision=policy.revision,
            cycle_expire=cycle,
            identity=ident,
            price=price,
            month=month_key(now),
            old_values=values,
            target_values=target,
            status="applying",
            attempted=False,
            retry_count=0,
            next_retry_at=now,
            error="",
            created_at=now,
        )
        session.add(order)
        user.amount = int(user.amount or 0) - price
        policy.reason = "processing"
        return order


async def process_policy(code):
    async with service_write_lock(code):
        now = now_ts()
        async with Session() as session, session.begin():
            policy = await session.get(Policy, code, with_for_update=True)
            if not policy or policy.state != "enabled" or policy.next_check_at > now:
                return
            policy.next_check_at = now + 900  # persisted before network: a dead panel cannot starve later policies
            ident = {k: getattr(policy, k) for k in ("panel_code", "panel_userid", "username", "panel_url")}
            panel = await session.get(Panels, policy.panel_code)
            service = await session.get(Service, code)
            if (
                not service
                or service.id != policy.user_id
                or service.panel_userid != policy.panel_userid
                or service.in_panel != policy.panel_code
                or service.username != policy.username
            ):
                await pause(session, policy, "identity_changed", now)
                return
            config = await settings(session)
            if not (config.auto_renew_enabled and config.bot_mode and config.tamdid_mode and panel and panel.enable):
                return
            if panel.base_url != policy.panel_url:
                await pause(session, policy, "identity_changed", now)
                return
        current = await api_call(panel, "get_user_by_id", user_id=ident["panel_userid"])
        order = await _reserve(code, current, ident, now)
        if order:
            await reconcile(order.token)


async def _settle(order, *, refund=False):
    now = now_ts()
    async with Session() as session, session.begin():
        user = await session.get(User, order.user_id, with_for_update=True)
        row = await session.get(Attempt, order.token, with_for_update=True)
        if row.status not in PENDING:
            return row
        policy = await session.get(Policy, row.service_code, with_for_update=True)
        service = await session.get(Service, row.service_code, with_for_update=True)
        if refund:
            if not user:
                raise RenewalConflict("حساب کیف پول برای بازپرداخت پیدا نشد.")
            user.amount = int(user.amount or 0) + row.price
            row.status = "refunded"
            if policy and policy.state == "enabled":
                await pause(session, policy, "panel_rejected", now)
        else:
            panel = await session.get(Panels, row.identity["panel_code"])
            if (
                not panel
                or panel.base_url != row.identity["panel_url"]
                or not service
                or service.id != row.user_id
                or service.in_panel != row.identity["panel_code"]
                or service.panel_userid != row.identity["panel_userid"]
                or service.username != row.identity["username"]
            ):
                raise RenewalConflict("local_identity_changed")
            service.expiration_time, service.package_size = row.target_values["expire"], row.target_values["data_limit"]
            service.ip_limit, service.data_limit_reset_strategy = row.target_values["hwid_limit"], "no_reset"
            service.enable, service.warning, service.warning_time = True, 0, 0
            service.expire_notified, service.low_volume_notified = False, False
            row.status = "applied"
            if policy and policy.user_id == row.user_id:
                policy.expected_values = row.target_values
                policy.reason = "" if policy.state == "enabled" else policy.reason
                policy.next_check_at = min(now + 6 * 3600, row.target_values["expire"] - NOTICE_WINDOW)
                policy.updated_at = now
        row.active_service_code, row.completed_at = None, now
        # Receipt is addressed to the original payer, never a later service owner.
        key = f"attempt:{row.token}:receipt"
        if not await session.get(Notice, key):
            session.add(
                Notice(
                    key=key,
                    user_id=row.user_id,
                    service_code=row.service_code,
                    revision=row.policy_revision,
                    cycle_expire=row.cycle_expire,
                    kind="receipt",
                    payload={"price": row.price, "status": row.status, "token": row.token},
                    state="pending",
                    created_at=now,
                )
            )
        return row


async def _defer(token, reason, *, review=False):
    async with Session() as session, session.begin():
        row = await session.get(Attempt, token, with_for_update=True)
        if row and row.status in PENDING:
            row.retry_count += 1
            row.error = reason
            row.next_retry_at = now_ts() + min(3600, 60 * 2 ** min(row.retry_count, 6))
            if review or row.retry_count >= 10:
                row.status = "review"
                key = f"attempt:{row.token}:review"
                if not await session.get(Notice, key):
                    session.add(
                        Notice(
                            key=key,
                            user_id=row.user_id,
                            service_code=row.service_code,
                            revision=row.policy_revision,
                            cycle_expire=row.cycle_expire,
                            kind="review",
                            payload={"token": row.token},
                            state="pending",
                            created_at=now_ts(),
                        )
                    )


async def reconcile(token):
    async with Session() as session:
        order = await session.get(Attempt, token)
        if not order:
            raise ValueError("درخواست پیدا نشد.")
    async with service_write_lock(order.service_code):
        async with Session() as session:
            order = await session.get(Attempt, token)
            if order.status not in PENDING:
                return order
            # Persist a scheduling claim before any network call. Process cancellation does
            # not leave the same oldest attempt starving the rest of the recovery queue.
            order.next_retry_at = now_ts() + 60
            await session.commit()
            panel = await session.get(Panels, order.identity["panel_code"])
            service = await session.get(Service, order.service_code)
            if (
                not panel
                or panel.base_url != order.identity["panel_url"]
                or not service
                or service.id != order.user_id
                or identity(service, panel) != order.identity
            ):
                await _defer(token, "identity_changed", review=True)
                return None
        try:
            current = await api_call(panel, "get_user_by_id", user_id=order.identity["panel_userid"])
            values = validate_live(current, order.identity)
            if values == order.target_values:
                return await _settle(order)
            if values != order.old_values or order.target_values["expire"] <= now_ts():
                await _defer(token, "panel_drift", review=True)
                return None
            first = not order.attempted
            async with Session() as session, session.begin():
                row = await session.get(Attempt, token, with_for_update=True)
                row.attempted = True
            try:
                await api_call(
                    panel,
                    "modify_user_by_id",
                    user_id=order.identity["panel_userid"],
                    user=UserModify(**order.target_values, status="active"),
                )
            except HTTPStatusError as exc:
                if first and exc.response.status_code in (400, 403, 404, 422):
                    return await _settle(order, refund=True)
                raise
            current = await api_call(panel, "get_user_by_id", user_id=order.identity["panel_userid"])
            if validate_live(current, order.identity) != order.target_values:
                await _defer(token, "readback_mismatch", review=True)
                return None
            return await _settle(order)
        except RenewalConflict:
            await _defer(token, "panel_or_identity_changed", review=True)
        except Exception:
            await _defer(token, "external_or_database_error")
        return None
