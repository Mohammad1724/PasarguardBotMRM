"""Allowlisted resources. Drafts never contain credentials or executable code."""

import hashlib
import json
import math
import string

from sqlalchemy import func, select

from app.db.models.admin_app import AdminGrant, AdminState
from app.db.models.auto_renew import AutoRenewPolicy as Policy
from app.db.models.bot_text import BotText
from app.db.models.customer_experience import SupportTicket, TicketMessage
from app.db.models.keyboards import KeyboardButton
from app.db.models.panels import Panels
from app.db.models.plans import Plan
from app.db.models.services import Service
from app.db.models.settings import SETTINGS_SECTION_DEFAULTS, Settings, resolve_settings_update_kwargs
from app.db.models.user import User
from app.services.admin_app.auth import PERMISSIONS, fail, require
from app.telegram.keyboards.registry import (
    KEYBOARD_BUTTON_DEFAULT_STYLES,
    KEYBOARD_BUTTON_DEFAULTS,
)
from app.telegram.keyboards.texts import TEXT_KEYS_CONFIG
from config import ADMIN_ID

HIDDEN_SETTINGS = {"zarinpal_merchant", "zarinpal_callback_url", "stars_history_offset", "cx_support_ids"}
SECTION_KEYS = {
    name: {k: v for k, v in values.items() if k not in HIDDEN_SETTINGS}
    for name, values in SETTINGS_SECTION_DEFAULTS.items()
}
TEXTS = {item["key"]: item for items in TEXT_KEYS_CONFIG.values() for item in items}
HOME_KEYS = [k for k in KEYBOARD_BUTTON_DEFAULTS if k.startswith("bt.") and k != "bt.menu_admin_panel"]
REQUIRED_HOME = {"bt.menu_my_services", "bt.menu_add_balance"}
PLAN_FIELDS = (
    "id",
    "price",
    "storage",
    "duration",
    "panel_code",
    "plan_type",
    "data_limit_reset_strategy",
    "ip_limit",
    "display_button_text",
    "button_style",
    "button_icon",
    "enabled",
)


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def permission(actor, entity, target):
    if entity == "grants":
        if not actor["owner"]:
            fail(403, "فقط مالک می‌تواند دسترسی بدهد.")
        return
    mappings = {
        "settings": "payments.manage" if target == "payment_settings" else "settings.manage",
        "buttons": "appearance.manage",
        "layout": "appearance.manage",
        "texts": "appearance.manage",
        "plans": "plans.manage",
        "users": "users.manage",
        "tickets": "tickets.manage",
        "services": "services.manage",
    }
    if entity not in mappings:
        fail(404, "بخش نامعتبر")
    require(actor, mappings[entity])


def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        fail(422, f"عدد صحیح بین {low} و {high} لازم است.")
    return value


def number(value, low, high):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        fail(422, "عدد خارج از محدوده است.")
    return value


def label(value, limit=64):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 for c in value):
        fail(422, "متن کوتاه، غیرخالی و بدون نویسه کنترلی لازم است.")
    return value.strip()


def icon(value):
    if value in (None, ""):
        return None
    if not isinstance(value, str) or not value.isascii() or not value.isdigit() or not 0 < int(value) < 2**63:
        fail(422, "شناسه ایموجی نامعتبر است.")
    return str(int(value))


def style(value):
    if value not in (None, "", "primary", "success", "danger"):
        fail(422, "استایل دکمه نامعتبر است.")
    return value or None


def fields(data, allowed):
    if not isinstance(data, dict) or set(data) - set(allowed):
        fail(422, "فیلد ناشناخته یا غیرمجاز")


async def snapshot(session, entity, target, *, lock=False):
    def query(q):
        return q.with_for_update() if lock else q

    if entity == "settings":
        if target not in SECTION_KEYS:
            fail(404, "بخش تنظیمات نامعتبر است.")
        row = await session.scalar(query(select(Settings)))
        if not row:
            fail(409, "ابتدا تنظیمات ربات را راه‌اندازی کنید.")
        return {k: getattr(row, k, default) for k, default in SECTION_KEYS[target].items()}
    if entity == "buttons":
        if target not in KEYBOARD_BUTTON_DEFAULTS:
            fail(404, "دکمه ناشناخته")
        row = await session.scalar(query(select(KeyboardButton).where(KeyboardButton.button_key == target)))
        default_style, default_icon = KEYBOARD_BUTTON_DEFAULT_STYLES.get(target, (None, None))
        cleared = row is not None and row.button_style == ""
        effective_style = None if cleared else (row.button_style if row and row.button_style else default_style)
        effective_icon = row.button_icon if row and row.button_icon is not None else (None if cleared else default_icon)
        return {
            "text": row.button_text if row else KEYBOARD_BUTTON_DEFAULTS[target],
            "style": effective_style,
            "icon": str(effective_icon) if effective_icon else None,
        }
    if entity == "layout":
        row = await session.get(AdminState, 1, with_for_update=lock)
        return {"rows": row.layout if row else []}
    if entity == "texts":
        if target not in TEXTS:
            fail(404, "کلید متن ناشناخته")
        row = await session.scalar(query(select(BotText).where(BotText.key == target)))
        return {"text": row.value if row else None}
    if entity == "plans":
        if target == "new":
            return {}
        row = await session.get(Plan, int(target), with_for_update=lock)
        if not row:
            fail(404, "پلن پیدا نشد.")
        data = {k: getattr(row, k) for k in PLAN_FIELDS}
        if row.price == int(row.price):
            data["price"] = int(row.price)
        if data["plan_type"] == "fair":
            data["plan_type"] = "fair_usage"
        data["button_icon"] = str(row.button_icon) if row.button_icon else None
        return data
    if entity == "grants":
        row = await session.get(AdminGrant, int(target), with_for_update=lock)
        return {"name": row.name if row else "ادمین", "permissions": row.permissions if row else []}
    if entity == "users":
        row = await session.get(User, int(target), with_for_update=lock)
        if not row:
            fail(404, "کاربر پیدا نشد.")
        return {"status": row.status}
    if entity == "tickets":
        row = await session.get(SupportTicket, int(target), with_for_update=lock)
        if not row:
            fail(404, "تیکت پیدا نشد.")
        last = await session.scalar(select(func.max(TicketMessage.id)).where(TicketMessage.ticket_id == row.id))
        return {"status": row.status, "assigned_to": row.assigned_to, "last_message": last}
    if entity == "services":
        row = await session.get(Service, int(target), with_for_update=lock)
        if not row:
            fail(404, "سرویس پیدا نشد.")
        # Same User -> Policy lock order as recurring reservation/cancellation.
        await session.get(User, row.id, with_for_update=lock)
        policy = await session.get(Policy, row.code, with_for_update=lock)
        return {
            "user_id": row.id,
            "username": row.username,
            "panel_code": row.in_panel,
            "panel_userid": row.panel_userid,
            "state": policy.state if policy else "off",
            "revision": policy.revision if policy else 0,
        }
    return fail(404, "بخش ناشناخته")


async def validate(session, entity, target, data, before, actor):
    permission(actor, entity, target)
    finance_texts = {
        item["key"]
        for section, items in TEXT_KEYS_CONFIG.items()
        if section in ("balance", "manual_card", "crypto_payment", "webhook", "renewal", "buy_service")
        for item in items
    }
    if (entity == "texts" and target in finance_texts) or (
        entity == "buttons"
        and (target.startswith("in.balance.") or target in ("in.buy.confirm", "in.ms.renew.confirm"))
    ):
        require(actor, "payments.manage")
    if entity == "settings":
        fields(data, SECTION_KEYS[target])
        result = {**before, **data}
        for k, v in result.items():
            default = SECTION_KEYS[target][k]
            if type(default) is bool:
                if type(v) is not bool:
                    fail(422, "مقدار روشن/خاموش لازم است.")
            elif default is None:
                if v not in (None, "all", "safe_mode"):
                    fail(422, "حالت نمایش کارت نامعتبر است.")
            else:
                integer(v, 0, 10**12)
                if "percent" in k and v > 100:
                    fail(422, "درصد بیشتر از ۱۰۰ مجاز نیست.")
                if k == "backup_interval_hours" and not 1 <= v <= 720:
                    fail(422, "فاصله بکاپ ۱ تا ۷۲۰ ساعت است.")
        for k, v in result.items():
            if k.endswith("_min") and k[:-4] + "_max" in result and v > result[k[:-4] + "_max"]:
                fail(422, "حداقل نباید بیشتر از حداکثر باشد.")
        if result.get("test_panel_id") and not await session.get(Panels, result["test_panel_id"]):
            fail(422, "پنل تست پیدا نشد.")
        return result
    if entity == "buttons":
        fields(data, ("text", "style", "icon"))
        value = {**before, **data}
        value = {"text": label(value["text"]), "style": style(value["style"]), "icon": icon(value["icon"])}
        if target.startswith("bt."):
            all_rows = (await session.scalars(select(KeyboardButton))).all()
            labels = {k: v for k, v in KEYBOARD_BUTTON_DEFAULTS.items() if k.startswith("bt.") and k != target}
            labels.update(
                {
                    r.button_key: r.button_text
                    for r in all_rows
                    if r.button_key.startswith("bt.") and r.button_key != target
                }
            )
            if value["text"].startswith("/") or value["text"] in labels.values():
                fail(422, "متن دکمه اصلی نباید تکراری یا دستور باشد.")
        return value
    if entity == "layout":
        fields(data, ("rows",))
        rows = data.get("rows")
        if not isinstance(rows, list) or len(rows) > 20:
            fail(422, "چیدمان نامعتبر است.")
        flat = []
        for row in rows:
            if not isinstance(row, list) or not 1 <= len(row) <= 3:
                fail(422, "هر ردیف ۱ تا ۳ دکمه داشته باشد.")
            if any(not isinstance(k, str) or k not in HOME_KEYS for k in row):
                fail(422, "دکمه غیرمجاز در منو")
            flat.extend(row)
        if len(flat) != len(set(flat)) or (rows and not REQUIRED_HOME.issubset(flat)):
            fail(422, "دکمه تکراری یا حذف دکمه ضروری")
        return {"rows": rows}
    if entity == "texts":
        fields(data, ("text",))
        if "text" not in data:
            fail(422, "متن مشخص نشده است.")
        value = data["text"]
        if value is None:
            row = await session.scalar(select(BotText).where(BotText.key == target))
            if row and row.banner_url:
                fail(422, "متن بنر دارد؛ بازنشانی آن را از مدیریت متن ربات انجام دهید.")
            return {"text": None}
        if not isinstance(value, str) or not 1 <= len(value) <= 3000:
            fail(422, "متن باید ۱ تا ۳۰۰۰ کاراکتر باشد.")
        try:
            used = set()
            for _, name, spec, conv in string.Formatter().parse(value):
                if name is not None:
                    if spec or conv or name not in TEXTS[target].get("placeholders", {}):
                        raise ValueError()
                    used.add(name)
            # Existing template variables cannot be silently removed.
            required = {name for _, name, _, _ in string.Formatter().parse(before["text"] or "") if name}
            if not required.issubset(used):
                raise ValueError()
        except ValueError:
            fail(422, "متغیرهای متن معتبر نیستند یا متغیر قبلی حذف شده است.")
        return {"text": value}
    if entity == "plans":
        fields(data, set(PLAN_FIELDS) - {"id"})
        value = {k: v for k, v in before.items() if k != "id"}
        value.update(data)
        for key, default in {
            "enabled": True,
            "plan_type": "volume",
            "data_limit_reset_strategy": "no_reset",
            "ip_limit": 0,
            "display_button_text": None,
            "button_style": None,
            "button_icon": None,
        }.items():
            value.setdefault(key, default)
        if set(value) != set(PLAN_FIELDS) - {"id"}:
            fail(422, "فیلدهای پلن ناقص است.")
        integer(value["price"], 1, 10**12)
        number(value["storage"], 0, 10**6)
        integer(value["duration"], 0, 3650)
        integer(value["ip_limit"], 0, 10000)
        integer(value["panel_code"], 1, 2**52 - 1)
        if not await session.get(Panels, value["panel_code"]):
            fail(422, "پنل پیدا نشد.")
        if before and value["panel_code"] != before["panel_code"]:
            fail(422, "برای تغییر پنل، پلن جدید بسازید.")
        if (
            type(value["enabled"]) is not bool
            or value["plan_type"] not in ("volume", "fair_usage")
            or value["data_limit_reset_strategy"] not in ("no_reset", "day", "week", "month", "year")
        ):
            fail(422, "نوع پلن یا ریست نامعتبر است.")
        if value["storage"] == 0 and value["duration"] == 0:
            fail(422, "حجم و زمان هم‌زمان نامحدود نباشند.")
        if value["display_button_text"] is not None:
            value["display_button_text"] = label(value["display_button_text"], 120)
        value["button_style"] = style(value["button_style"])
        value["button_icon"] = icon(value["button_icon"])
        return value
    if entity == "grants":
        fields(data, ("name", "permissions"))
        uid = int(target)
        if uid in ADMIN_ID or not await session.get(User, uid):
            fail(422, "مالک قابل تغییر نیست؛ کاربر باید قبلاً /start زده باشد.")
        permissions = data.get("permissions")
        if not isinstance(permissions, list) or any(
            not isinstance(p, str) or p not in PERMISSIONS for p in permissions
        ):
            fail(422, "مجوز ناشناخته")
        permissions = set(permissions)
        for p in list(permissions):
            view = p.replace(".manage", ".view")
            if view in PERMISSIONS:
                permissions.add(view)
        return {"name": label(data.get("name"), 60), "permissions": sorted(permissions)}
    if entity == "users":
        fields(data, ("status",))
        if int(target) in ADMIN_ID or int(target) == actor["id"]:
            fail(422, "حساب خود یا مالک قابل مسدودسازی نیست.")
        if "status" not in data or data["status"] not in (None, "ban"):
            fail(422, "وضعیت نامعتبر")
        if before["status"] in ("DeleteAccount", "BlockedBot"):
            fail(422, "وضعیت حذف حساب یا مسدودشدن ربات از اینجا قابل تغییر نیست.")
        return data
    if entity == "tickets":
        fields(data, ("status", "reply", "assign_me"))
        value = {
            "status": data.get("status", before["status"]),
            "reply": data.get("reply", ""),
            "assign_me": data.get("assign_me", False),
        }
        if value["status"] not in ("open", "in_progress", "waiting", "closed") or type(value["assign_me"]) is not bool:
            fail(422, "وضعیت تیکت نامعتبر")
        if not isinstance(value["reply"], str) or len(value["reply"]) > 3000:
            fail(422, "پاسخ حداکثر ۳۰۰۰ کاراکتر باشد.")
        return value
    if entity == "services":
        fields(data, ("state",))
        if data != {"state": "off"}:
            fail(422, "فقط توقف برداشت آینده مجاز است.")
        return data
    return fail(422, "درخواست نامعتبر")


async def apply(session, entity, target, data, actor, now):
    if entity == "settings":
        row = await session.scalar(select(Settings).with_for_update())
        for k, v in resolve_settings_update_kwargs(row, **data).items():
            setattr(row, k, v)
    elif entity == "buttons":
        row = await session.scalar(select(KeyboardButton).where(KeyboardButton.button_key == target).with_for_update())
        if not row:
            row = KeyboardButton(
                id=(await session.scalar(select(func.max(KeyboardButton.id))) or 0) + 1, button_key=target
            )
            session.add(row)
        row.button_text, row.button_style, row.button_icon = (
            data["text"],
            data["style"] or "",
            int(data["icon"]) if data["icon"] else 0,
        )
    elif entity == "layout":
        (await session.get(AdminState, 1)).layout = data["rows"]
    elif entity == "texts":
        row = await session.scalar(select(BotText).where(BotText.key == target).with_for_update())
        if not row:
            row = BotText(id=(await session.scalar(select(func.max(BotText.id))) or 0) + 1, key=target, lang="fa")
            session.add(row)
        if data["text"] is None:
            if row in session.new:
                session.expunge(row)
            else:
                await session.delete(row)
        else:
            row.value = data["text"]
    elif entity == "plans":
        row = await session.get(Plan, int(target)) if target != "new" else Plan()
        if target == "new":
            session.add(row)
        billing_fields = (
            "price",
            "storage",
            "duration",
            "panel_code",
            "plan_type",
            "data_limit_reset_strategy",
            "ip_limit",
            "enabled",
        )
        changed = target != "new" and any(getattr(row, k) != data[k] for k in billing_fields)
        for k, v in data.items():
            setattr(row, k, int(v) if k == "button_icon" and v else v)
        await session.flush()
        paused = 0
        if changed:
            from app.services.auto_renew.plans import invalidate_renewals_for_plan

            paused = await invalidate_renewals_for_plan(session, row.id)
        return {"id": row.id, "paused_renewals": paused}
    elif entity == "grants":
        row = await session.get(AdminGrant, int(target))
        if not row:
            row = AdminGrant(user_id=int(target))
            session.add(row)
        row.name, row.permissions, row.updated_at = data["name"], data["permissions"], now
        row.revision = (row.revision or 0) + 1
    elif entity == "users":
        (await session.get(User, int(target))).status = data["status"]
    elif entity == "tickets":
        row = await session.get(SupportTicket, int(target))
        row.status, row.updated_at = data["status"], now
        if data["assign_me"]:
            row.assigned_to = actor["id"]
        if data["reply"].strip():
            session.add(
                TicketMessage(
                    ticket_id=row.id, author_id=actor["id"], kind="staff", text=data["reply"].strip(), created_at=now
                )
            )
    elif entity == "services":
        from app.db.models.auto_renew import AutoRenewNotice

        policy = await session.get(Policy, int(target))
        if policy:
            policy.state, policy.reason, policy.updated_at = "off", "cancelled", now
            policy.revision += 1
            notices = (
                await session.scalars(
                    select(AutoRenewNotice)
                    .where(
                        AutoRenewNotice.service_code == int(target),
                        AutoRenewNotice.state == "pending",
                        AutoRenewNotice.kind.in_(("upcoming", "low_balance", "budget")),
                    )
                    .with_for_update()
                )
            ).all()
            for notice in notices:
                notice.state = "cancelled"
    return {"saved": True}
