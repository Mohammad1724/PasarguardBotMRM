"""Explicit two-step recurring consent. All callbacks recheck the caller server-side."""

from sqlalchemy import func, select
from telethon import Button, events

from app.db.base import AsyncSessionLocal as Session
from app.db.crud.plans import PlanManager
from app.db.models.auto_renew import AutoRenewAttempt as Attempt, AutoRenewPolicy as Policy
from app.db.models.customer_experience import CustomerExperienceAudit
from app.db.models.services import Service
from app.db.models.settings import Settings, resolve_settings_update_kwargs
from app.logger import get_logger
from app.services.auto_renew import service as ar
from app.services.auto_renew.notices import REASONS
from app.services.customer_experience.common import owned_service, require_user, settings
from app.telegram.shared.utils.maintenance import bot_is_offline
from config import ADMIN_ID

logger = get_logger(__name__)
STATES = {"enabled": "روشن", "off": "خاموش", "paused": "متوقف‌شده"}


def btn(text, data):
    return Button.inline(text, "ar:" + data)


def num(value):
    n = int(value)
    if not 0 <= n < 2**63:
        raise ValueError("شناسه نامعتبر است.")
    return n


async def list_services(event, page=0):
    async with Session() as session:
        await require_user(session, event.sender_id)
        rows = list(
            (
                await session.scalars(
                    select(Service)
                    .where(
                        Service.id == event.sender_id,
                        Service.is_test.is_not(True),
                    )
                    .order_by(Service.code)
                    .offset(min(page, 10000) * 8)
                    .limit(9)
                )
            ).all()
        )
    buttons = [[btn(f"سرویس {row.code}", f"view:{row.code}")] for row in rows[:8]]
    if page:
        buttons.append([btn("قبلی", f"list:{page - 1}")])
    if len(rows) > 8:
        buttons.append([btn("بعدی", f"list:{page + 1}")])
    buttons.append([btn("رسیدهای تمدید خودکار من", "receipts:0")])
    await event.respond(
        "تمدید خودکار اختیاری از کیف پول\nبرای دیدن وضعیت یا انتخاب پلن، سرویس را انتخاب کنید. هیچ سرویسی پیش‌فرض عضو نیست.",
        buttons=buttons,
        parse_mode=None,
    )


async def view(event, code):
    policy, attempt = await ar.get_policy(event.sender_id, code)
    async with Session() as session:
        config = await settings(session)
    text = f"تمدید خودکار سرویس #{code}\n"
    buttons = []
    if policy:
        text += f"وضعیت: {STATES[policy.state]}\nمبلغ هر تمدید: {policy.per_charge_cap:,} تومان\nسقف ماهانه همین سرویس: {policy.monthly_cap:,} تومان\n"
        if policy.reason:
            text += REASONS.get(policy.reason, policy.reason) + "\n"
        buttons.append([btn("خاموش کردن تمدیدهای بعدی", f"off:{code}")])
    else:
        text += "رضایت فعال برای شما ثبت نشده است.\n"
    if attempt and attempt.user_id == event.sender_id:
        text += f"درخواست پرداخت‌شده ناتمام: {attempt.token}\nدوباره پرداخت نکنید.\n"
        buttons.append([btn("پیگیری بدون برداشت دوباره", f"retry:{attempt.token}")])
    elif config.auto_renew_enabled:
        buttons.append([btn("انتخاب پلن و سقف / تأیید جدید", f"plans:{code}:0")])
    text += "ماه = ماه میلادی به وقت تهران. تغییر قیمت/پلن تأیید جدید می‌خواهد. خاموش‌کردن، برداشت‌های آینده را متوقف می‌کند؛ درخواست قبلاً پرداخت‌شده باید تعیین تکلیف شود."
    buttons.append([btn("رسیدهای من", "receipts:0")])
    await event.respond(text, buttons=buttons, parse_mode=None)


async def plans(event, code, page=0):
    async with Session() as session:
        serv, _ = await ar._available(session, event.sender_id, code)
    plans = []
    for plan in await PlanManager().get_all_plans(panel_code=serv.in_panel):
        try:
            ar.plan_snapshot(plan)
            plans.append(plan)
        except ValueError:
            pass
    plans.sort(key=lambda p: (p.duration, p.storage, p.id))
    buttons = [
        [btn(f"{p.storage:g} گیگ · {p.duration} روز · {int(p.price):,} تومان", f"cap:{code}:{p.id}")]
        for p in plans[page * 8 : page * 8 + 8]
    ]
    if page:
        buttons.append([btn("قبلی", f"plans:{code}:{page - 1}")])
    if len(plans) > page * 8 + 8:
        buttons.append([btn("بعدی", f"plans:{code}:{page + 1}")])
    await event.respond(
        "پلن تمدید را انتخاب کنید؛ این مرحله هنوز هیچ رضایت یا برداشتی ثبت نمی‌کند.\nفقط پلن حجمی همان پنل، بدون ریست و با مدت حداقل ۲ روز."
        if plans
        else "پلن سازگار موجود نیست.",
        buttons=buttons or None,
        parse_mode=None,
    )


async def quote(event, code, plan_id, multiple):
    value = await ar.make_consent(event.sender_id, code, plan_id, multiple)
    p = value.snapshot["plan"]
    await event.respond(
        f"تأیید رضایت تمدید دوره‌ای سرویس #{code}\n"
        f"پلن: {p['storage']:g} گیگ، {p['duration']} روز\nمبلغ و سقف هر برداشت: {p['price']:,} تومان\n"
        f"سقف ماه میلادی همین سرویس: {value.monthly_cap:,} تومان (به وقت تهران)\n"
        "از کیف پول ربات، نه کارت بانکی. تمدید از ۲۴ ساعت قبل از انقضا، فقط بعد از اعلان موفق مبلغ و گذشت حداقل ۶ ساعت.\n"
        "حجم باقی‌مانده حفظ و حجم پلن اضافه می‌شود؛ مصرف ریست نمی‌شود. مدت باقی‌مانده از دست نمی‌رود.\n"
        "کمبود موجودی: بررسی مجدد تا ۴۸ ساعت پس از انقضا؛ تضمین اتصال بی‌وقفه نیست.\n"
        "تغییر قیمت/مشخصات پلن موجب توقف و درخواست تأیید جدید می‌شود. تخفیف و برداشت مستقیم از کارت در این قابلیت نیست.\n"
        "تا خاموش‌کردن شما، دوره‌های بعدی نیز طبق همین شروط تمدید می‌شوند. تأییدیه ۱۵ دقیقه اعتبار دارد.",
        buttons=[
            [btn("با این مبلغ و سقف، تمدید دوره‌ای را فعال کن", f"confirm:{value.token}")],
            [btn("انصراف؛ بدون تغییر رضایت فعلی", f"view:{code}")],
        ],
        parse_mode=None,
    )


async def receipts(event, page=0):
    async with Session() as session:
        await require_user(session, event.sender_id)
        rows = list(
            (
                await session.scalars(
                    select(Attempt)
                    .where(Attempt.user_id == event.sender_id)
                    .order_by(Attempt.created_at.desc(), Attempt.token)
                    .offset(min(page, 10000) * 8)
                    .limit(9)
                )
            ).all()
        )
    text = "رسیدهای تمدید خودکار (شارژ کیف پول نیست):\n" + "\n\n".join(
        f"سرویس {r.service_code} · {r.price:,} تومان · {r.status}\nرسید: {r.token}" for r in rows[:8]
    )
    buttons = [[btn(f"پیگیری سرویس {r.service_code}", f"retry:{r.token}")] for r in rows[:8] if r.status in ar.PENDING]
    if page:
        buttons.append([btn("قبلی", f"receipts:{page - 1}")])
    if len(rows) > 8:
        buttons.append([btn("بعدی", f"receipts:{page + 1}")])
    await event.respond(text if rows else "رسید تمدید خودکاری ندارید.", buttons=buttons or None, parse_mode=None)


async def admin(event, page=0):
    if event.sender_id not in ADMIN_ID:
        raise ValueError("این بخش فقط برای مالک ربات است.")
    async with Session() as session:
        config = await settings(session)
        rows = list(
            (
                await session.scalars(
                    select(Attempt)
                    .where(Attempt.status.in_(ar.PENDING))
                    .order_by(Attempt.created_at)
                    .offset(min(page, 10000) * 8)
                    .limit(9)
                )
            ).all()
        )
        counts = dict((await session.execute(select(Policy.state, func.count()).group_by(Policy.state))).all())
    buttons = [
        [
            btn(
                "خاموش‌کردن برداشت‌های جدید" if config.auto_renew_enabled else "روشن‌کردن قابلیت (نیازمند رضایت مشتری)",
                "switch",
            )
        ]
    ]
    buttons += [[btn(f"پیگیری {r.service_code} · {r.status}", f"retry:{r.token}")] for r in rows[:8]]
    if page:
        buttons.append([btn("قبلی", f"admin:{page - 1}")])
    if len(rows) > 8:
        buttons.append([btn("بعدی", f"admin:{page + 1}")])
    await event.respond(
        f"مدیریت تمدید خودکار\nقابلیت: {'روشن' if config.auto_renew_enabled else 'خاموش'}\nبرنامه‌ها: {counts}\n"
        "کلید عمومی فقط برداشت جدید را متوقف می‌کند؛ تطبیق درخواست پرداخت‌شده ادامه دارد.\n"
        "برای خاموش‌کردن یک سرویس: /autorenewoff CODE\n"
        "موارد review خودکار بازنویسی نمی‌شوند؛ پنل و رسید را بررسی و سپس همین درخواست را پیگیری کنید.",
        buttons=buttons,
        parse_mode=None,
    )


async def toggle(actor):
    if actor not in ADMIN_ID:
        raise ValueError("اجازه تغییر تنظیمات ندارید.")
    async with Session() as session, session.begin():
        config = await session.scalar(select(Settings).with_for_update())
        if not config:
            raise ValueError("تنظیمات در دسترس نیست.")
        value = not config.auto_renew_enabled
        for column, updated in resolve_settings_update_kwargs(config, auto_renew_enabled=value).items():
            setattr(config, column, updated)
        session.add(
            CustomerExperienceAudit(
                actor_id=actor, action="auto_renew_global", detail=str(value), created_at=ar.now_ts()
            )
        )


async def retry(event, token):
    if len(token) != 32 or any(c not in "0123456789abcdef" for c in token):
        raise ValueError("شناسه درخواست نامعتبر است.")
    async with Session() as session, session.begin():
        await require_user(session, event.sender_id)
        row = await session.get(Attempt, token)
        if not row or (row.user_id != event.sender_id and event.sender_id not in ADMIN_ID):
            raise ValueError("درخواست متعلق به شما نیست یا پیدا نشد.")
        session.add(
            CustomerExperienceAudit(
                actor_id=event.sender_id, action="auto_renew_retry", detail=token, created_at=ar.now_ts()
            )
        )
    # Reconciliation NEVER creates a new debit. It is allowed when recurring consent is off.
    result = await ar.reconcile(token)
    await event.respond(
        "نتیجه: " + result.status if result else "نتیجه هنوز قطعی نیست؛ دوباره پرداخت نکنید. بررسی پنل/مدیر لازم است.",
        parse_mode=None,
    )


async def callback(event):
    if not event.is_private:
        await event.answer("فقط در گفتگوی خصوصی ربات.", alert=True)
        raise events.StopPropagation
    try:
        parts = event.data.decode("ascii").split(":")
        action, args = parts[1], parts[2:]
        await event.answer()
        async with Session() as session:
            await require_user(session, event.sender_id)
        if action == "list":
            await list_services(event, num(args[0]))
        elif action == "view":
            await view(event, num(args[0]))
        elif action == "plans":
            await plans(event, num(args[0]), min(num(args[1]), 10000))
        elif action == "cap":
            code, plan_id = num(args[0]), num(args[1])
            async with Session() as session:
                await owned_service(session, event.sender_id, code)
            await event.respond(
                "سقف ماهانه همین سرویس را انتخاب کنید؛ مبلغ دقیق در مرحله تأیید نهایی نمایش داده می‌شود.",
                buttons=[[btn(f"حداکثر هزینه {n} تمدید در ماه", f"quote:{code}:{plan_id}:{n}")] for n in (1, 2, 3)],
                parse_mode=None,
            )
        elif action == "quote":
            await quote(event, num(args[0]), num(args[1]), num(args[2]))
        elif action == "confirm":
            from app.services.billing.direct_pay_store import cancel_pending_for_user
            from app.telegram.state import set_step

            result = await ar.confirm(event.sender_id, args[0])
            await set_step(event.sender_id, "home")
            await cancel_pending_for_user(event.sender_id)
            await event.respond("رضایت تمدید دوره‌ای ثبت شد؛ در این مرحله مبلغی کسر نشد.", parse_mode=None)
            await view(event, result.service_code)
        elif action == "off":
            unfinished = await ar.disable(event.sender_id, num(args[0]))
            await event.respond(
                "برداشت‌های آینده خاموش شد."
                + (" درخواست قبلاً پرداخت‌شده باقی است و باید تطبیق داده شود." if unfinished else ""),
                parse_mode=None,
            )
        elif action == "retry":
            await retry(event, args[0])
        elif action == "receipts":
            await receipts(event, num(args[0]))
        elif action == "admin":
            await admin(event, num(args[0]) if args else 0)
        elif action == "switch":
            await toggle(event.sender_id)
            await admin(event)
        else:
            raise ValueError("دکمه معتبر نیست.")
    except (ValueError, IndexError, UnicodeError) as exc:
        await event.respond(str(exc) if isinstance(exc, ValueError) else "دکمه معتبر نیست.", parse_mode=None)
    except Exception as exc:
        logger.warning("Auto-renew UI deferred error_type=%s", type(exc).__name__)
        await event.respond(
            "عملیات کامل نشد؛ وضعیت یا رسید را بررسی کنید. برای درخواست پرداخت‌شده دوباره پرداخت نکنید.", parse_mode=None
        )
    raise events.StopPropagation


@bot_is_offline
async def command(event):
    if not event.is_private:
        return
    try:
        words = (event.raw_text or "").split()
        cmd = words[0].split("@")[0]
        if cmd == "/autorenew":
            await list_services(event)
        elif cmd == "/autorenewadmin":
            await admin(event)
        elif cmd == "/autorenewoff":
            if len(words) != 2:
                raise ValueError("نمونه: /autorenewoff 12345")
            unfinished = await ar.disable(event.sender_id, num(words[1]))
            await event.respond(
                "برداشت‌های آینده خاموش شد." + (" درخواست پرداخت‌شده ناتمام باقی است." if unfinished else ""),
                parse_mode=None,
            )
    except (ValueError, IndexError) as exc:
        await event.respond(str(exc), parse_mode=None)
    raise events.StopPropagation


def register(client):
    client.add_event_handler(callback, events.CallbackQuery(pattern=rb"^ar:"))
    client.add_event_handler(
        command, events.NewMessage(incoming=True, pattern=r"^/(autorenew|autorenewadmin|autorenewoff)(?:@\w+)?(?:\s|$)")
    )
