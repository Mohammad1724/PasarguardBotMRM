"""Admin reply/inline keyboard builders."""

from telethon import Button

from app.db.crud.user import UserCRUD

from .admin_navigation import ADMIN_APP_LABEL, ADMIN_CATEGORIES, ADMIN_NAV_PREFIX, ADMIN_ROOT_LABEL, CATEGORIES_BY_KEY
from .common import create_button, glass_inline_button, glass_text_button, styled_simple_webview_button

DOCS_URL = "https://amirkenzo.github.io/PasarguardBot/"

Lock_Channels_Menu_Buttons = [
    [glass_text_button("افزودن کانال"), glass_text_button("حذف کانال")],
    [glass_text_button("لیست کانال‌ها")],
    [glass_text_button("🔙 بازگشت به پنل")],
]
# Lock Channels (🔐) Inline Menu (fully glassy)
Lock_Channels_Inline_Menu = [
    [
        glass_inline_button("➕ افزودن کانال", data="lock_add"),
        glass_inline_button("📋 لیست کانال‌ها", data="lock_list:1"),
    ],
    [glass_inline_button("🔙 بازگشت به پنل", data="back_to_panel")],
]


async def create_inline_manageuser(UserID):
    user = await UserCRUD().read_user(UserID)
    buttons = [
        [Button.inline("📂 لیست سرویس‌ها", f"MToUser_listSv:{UserID}")],
        [Button.inline("🏢 نمایندگی‌های کاربر", f"MToUser_resellers:{UserID}")],
        [
            Button.inline("🔍 جستجوی کانفیگ", f"AdminSearchConfig:{UserID}"),
            Button.inline("🗑 حذف گروهی کانفیگ", f"BulkDeleteConfigs:{UserID}"),
        ],
        [Button.inline("رفع مسدودی", f"unbansup_{UserID}"), Button.inline("مسدود سازی", f"bansup_{UserID}")],
        [Button.inline("اطلاعات کاربر", f"UserInfo:{UserID}"), Button.inline("پیام به کاربر", f"sendm_{UserID}")],
    ]
    if not user or not user.number:
        buttons.append([Button.inline("📱 تایید شماره کاربر", f"confirm_phone_{UserID}")])

    buttons.append([Button.inline("ساخت کانفیگ برای کاربر", f"CreateConfigFor:{UserID}")])
    return buttons


def build_admin_reseller_list_buttons(user_id: int, accounts) -> list:
    rows = [
        [Button.inline(f"🏢 {acc.username} · #{acc.code}", data=f"AdminReseller_view:{user_id}:{acc.code}")]
        for acc in accounts
    ]
    rows.append([Button.inline("🔙 بازگشت", data=f"BackToUserManagement:{user_id}")])
    return rows


def build_admin_reseller_account_buttons(user_id: int, account) -> list:
    code = account.code
    rows = [
        [Button.inline("🔑 نمایش رمز ورود", data=f"AdminReseller_creds:{user_id}:{code}")],
        [Button.inline("🔄 تغییر رمز عبور", data=f"AdminReseller_chpwd:{user_id}:{code}")],
    ]
    if account.status in ("paused", "admin_paused", "usage_capped"):
        if account.status != "usage_capped":
            rows.append([Button.inline("▶️ فعال‌سازی پنل", data=f"AdminReseller_resume:{user_id}:{code}")])
    elif account.status in ("active", "suspended"):
        rows.append([Button.inline("⏸ غیرفعال‌سازی پنل", data=f"AdminReseller_pause:{user_id}:{code}")])
    if account.pricing_mode == "fixed":
        rows.append([Button.inline("💎 تمدید", data=f"AdminReseller_renew:{user_id}:{code}")])
    if account.pricing_mode == "usage":
        rows.append([Button.inline("📦 محدودیت مصرف", data=f"AdminReseller_usage_cap:{user_id}:{code}")])
    rows.append([Button.inline("🗑 حذف نمایندگی", data=f"AdminReseller_delete:{user_id}:{code}")])
    rows.append([Button.inline("🔙 بازگشت به لیست", data=f"MToUser_resellers:{user_id}")])
    return rows


def build_admin_reseller_usage_cap_buttons(user_id: int, account_code: int, *, has_cap: bool) -> list:
    rows = [
        [Button.inline("✏️ تنظیم سقف (گیگ)", data=f"AdminReseller_usage_cap_set:{user_id}:{account_code}")],
    ]
    if has_cap:
        rows.append([Button.inline("🗑 حذف سقف مصرف", data=f"AdminReseller_usage_cap_clear:{user_id}:{account_code}")])
    rows.append([Button.inline("🔙 بازگشت", data=f"AdminReseller_view:{user_id}:{account_code}")])
    return rows


def build_admin_reseller_delete_confirm_buttons(user_id: int, account_code: int) -> list:
    return [
        [Button.inline("✅ بله، کامل حذف شود", data=f"AdminReseller_delete_confirm:{user_id}:{account_code}")],
        [Button.inline("❌ انصراف", data=f"AdminReseller_view:{user_id}:{account_code}")],
    ]


def build_admin_reseller_chpwd_confirm_buttons(user_id: int, account_code: int) -> list:
    return [
        [Button.inline("✅ بله، رمز عوض شود", data=f"AdminReseller_chpwd_confirm:{user_id}:{account_code}")],
        [Button.inline("❌ انصراف", data=f"AdminReseller_view:{user_id}:{account_code}")],
    ]


def admin_category_buttons(key: str) -> list:
    category = CATEGORIES_BY_KEY[key]
    rows = [
        [
            styled_simple_webview_button(label, DOCS_URL) if label == "📚 مستندات ربات" else create_button(label)
            for label in row
        ]
        for row in category.rows
    ]
    rows.append([create_button(ADMIN_ROOT_LABEL), create_button("🏠")])
    return rows


# New messages use a reply keyboard, so choosing a category also replaces old flat keyboards.
Panel_Admin_Buttons = [
    [create_button(category.title) for category in ADMIN_CATEGORIES[i : i + 2]]
    for i in range(0, len(ADMIN_CATEGORIES), 2)
] + [[create_button(ADMIN_APP_LABEL)], [create_button("🏠")]]

# Message edits cannot install a reply keyboard. Legacy completion/cancel callbacks
# use this inline equivalent; each category opens a fresh, compatible reply submenu.
Panel_Admin_Inline_Buttons = [
    [Button.inline(category.title, ADMIN_NAV_PREFIX + category.key) for category in ADMIN_CATEGORIES[i : i + 2]]
    for i in range(0, len(ADMIN_CATEGORIES), 2)
] + [[Button.inline(ADMIN_APP_LABEL, ADMIN_NAV_PREFIX + "miniapp")], [Button.inline("🏠", "DataCancel")]]

BT_takhfifList = [
    [create_button("🎛 لیست کدتخفیف"), create_button("🪄 ساخت کدتخفیف")],
    [create_button("🔙 بازگشت به پنل")],
]

panel_xui_buttons = [
    [create_button("📉 وضعیت پنل ها"), create_button("▫️ افزودن پنل جدید")],
    [create_button("🔙 بازگشت به پنل")],
]


panel_back = [[create_button("🔙 بازگشت به پنل")]]

Home_Back = [[create_button("🏠")]]
