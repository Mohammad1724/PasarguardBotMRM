"""Text templates for admin gift code management."""

from app.utils.formatting.dates import Time_Date

GIFT_TYPE_LABELS = {
    "balance": "💰 شارژ کیف پول",
    "days": "📅 روز رایگان",
    "volume": "📦 حجم رایگان",
}

GIFT_UNIT_BY_TYPE = {
    "balance": "تومان",
    "days": "روز",
    "volume": "گیگابایت",
}

GIFT_INVALID_CODE = "❌ این کد معتبر نیست یا منقضی/غیرفعال شده است."
GIFT_INVALID_SERVICE = "❌ سرویس انتخابی معتبر نیست."
GIFT_SAVED = "✅ کد هدیه ذخیره شد."
GIFT_NUMERIC_ONLY = "لطفا فقط عدد ارسال کنید."
GIFT_USES_RECORDED = "🎁 کد هدیه `{code}` برای کاربر `{user_id}` ثبت شد.\n\n{detail}"


def gift_value_label(gift) -> str:
    unit = GIFT_UNIT_BY_TYPE.get(gift.type, "")
    return f"{int(gift.value):,} {unit}"


def gift_main_menu_text() -> str:
    return (
        "🎁 **مدیریت کد هدیه**\n\n"
        "کد هدیه می‌تواند شارژ کیف پول، روز یا حجم رایگان باشد.\n"
        "کاربران با ارسال کد از منوی «🎟 کد هدیه» آن را فعال می‌کنند."
    )


def gift_info_text(gift, uses, uses_count: int) -> str:
    created = Time_Date(gift.created_at)["jf"] if gift.created_at else "-"
    expires = Time_Date(gift.expires_at)["jf"] if gift.expires_at else "بی‌نهایت"
    lines = [
        f"**🎟 کد:** `{gift.code}`",
        f"**📦 نوع:** {GIFT_TYPE_LABELS.get(gift.type, gift.type)}",
        f"**💎 مقدار:** {gift_value_label(gift)}",
        f"**✅ وضعیت:** {'فعال' if gift.is_active else 'غیرفعال'}",
        f"**🔁 استفاده:** `{uses_count}`**/**`{gift.max_uses}` (هر کاربر: {gift.per_user_limit})",
        f"**⏳ انقضا:** {expires}",
        f"**🕒 ساخت:** {created}",
    ]
    if gift.note:
        lines.append(f"**📝 یادداشت:** {gift.note}")
    if uses:
        lines.append("\n**آخرین استفاده‌ها:**")
        for use in uses[:10]:
            when = Time_Date(use.used_at)["jf"] if use.used_at else "-"
            target = f" | سرویس: `{use.service_code}`" if use.service_code else ""
            lines.append(f"• کاربر `{use.user_id}` — {when}{target}")
    return "\n".join(lines)


def gift_created_log(gift) -> str:
    expires = Time_Date(gift.expires_at)["jf"] if gift.expires_at else "بی‌نهایت"
    return (
        "#کد_هدیه_جدید\n"
        f"🎟 کد: `{gift.code}`\n"
        f"📦 نوع: {GIFT_TYPE_LABELS.get(gift.type, gift.type)}\n"
        f"💎 مقدار: {gift_value_label(gift)}\n"
        f"🔁 سقف استفاده: {gift.max_uses} (هر کاربر {gift.per_user_limit})\n"
        f"⏳ انقضا: {expires}"
    )
