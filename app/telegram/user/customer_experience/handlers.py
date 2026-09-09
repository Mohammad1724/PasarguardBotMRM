"""Telegram UI for tickets, guided setup, conversion and consent. No credentials in tickets/logs."""

import asyncio
import time
from urllib.parse import urlsplit

from sqlalchemy import select
from telethon import Button, events
from telethon.tl.types import KeyboardButtonCopy, KeyboardButtonRow, MessageMediaWebPage
from telethon.utils import pack_bot_file_id

from app import Kenzo
from app.db.base import AsyncSessionLocal as Session
from app.db.crud.keyboards import get_button_text
from app.db.crud.plans import PlanManager
from app.db.crud.settings import SettingsManager
from app.db.models.customer_experience import TrialConversion
from app.db.models.panels import Panels
from app.logger import get_logger
from app.services.customer_experience import conversion, journeys, tickets
from app.services.customer_experience.common import (
    FLAGS,
    change_setting,
    owned_service,
    require_user,
    settings,
    staff_ids,
)
from app.services.gifts import panel_call
from app.services.subscriptions.links import format_subscription_links_for_message
from app.telegram.keyboards.help import get_help_buttons
from app.telegram.keyboards.home import bhome_buttons
from app.telegram.shared.utils.maintenance import bot_is_offline
from app.telegram.state import get_step, set_step
from config import ADMIN_ID

logger = get_logger(__name__)
OS = {
    "android": ("Android", "V2rayNG", "Download_v2rayng"),
    "ios": ("iPhone / iPad", "Streisand", "IOS_streisand"),
    "windows": ("Windows", "V2rayN", "Download_v2rayn"),
    "macos": ("macOS", "Hiddify", "Download_hiddifyapp"),
}
DIAG = {"quota": "حجم/زمان تمام شده", "import": "ورود ساب مشکل دارد", "network": "وصل نمی‌شود", "other": "سایر"}
FLAG_LABELS = ["تیکت", "راه‌اندازی مرحله‌ای", "تبدیل تست", "پیگیری با رضایت مشتری"]
COMMANDS = {"/tickets", "/supportdesk", "/cx", "/cxstats", "/cxstaff", "/stopfollowups"}


def number(value):
    result = int(value)
    if not 0 <= result < 2**63:
        raise ValueError("شناسه نامعتبر است.")
    return result


def button(text, data):
    return Button.inline(text, "cx:" + data)


async def service_access(user_id, code):
    async with Session() as session:
        await require_user(session, user_id)
        return await owned_service(session, user_id, code), await settings(session)


async def show_inbox(event, *, staff=False, page=0, closed=False):
    rows = await tickets.list_tickets(event.sender_id, staff_queue=staff, page=page, closed=closed)
    buttons = []
    for row in rows[:8]:
        overdue = " ⏱" if row.status in ("open", "in_progress") and time.time() - row.updated_at > 24 * 3600 else ""
        buttons.append(
            [
                button(
                    f"#{row.id} · {tickets.TOPICS[row.topic]} · {tickets.STATUSES[row.status]}{overdue}",
                    f"ticket:{row.id}:0",
                )
            ]
        )
    prefix = "queue" if staff else "inbox"
    nav = []
    if page:
        nav.append(button("قبلی", f"{prefix}:{page - 1}:{int(closed)}"))
    if len(rows) > 8:
        nav.append(button("بعدی", f"{prefix}:{page + 1}:{int(closed)}"))
    if nav:
        buttons.append(nav)
    if staff:
        buttons.append([button("تیکت‌های باز" if closed else "تیکت‌های بسته", f"queue:0:{int(not closed)}")])
    else:
        config = await SettingsManager().get_settings()
        if config and config.cx_tickets_enabled and config.support_mode:
            buttons.append([button("تیکت جدید", "topics:0")])
    await event.respond(
        "📨 صندوق تیکت‌ها\nبرای مشکل اشتراک، از دکمه «مشکل این سرویس» استفاده کنید.\n⏱ یعنی بیش از ۲۴ ساعت بدون پاسخ/رسیدگی."
        if rows
        else "هنوز تیکتی در این بخش نیست.",
        buttons=buttons or None,
        parse_mode=None,
    )


async def show_ticket(event, ticket_id, before=0):
    row, staff, messages, more = await tickets.history(event.sender_id, ticket_id, before=before)
    owner_line = f"\nکاربر: {row.user_id}" if staff else ""
    await event.respond(
        f"تیکت #{row.id} · {tickets.TOPICS[row.topic]}\nوضعیت: {tickets.STATUSES[row.status]}"
        f"\nسرویس: {row.service_code or 'عمومی'}\nمسئول: {row.assigned_to or 'تعیین نشده'}{owner_line}",
        parse_mode=None,
    )
    for msg in messages:
        label = {"customer": "مشتری", "staff": "پشتیبان", "system": "رویداد"}[msg.kind]
        media = [[button("دریافت پیوست", f"file:{msg.id}")]] if msg.file_id else None
        await event.respond(f"{label} · #{msg.id}\n{msg.text or 'پیوست'}", buttons=media, parse_mode=None)
    buttons = []
    if more:
        buttons.append([button("پیام‌های قدیمی‌تر", f"ticket:{row.id}:{messages[0].id}")])
    if row.status != "closed":
        buttons.append([button("پاسخ", f"reply:{row.id}"), button("بستن", f"status:{row.id}:close")])
        if staff and row.user_id != event.sender_id:
            buttons.append(
                [button("رسیدگی با من", f"status:{row.id}:claim"), button("آزادکردن مسئول", f"status:{row.id}:release")]
            )
            buttons.append([button("پاسخ آماده: به‌روزرسانی ساب", f"template:{row.id}")])
    else:
        buttons.append([button("بازگشایی", f"status:{row.id}:reopen")])
    buttons.append([button("صندوق پشتیبانی" if staff else "تیکت‌های من", "queue:0:0" if staff else "inbox:0:0")])
    await event.respond("عملیات تیکت:", buttons=buttons, parse_mode=None)


async def notify_ticket(row, msg):
    config = await SettingsManager().get_settings()
    recipients = {row.user_id} if msg.kind == "staff" else staff_ids(config)
    for recipient in recipients - {msg.author_id}:
        try:
            async with asyncio.timeout(10):
                await Kenzo.send_message(
                    recipient,
                    f"پیام تازه در تیکت #{row.id} · {tickets.TOPICS[row.topic]}",
                    buttons=[[button("مشاهده تیکت", f"ticket:{row.id}:0")]],
                    parse_mode=None,
                )
        except Exception as exc:
            logger.warning("Ticket notification pending in inbox ticket=%s error_type=%s", row.id, type(exc).__name__)


async def show_plans(event, code, page=0):
    service, config = await service_access(event.sender_id, code)
    pending = await conversion.pending_order(event.sender_id, code)
    if pending:
        await event.respond(
            "درخواست پرداخت‌شده ناتمام دارید. پرداخت تازه نکنید؛ همان درخواست را پیگیری کنید.",
            buttons=[[button("پیگیری بدون برداشت دوباره", f"pay:{pending.token}")]],
            parse_mode=None,
        )
        return
    if not (config.cx_conversion_enabled and config.sale_mode and config.bot_mode and service.is_test is True):
        raise ValueError("تبدیل تست در حال حاضر در دسترس نیست. برای خرید معمولی /buy را بفرستید.")
    from app.services.billing.direct_pay_store import cancel_pending_for_user

    await cancel_pending_for_user(event.sender_id)
    await set_step(event.sender_id, "home")
    plans = []
    for plan in await PlanManager().get_all_plans(panel_code=service.in_panel):
        try:
            conversion.snapshot(plan)
            plans.append(plan)
        except ValueError:
            continue
    plans.sort(key=lambda p: (p.duration, p.storage, p.id))
    buttons = [
        [button(f"{p.storage:g} گیگ · {p.duration} روز · {int(p.price):,} تومان", f"quote:{code}:{p.id}")]
        for p in plans[page * 8 : page * 8 + 8]
    ]
    if page:
        buttons.append([button("قبلی", f"plans:{code}:{page - 1}")])
    if len(plans) > page * 8 + 8:
        buttons.append([button("بعدی", f"plans:{code}:{page + 1}")])
    await event.respond(
        "پلن‌های حجمی همان پنل؛ کانفیگ و لینک عوض نمی‌شوند.\nپرداخت از کیف پول است؛ این نسخه کد تخفیف را روی تبدیل اعمال نمی‌کند."
        if plans
        else "پلن سازگار روی پنل تست وجود ندارد. برای خرید سرویس جداگانه /buy را بفرستید.",
        buttons=buttons or None,
        parse_mode=None,
    )


async def show_quote(event, code, plan_id):
    order = await conversion.quote(event.sender_id, code, plan_id)
    await event.respond(
        f"تبدیل تست #{code}\n{order.plan_snapshot['storage']:g} گیگ · {order.plan_snapshot['duration']} روز"
        f"\nمبلغ نهایی: {order.price:,} تومان از کیف پول"
        "\nحجم باقی‌مانده تست حفظ می‌شود؛ مدت پلن از زمان تأیید شروع می‌شود. مصرف گذشته ریست نمی‌شود."
        "\nنام کاربری، کانفیگ و لینک ساب تغییر نمی‌کنند. تأییدیه ۱۵ دقیقه اعتبار دارد."
        "\nاگر موجودی کافی نیست، /charge را بفرستید و سپس به همین پیام برگردید.",
        buttons=[[button("تأیید مبلغ و تبدیل", f"pay:{order.token}")], [button("بازگشت به پلن‌ها", f"plans:{code}:0")]],
        parse_mode=None,
    )


async def convert(event, token, *, admin_retry=False):
    user_id = event.sender_id
    if admin_retry:
        if user_id not in ADMIN_ID:
            raise ValueError("اجازه پیگیری مالی ندارید.")
        async with Session() as session:
            order = await session.get(TrialConversion, token)
            if not order or order.status != "applying":
                raise ValueError("درخواست پرداخت‌شده ناتمام پیدا نشد.")
            user_id = order.user_id
    try:
        async with asyncio.timeout(35):
            order = await conversion.execute(user_id, token)
    except conversion.InsufficientBalance:
        raise
    except Exception as exc:
        logger.warning("Conversion deferred token=%s error_type=%s", token, type(exc).__name__)
        order = await conversion.get_order(user_id, token)
        if order.status == "applying":
            await event.respond(
                "درخواست محفوظ است اما نتیجه پنل هنوز قطعی نشده. دوباره پرداخت نکنید."
                "\nبا همان دکمه پیگیری کنید؛ در صورت تداوم، شماره زیر را به پشتیبانی بدهید.\n" + token,
                buttons=[[button("پیگیری همان درخواست", f"retry:{token}" if admin_retry else f"pay:{token}")]],
                parse_mode=None,
            )
            return
        if order.status == "refunded":
            raise ValueError(
                "پنل درخواست را رد کرد؛ مبلغ دقیقاً به کیف پول برگشت. می‌توانید دوباره پلن انتخاب کنید."
            ) from None
        if isinstance(exc, ValueError):
            raise
        raise ValueError("ارتباط با پنل برقرار نشد؛ وضعیت درخواست را دوباره بررسی کنید.") from None
    await event.respond(
        f"✅ تبدیل انجام شد.\nکد سرویس: {order.service_code}\nمبلغ: {order.price:,} تومان"
        f"\nرسید: {order.token}\nهمان کانفیگ را استفاده کنید؛ ساب را در کلاینت به‌روز کنید.",
        buttons=None
        if admin_retry
        else [
            [button("راهنمای اتصال", f"guide:{order.service_code}")],
            [Button.inline("مشاهده سرویس", f"service_info:{order.service_code}")],
        ],
        parse_mode=None,
    )


async def guide(event, code, os_key=None, *, installed=False):
    service, config = await service_access(event.sender_id, code)
    if not config.cx_onboarding_enabled:
        raise ValueError("راهنمای مرحله‌ای فعلاً غیرفعال است؛ /help را بفرستید.")
    await journeys.track_event(event.sender_id, code, "guide_started")
    if os_key is None:
        await event.respond(
            "۱ از ۳ · دستگاهت چیست؟",
            buttons=[[button(value[0], f"os:{code}:{key}")] for key, value in OS.items()],
            parse_mode=None,
        )
        return
    if os_key not in OS:
        raise ValueError("سیستم‌عامل نامعتبر است.")
    label, app, download = OS[os_key]
    if not installed:
        await event.respond(
            f"۲ از ۳ · {label}\nکلاینت پیشنهادی: {app}. نسخه سازگار با دستگاهت را از بخش دانلود انتخاب کن."
            "\nاگر برنامه نصب است، مرحله بعد را بزن.",
            buttons=[
                [Button.inline(f"دانلود {app}", download)],
                [button("همه برنامه‌ها و آموزش‌ها", f"downloads:{code}:{os_key}")],
                [button("نصب است؛ مرحله بعد", f"setup:{code}:{os_key}")],
            ],
            parse_mode=None,
        )
        return
    async with Session() as session:
        panel = await session.get(Panels, service.in_panel)
    if not panel or not service.panel_userid:
        raise ValueError("اطلاعات پنل کافی نیست؛ از پشتیبانی کمک بگیرید.")
    async with asyncio.timeout(20):
        user = await panel_call(panel, "get_user_by_id", user_id=service.panel_userid)
    url = user.subscription_url
    url = url if url.startswith("http") else f"{panel.base_url}{url}"
    _, url = format_subscription_links_for_message(panel, url)
    if urlsplit(url).scheme not in ("http", "https") or not urlsplit(url).netloc:
        raise ValueError("لینک ساب نامعتبر است؛ با پشتیبانی تماس بگیرید.")
    instruction = {
        "android": "در V2rayNG از + گزینه Import config from clipboard را بزن؛ سپس ساب را به‌روز و اتصال را روشن کن.",
        "ios": "در Streisand از + گزینه Import from Clipboard را انتخاب کن و اتصال VPN را تأیید کن.",
        "windows": "در V2rayN بخش Subscription groups، یک گروه با این URL بساز، Update subscription بزن و یک سرور انتخاب کن.",
        "macos": "در Hiddify از +، Import from clipboard را انتخاب کن؛ بعد دکمه اتصال را بزن.",
    }[os_key]
    await event.respond(
        f"۳ از ۳ · لینک را کپی کن.\n{instruction}\nلینک ساب خصوصی است؛ آن را عمومی نفرست.",
        buttons=[
            [KeyboardButtonCopy("کپی لینک ساب", url)],
            [Button.inline("QR و اطلاعات سرویس", f"service_info:{code}")],
            [button("متصل شدم", f"connected:{code}"), button("متصل نشدم", f"diagnose:{code}")],
        ],
        parse_mode=None,
    )


async def diagnosis(event, code):
    service, config = await service_access(event.sender_id, code)
    if not config.cx_onboarding_enabled:
        raise ValueError("راهنمای مرحله‌ای غیرفعال است.")
    summary = "وضعیت زنده پنل در دسترس نیست."
    try:
        async with Session() as session:
            panel = await session.get(Panels, service.in_panel)
        if panel and service.panel_userid:
            async with asyncio.timeout(15):
                user = await panel_call(panel, "get_user_by_id", user_id=service.panel_userid)
            limit = int(user.data_limit or 0)
            summary = f"وضعیت اعلام‌شده پنل: {getattr(user.status, 'value', user.status)}"
            if limit:
                summary += f"\nحجم باقی‌مانده: {max(0, limit - int(user.used_traffic or 0)) / 1024**3:.2f} گیگ"
    except Exception:
        pass
    rows = (
        [[button(label, f"new:{code}:connection:{key}")] for key, label in DIAG.items()]
        if config.cx_tickets_enabled
        else []
    )
    rows.append([button("راهنما از ابتدا", f"guide:{code}")])
    await event.respond(
        summary + "\nاین بررسی، کیفیت اتصال روی اینترنت شما را ثابت نمی‌کند."
        "\nابتدا ساب را به‌روز کنید و ساعت دستگاه را خودکار بگذارید."
        "\nبرای ارسال نتیجه به پشتیبانی، نوع مشکل را انتخاب کنید؛ سپس توضیح یا تصویر بفرستید.",
        buttons=rows,
        parse_mode=None,
    )


async def control_panel(event):
    if event.sender_id not in ADMIN_ID:
        raise ValueError("این بخش مخصوص مالک ربات است.")
    config = await SettingsManager().get_settings()
    buttons = [
        [button(f"{'✅' if getattr(config, key) else '❌'} {label}", f"toggle:{i}")]
        for i, (key, label) in enumerate(zip(FLAGS, FLAG_LABELS, strict=True))
    ]
    buttons.append([Button.inline("تمدید دوره‌ای از کیف پول — نسخه دوم", "ar:admin:0")])
    buttons += [
        [button("آمار ۳۰ روز اخیر", "stats")],
        [button("تبدیل‌های ناتمام", "pending:0")],
        [button("صندوق پشتیبانی", "queue:0:0")],
    ]
    await event.respond(
        "نسخه اول تجربه مشتری\nهمه گزینه‌ها در نصب اولیه خاموش‌اند."
        "\nپیگیری: فقط با رضایت مشتری، حداکثر یک تلاش ارسال، ۱۰ تا ۲۱ به وقت تهران."
        "\nتبدیل: فقط پلن حجمی همان پنل از کیف پول."
        "\nافزودن/حذف پشتیبان بدون دسترسی مالی:\n/cxstaff add 123456\n/cxstaff remove 123456"
        f"\nپشتیبان‌های محدود: {config.cx_support_ids}",
        buttons=buttons,
        parse_mode=None,
    )


async def stats(event):
    if event.sender_id not in ADMIN_ID:
        raise ValueError("این گزارش مخصوص مالک ربات است.")
    totals, states, cohorts, pending = await journeys.report()
    labels = {
        "trial_created": "تست ثبت‌شده",
        "guide_started": "شروع راهنما",
        "connected_self_reported": "اعلام اتصال توسط مشتری",
        "plan_selected": "انتخاب پلن تبدیل",
        "trial_converted": "تبدیل موفق تست",
        "purchase_delivered": "خرید سرویس جداگانه",
    }
    lines = ["آمار ۳۰ روز اخیر؛ هر رویداد حداکثر یک بار برای هر سرویس/کاربر:"]
    lines += [f"{label}: {totals.get(key, 0)}" for key, label in labels.items()]
    lines.append(f"تبدیل پرداخت‌شده ناتمام (همه زمان‌ها): {pending}")
    lines.append("وضعیت پیگیری‌ها: " + str(states))
    for group, (total, paid) in cohorts.items():
        rate = f"{100 * paid / total:.1f}%" if total else "داده کافی نیست"
        lines.append(f"{'گروه پیام' if group == 'message' else 'گروه کنترل'}: {paid}/{total} · {rate}")
    lines.append(
        "گروه‌ها: فقط رضایت‌داده‌هایی که ۷ روز از پایان تستشان گذشته. خرید تا ۷ روز پس از پایان تست شمرده می‌شود؛ ۲۰٪ تخصیص ثابت کنترل. این گزارش اثبات اثر تبلیغ نیست. اتصال هم خوداظهاری مشتری است."
    )
    await event.respond("\n".join(lines), parse_mode=None)


async def callback(event):
    if not event.is_private:
        await event.answer("این بخش فقط در گفتگوی خصوصی ربات در دسترس است.", alert=True)
        raise events.StopPropagation
    try:
        parts = event.data.decode("ascii").split(":")
        action, args = parts[1], parts[2:]
        await event.answer()
        async with Session() as session:
            await require_user(session, event.sender_id)
        if action == "inbox":
            await show_inbox(event, page=number(args[0]))
        elif action == "queue":
            await show_inbox(event, staff=True, page=number(args[0]), closed=args[1] == "1")
        elif action == "ticket":
            await show_ticket(event, number(args[0]), number(args[1]))
        elif action == "topics":
            code = number(args[0])
            if code:
                await service_access(event.sender_id, code)
            await event.respond(
                "موضوع تیکت را انتخاب کنید:",
                buttons=[[button(label, f"new:{code}:{key}:other")] for key, label in tickets.TOPICS.items()],
                parse_mode=None,
            )
        elif action == "new":
            code, topic, diag = number(args[0]), args[1], args[2]
            if topic not in tickets.TOPICS or diag not in DIAG:
                raise ValueError("موضوع نامعتبر است.")
            if code:
                await service_access(event.sender_id, code)
            await set_step(event.sender_id, f"cx_new:{code}:{topic}:{diag}")
            if await get_step(event.sender_id) != f"cx_new:{code}:{topic}:{diag}":
                raise ValueError("ثبت وضعیت ممکن نشد؛ کمی بعد تلاش کنید.")
            await event.respond(
                "شرح مشکل یا عکس/فایل را بفرستید؛ اولین پیام تیکت را ثبت می‌کند."
                "\nحداکثر متن ۳۰۰۰ کاراکتر و پیوست ۱۰ مگابایت (عکس، ویدئو، PDF یا متن)."
                "\nرمز، توکن ربات یا اطلاعات کارت بانکی نفرستید. لغو: /cancel",
                parse_mode=None,
            )
        elif action == "reply":
            tid = number(args[0])
            row, _, _, _ = await tickets.history(event.sender_id, tid)
            if row.status == "closed":
                raise ValueError("ابتدا تیکت را باز کنید.")
            await set_step(event.sender_id, f"cx_reply:{tid}")
            if await get_step(event.sender_id) != f"cx_reply:{tid}":
                raise ValueError("ثبت وضعیت ممکن نشد؛ کمی بعد تلاش کنید.")
            await event.respond(f"پاسخ تیکت #{tid} را به صورت متن یا فایل بفرستید. لغو: /cancel", parse_mode=None)
        elif action == "status":
            await tickets.change_status(event.sender_id, number(args[0]), args[1])
            await show_ticket(event, number(args[0]))
        elif action == "template":
            row, staff, _, _ = await tickets.history(event.sender_id, number(args[0]))
            if not staff or row.user_id == event.sender_id:
                raise ValueError("دسترسی پشتیبانی ندارید.")
            await event.respond(
                "متن آماده (ارسال خودکار نیست؛ با دکمه پاسخ می‌توانید ویرایش و ارسال کنید):\n"
                "لطفاً ساب را در برنامه به‌روز کنید، ساعت دستگاه را خودکار بگذارید و نام برنامه و تصویر خطا را بفرستید. لینک خصوصی ساب را عمومی منتشر نکنید.",
                buttons=[[button("پاسخ به تیکت", f"reply:{row.id}")]],
                parse_mode=None,
            )
        elif action == "file":
            file_id = await tickets.attachment(event.sender_id, number(args[0]))
            try:
                await Kenzo.send_file(
                    event.sender_id, file_id, caption="پیوست تیکت؛ فایل کاربر است، قبل از بازکردن بررسی کنید."
                )
            except Exception:
                raise ValueError("پیوست در تلگرام قابل بازیابی نیست؛ از فرستنده بخواهید دوباره ارسال کند.") from None
        elif action in ("guide", "os", "setup"):
            await guide(event, number(args[0]), args[1] if len(args) > 1 else None, installed=action == "setup")
        elif action == "downloads":
            _, config = await service_access(event.sender_id, number(args[0]))
            if not config.cx_onboarding_enabled or args[1] not in OS:
                raise ValueError("درخواست نامعتبر است.")
            rows = await get_help_buttons(event.sender_id)
            rows.append([button("نصب شد؛ ادامه", f"setup:{args[0]}:{args[1]}")])
            await event.respond("برنامه سازگار با دستگاهتان را انتخاب کنید:", buttons=rows, parse_mode=None)
        elif action == "connected":
            _, config = await service_access(event.sender_id, number(args[0]))
            if not config.cx_onboarding_enabled:
                raise ValueError("راهنمای مرحله‌ای غیرفعال است.")
            await journeys.track_event(event.sender_id, number(args[0]), "connected_self_reported")
            await event.respond(
                "عالی! اعلام اتصال شما ثبت شد. برای کمک بعدی از کارت سرویس استفاده کنید.", parse_mode=None
            )
        elif action == "diagnose":
            await diagnosis(event, number(args[0]))
        elif action in ("plans", "follow"):
            code = number(args[0])
            if action == "follow":
                await journeys.click(event.sender_id, code)
            await show_plans(event, code, number(args[1]) if len(args) > 1 else 0)
        elif action == "quote":
            await show_quote(event, number(args[0]), number(args[1]))
        elif action in ("pay", "retry"):
            if len(args[0]) != 32 or any(c not in "0123456789abcdef" for c in args[0]):
                raise ValueError("شناسه سفارش نامعتبر است.")
            await convert(event, args[0], admin_retry=action == "retry")
        elif action == "remind":
            await journeys.consent(event.sender_id, number(args[0]), allowed=True)
            await event.respond(
                "رضایت شما برای حداکثر یک پیام پیشنهاد خرید پس از پایان تست ثبت شد."
                "\nممکن است برای سنجش عملکرد در گروه بدون پیام قرار بگیرید. لغو هر زمان: /stopfollowups",
                buttons=[[button("لغو یادآوری", "optout")]],
                parse_mode=None,
            )
        elif action == "optout":
            await journeys.consent(event.sender_id, allowed=False)
            await event.respond("یادآوری تست‌های ثبت‌شده شما لغو شد.", parse_mode=None)
        elif action == "panel":
            await control_panel(event)
        elif action == "toggle":
            if event.sender_id not in ADMIN_ID:
                raise ValueError("اجازه تغییر تنظیمات ندارید.")
            index = number(args[0])
            key = FLAGS[index]
            config = await SettingsManager().get_settings()
            await change_setting(event.sender_id, key, not getattr(config, key))
            await control_panel(event)
        elif action == "stats":
            await stats(event)
        elif action == "pending":
            if event.sender_id not in ADMIN_ID:
                raise ValueError("دسترسی مالی ندارید.")
            page = number(args[0])
            async with Session() as session:
                rows = list(
                    (
                        await session.scalars(
                            select(TrialConversion)
                            .where(TrialConversion.status == "applying")
                            .order_by(TrialConversion.created_at)
                            .offset(page * 8)
                            .limit(9)
                        )
                    ).all()
                )
            buttons = [[button(f"سرویس {r.service_code} · {r.price:,} تومان", f"retry:{r.token}")] for r in rows[:8]]
            if len(rows) > 8:
                buttons.append([button("بعدی", f"pending:{page + 1}")])
            if page:
                buttons.append([button("قبلی", f"pending:{page - 1}")])
            await event.respond(
                "درخواست‌های ناتمام؛ پیگیری فقط وضعیت هدف ذخیره‌شده را تطبیق می‌دهد و مبلغ تازه کسر نمی‌کند."
                "\nدر صورت اختلاف وضعیت، تغییر دستی/بازپرداخت را بدون بررسی پنل انجام ندهید."
                if rows
                else "تبدیل ناتمامی نیست.",
                buttons=buttons or None,
                parse_mode=None,
            )
        else:
            raise ValueError("دکمه معتبر نیست.")
    except (ValueError, IndexError, UnicodeError) as exc:
        await event.respond(str(exc) if isinstance(exc, ValueError) else "دکمه نامعتبر است.", parse_mode=None)
    except Exception as exc:
        logger.error("CX callback failed action_type=%s", type(exc).__name__)
        await event.respond(
            "عملیات کامل نشد؛ کمی بعد از همین دکمه تلاش کنید. اگر پرداخت در حال پردازش است، دوباره پرداخت نکنید.",
            parse_mode=None,
        )
    raise events.StopPropagation


async def message_filter(event):
    if not event.is_private:
        return False
    text = event.raw_text or ""
    command = text.split(maxsplit=1)[0].split("@")[0] if text else ""
    if command in COMMANDS:
        return True
    step = await get_step(event.sender_id) or ""
    if not step.startswith(("cx_new:", "cx_reply:")):
        return False
    if text in ("/cancel", "🏠 بازگشت"):
        return True
    # Navigation must not become a support message, even with custom menu labels.
    if text.startswith("/"):
        await set_step(event.sender_id, "home")
        return False
    for key, default in (
        ("bt.menu_support", "☎️ پشتیبانی"),
        ("bt.menu_buy_service", "🛍 خرید سرویس"),
        ("bt.menu_my_services", "🔑 سرویس های من"),
        ("bt.menu_add_balance", "💰 افزایش موجودی"),
        ("bt.menu_help", "📚 راهنما"),
        ("bt.menu_profile", "🙍 پروفایل من"),
        ("bt.menu_get_trial", "🎁 دریافت تست"),
        ("bt.menu_admin_panel", "⚙️ پنل مدیریت"),
        ("bt.menu_advanced_settings", "⚙️ تنظیمات پیشرفته"),
        ("bt.menu_uptime", "🔋 وضعیت سرویس ها"),
    ):
        if text in (default, await get_button_text(key, default)):
            await set_step(event.sender_id, "home")
            return False
    return True


@bot_is_offline
async def message(event):
    try:
        text = event.raw_text or ""
        words = text.split()
        command = words[0].split("@")[0] if words else ""
        if command in COMMANDS:
            await set_step(event.sender_id, "home")
            if command in ("/tickets", "/supportdesk"):
                await show_inbox(event, staff=command == "/supportdesk")
            elif command == "/cx":
                await control_panel(event)
            elif command == "/cxstats":
                await stats(event)
            elif command == "/stopfollowups":
                await journeys.consent(event.sender_id, allowed=False)
                await event.respond("یادآوری‌های تست شما لغو شد.", parse_mode=None)
            elif command == "/cxstaff":
                if event.sender_id not in ADMIN_ID:
                    raise ValueError("فقط مالک ربات می‌تواند پشتیبان تعیین کند.")
                if len(words) != 3 or words[1] not in ("add", "remove") or number(words[2]) == 0:
                    raise ValueError("نمونه: /cxstaff add 123456 یا /cxstaff remove 123456")
                config = await SettingsManager().get_settings()
                ids = {int(i) for i in config.cx_support_ids}
                if words[1] == "add":
                    ids.add(number(words[2]))
                else:
                    ids.discard(number(words[2]))
                if len(ids) > 20:
                    raise ValueError("حداکثر ۲۰ پشتیبان محدود مجاز است.")
                await change_setting(event.sender_id, "cx_support_ids", sorted(ids))
                await event.respond(
                    "دسترسی پشتیبانی به‌روز شد. پشتیبان باید /start و سپس /supportdesk را بفرستد.", parse_mode=None
                )
            raise events.StopPropagation
        if text in ("/cancel", "🏠 بازگشت"):
            await set_step(event.sender_id, "home")
            await event.respond("لغو شد.", buttons=await bhome_buttons(event.sender_id, "fa"), parse_mode=None)
            raise events.StopPropagation
        file_id = None
        if event.message.media and not isinstance(event.message.media, MessageMediaWebPage):
            media = event.message.photo or event.message.document
            file = event.message.file
            mime = getattr(file, "mime_type", "") or ""
            if (
                not media
                or not file
                or (file.size or 0) > tickets.MAX_FILE_BYTES
                or not (
                    event.message.photo
                    or mime.startswith(("image/", "video/"))
                    or mime in ("application/pdf", "text/plain")
                )
            ):
                raise ValueError("فقط عکس، ویدئو، PDF یا متن تا ۱۰ مگابایت مجاز است.")
            file_id = pack_bot_file_id(media)
            if not file_id:
                raise ValueError("پیوست قابل ذخیره نیست؛ دوباره ارسال کنید.")
        step = (await get_step(event.sender_id) or "").split(":")
        if step[0] == "cx_new":
            code, topic, diag = number(step[1]), step[2], step[3]
            if topic not in tickets.TOPICS or diag not in DIAG:
                raise ValueError("جلسه نامعتبر است؛ دوباره از کارت سرویس وارد شوید.")
            if diag != "other":
                text = f"مسیر عیب‌یابی: {DIAG[diag]}\n{text}"
            kwargs = {"service_code": code or None, "topic": topic}
        elif step[0] == "cx_reply":
            kwargs = {"ticket_id": number(step[1])}
        else:
            raise ValueError("جلسه منقضی شده؛ دوباره دکمه پاسخ/تیکت جدید را بزنید.")
        row, msg, created = await tickets.add_message(
            event.sender_id,
            text,
            file_id=file_id,
            source_chat_id=event.chat_id,
            source_message_id=event.message.id,
            **kwargs,
        )
        await set_step(event.sender_id, "home")
        await event.respond(
            f"پیام در تیکت #{row.id} ذخیره شد. وضعیت و پاسخ را از همین تیکت پیگیری کنید.",
            buttons=[[button("مشاهده / ادامه گفتگو", f"ticket:{row.id}:0")]],
            parse_mode=None,
        )
        if created:
            await notify_ticket(row, msg)
    except events.StopPropagation:
        raise
    except (ValueError, IndexError) as exc:
        await event.respond(str(exc) if isinstance(exc, ValueError) else "جلسه منقضی شده است.", parse_mode=None)
    except Exception as exc:
        logger.error("CX message failed error_type=%s", type(exc).__name__)
        await event.respond("عملیات کامل نشد. پیش از ارسال مجدد، /tickets را برای بررسی سابقه ببینید.", parse_mode=None)
    raise events.StopPropagation


async def service_button_rows(service, config):
    """Shared by delivery messages and service cards; every callback checks access again."""
    rows = []
    code = service.code
    if service.is_test is True:
        if config.cx_conversion_enabled and config.sale_mode:
            rows.append(KeyboardButtonRow([button("تبدیل تست به اشتراک اصلی", f"plans:{code}:0")]))
        if config.cx_followup_enabled:
            rows.append(KeyboardButtonRow([button("مایلم یک یادآوری پیشنهاد خرید بگیرم", f"remind:{code}")]))
    return rows


def register(client):
    client.add_event_handler(callback, events.CallbackQuery(pattern=rb"^cx:"))
    client.add_event_handler(message, events.NewMessage(incoming=True, func=message_filter))
