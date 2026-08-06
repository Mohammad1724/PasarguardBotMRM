"""Text templates for admin backup."""

from app.telegram.admin.backup import states

BACKUP_MENU_TRIGGER = states.BACKUP_MENU_TRIGGER

NUMERIC_ONLY = "لطفاً فقط عدد صحیح ارسال کنید (مثال: 1 یا 24). عدد 0 بکاپ خودکار را خاموش می‌کند."
INTERVAL_SAVED_TEMPLATE = "✅ فاصله بکاپ خودکار روی هر `{hours}` ساعت تنظیم شد."
INTERVAL_DISABLED = "✅ بکاپ خودکار خاموش شد."
WORKING = "⏳ در حال تهیه بکاپ و ارسال به کانال…"
CHANNEL_NOT_SET = (
    "❌ کانال لاگ بکاپ تنظیم نشده است.\nاز مسیر «مدیریت لاگ‌ها» کانال «🗄 بکاپ ربات» را ست کنید تا بکاپ ارسال شود."
)
CHANNEL_NOT_SET_ALERT = "❌ کانال بکاپ تنظیم نشده است. ابتدا از مدیریت لاگ‌ها کانال را ست کنید."

# Restore texts
RESTORE_WAITING_FILE = (
    "📥 **ریستور بکاپ**\n\n"
    "فایل ZIP بکاپ را ارسال کنید.\n"
    "فایل باید شامل `database.sql` باشد.\n\n"
    "⚠️ **هشدار:** ریستور تمام اطلاعات فعلی دیتابیس را پاک می‌کند!"
)
RESTORE_CONFIRM_TEMPLATE = (
    "📋 **بررسی فایل بکاپ**\n\n"
    "📦 فایل: `{filename}`\n"
    "💾 حجم فایل: `{zip_size_mb:.2f}` MB\n"
    "📊 حجم SQL: `{sql_size_mb:.2f}` MB\n"
    "🔑 CRYPTO_KEY: {crypto_status}\n\n"
    "⚠️ **هشدار مهم:**\n"
    "• تمام داده‌های فعلی دیتابیس **حذف** می‌شوند\n"
    "• اطلاعات از فایل بکاپ جایگزین می‌شود\n"
    "• `CRYPTO_KEY` از بکاپ در `.env` جایگزین می‌شود\n\n"
    "آیا مطمئن هستید؟"
)
RESTORE_CRYPTO_FOUND = "✅ موجود در بکاپ"
RESTORE_CRYPTO_NOT_FOUND = "❌ پیدا نشد"
RESTORE_NO_SQL = "❌ فایل `database.sql` در بکاپ پیدا نشد!"
RESTORE_NOT_ZIP = "❌ فایل ارسالی ZIP نیست. لطفاً فایل ZIP بکاپ را ارسال کنید."
RESTORE_CANCELLED = "❌ ریستور لغو شد."
RESTORE_WORKING = "⏳ در حال ریستور دیتابیس... لطفاً صبر کنید."
RESTORE_TOO_LARGE = "❌ فایل بکاپ خیلی بزرگ است (حداکثر 500 MB)."
RESTORE_INVALID_FILE = "❌ فایل ارسالی معتبر نیست. لطفاً فایل ZIP بکاپ را ارسال کنید."

RESTORE_SUCCESS_TEMPLATE = (
    "✅ **ریستور با موفقیت انجام شد!**\n\n{details}\n\n⚠️ **لطفاً ربات را ری‌استارت کنید تا تغییرات اعمال شود.**"
)


def menu_text(interval_hours: int, channel_configured: bool) -> str:
    interval_line = "⏸ بکاپ خودکار: خاموش" if interval_hours <= 0 else f"⏱ فاصله خودکار: هر `{interval_hours}` ساعت"
    channel_line = "✅ کانال بکاپ: تنظیم شده" if channel_configured else "❌ کانال بکاپ: تنظیم نشده (از مدیریت لاگ‌ها)"
    return (
        "🗄 **بکاپ ربات**\n\n"
        f"{interval_line}\n"
        f"{channel_line}\n\n"
        "📤 **بکاپ‌گیری:** فایل به کانال لاگ ارسال می‌شود.\n"
        "📥 **ریستور:** فایل ZIP بکاپ را ارسال کنید."
    )
