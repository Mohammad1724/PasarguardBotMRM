"""Keyboard builders for admin gift code management."""

from telethon import Button


def gift_main_menu_buttons() -> list:
    return [
        [Button.inline("🪄 ساخت کد هدیه", "gift_create")],
        [Button.inline("🎛 لیست کدهای هدیه", "gift_list:0")],
        [Button.inline("🔙 بازگشت به پنل", "gift_back_panel")],
    ]


def gift_type_buttons() -> list:
    return [
        [Button.inline("💰 شارژ کیف پول", "gift_type:balance")],
        [Button.inline("📅 روز رایگان", "gift_type:days")],
        [Button.inline("📦 حجم رایگان", "gift_type:volume")],
        [Button.inline("🔙 بازگشت", "gift_cancel")],
    ]


def gift_expiry_buttons() -> list:
    return [
        [
            Button.inline("۱ روز", "gift_exp:1"),
            Button.inline("۳ روز", "gift_exp:3"),
            Button.inline("۷ روز", "gift_exp:7"),
        ],
        [
            Button.inline("۳۰ روز", "gift_exp:30"),
            Button.inline("۹۰ روز", "gift_exp:90"),
            Button.inline("♻️ بی‌نهایت", "gift_exp:0"),
        ],
        [Button.inline("🔙 بازگشت", "gift_cancel")],
    ]


def gift_list_buttons(gifts, page: int, per_page: int = 8) -> list:
    start = page * per_page
    chunk = gifts[start : start + per_page]
    buttons = []
    for gift in chunk:
        status = "✅" if gift.is_active else "❌"
        buttons.append([Button.inline(f"{status} {gift.code}", f"gift_view:{gift.id}:{page}")])
    nav = []
    if start > 0:
        nav.append(Button.inline("⬅️", f"gift_list:{page - 1}"))
    if start + per_page < len(gifts):
        nav.append(Button.inline("➡️", f"gift_list:{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([Button.inline("🔙 بازگشت", "gift_back_main")])
    return buttons


def gift_view_buttons(gift, page: int) -> list:
    return [
        [
            Button.inline("✅ فعال / ❌ غیرفعال", f"gift_toggle:{gift.id}:{page}"),
            Button.inline("🗑 حذف", f"gift_delete:{gift.id}:{page}"),
        ],
        [Button.inline("🔙 بازگشت", f"gift_list:{page}")],
    ]


def gift_back_row() -> list:
    return [[Button.inline("🔙 بازگشت", "gift_cancel")]]
