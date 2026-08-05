"""Keyboard builders for admin backup."""

from telethon import Button


def menu_buttons(interval_hours: int) -> list:
    interval_label = "⏸ خودکار خاموش" if interval_hours <= 0 else f"⏱ فاصله: هر {interval_hours} ساعت"
    return [
        [Button.inline("🚀 بکاپ همین الان", data="backup_run_now")],
        [Button.inline(f"⚙️ تنظیم فاصله ({interval_label})", data="backup_set_interval")],
        [Button.inline("📥 ریستور از فایل", data="backup_restore_start")],
        [Button.inline("🔙 بازگشت به پنل", data="back_to_admin_panel")],
    ]


def interval_prompt_buttons() -> list:
    return [
        [Button.inline("🔙 بازگشت", data="backup_menu")],
    ]


def restore_confirm_buttons() -> list:
    return [
        [
            Button.inline("✅ تأیید و شروع ریستور", data="backup_restore_confirm"),
        ],
        [
            Button.inline("❌ لغو", data="backup_restore_cancel"),
        ],
    ]


def restore_waiting_buttons() -> list:
    return [
        [Button.inline("❌ لغو", data="backup_restore_cancel")],
    ]
