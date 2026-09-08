"""Shared helpers for admin home panel."""

from app import Kenzo
from app.db.crud.log_channels import LogChannelManager
from app.telegram.keyboards.admin import DOCS_URL, Panel_Admin_Buttons, admin_category_buttons
from app.telegram.keyboards.admin_navigation import CATEGORIES_BY_KEY
from app.telegram.state import get_step, set_step
from config import ADMIN_ID, LOG_CHANNEL

ADD_PANEL_STEPS = frozenset(
    {
        "addPanel_name",
        "AddPanel_url",
        "AddPanel_auth_type",
        "AddPanel_username",
        "AddPanel_password",
        "AddPanel_api_key",
        "AddPanel_select_group",
        "ChangePanelAuth_username",
        "ChangePanelAuth_password",
        "ChangePanelAuth_api_key",
    }
)

_SETUP_WARNING = (
    "⚠️ **تنظیمات ربات کامل نیست**\n\n"
    "برای عملکرد صحیح ربات، ابتدا مورد زیر را تنظیم کنید:\n\n"
    "**📝 مدیریت لاگ‌ها**\n"
    "هنوز کانال یا گروهی برای دریافت لاگ‌های ربات انتخاب نشده است.\n"
    "از دسته **🛠 نگهداری و امنیت** ← **📝 مدیریت لاگ‌ها**، مقصد ارسال لاگ‌ها را مشخص کنید."
)


def _admin_home_message(user_id: int, username: str | None, *, setup_warning: str | None = None) -> str:
    user_label = username or "—"
    message = (
        f"**🌺به پنل مدیریت خوش آمدید.**\n"
        f"ایدی عددی شما: `{user_id}`\n"
        f"نام کاربری شما: @{user_label}\n"
        f"\nابتدا دسته مورد نظر را انتخاب کنید؛ ابزارهای هر بخش داخل همان دسته هستند.\n"
        f"\n📚 مستندات ربات: {DOCS_URL}\n"
    )
    if setup_warning:
        message += f"\n{setup_warning}\n"
    return message


async def send_admin_home(user_id: int, username: str | None = None) -> None:
    if not await prepare_admin_navigation(user_id):
        return

    setup_warning = None
    if LOG_CHANNEL is None:
        channels = await LogChannelManager().get_all_log_channels()
        if not any(ch.is_active for ch in channels):
            setup_warning = _SETUP_WARNING

    await Kenzo.send_message(
        entity=user_id,
        message=_admin_home_message(user_id, username, setup_warning=setup_warning),
        buttons=Panel_Admin_Buttons,
    )


async def prepare_admin_navigation(user_id: int) -> bool:
    """Keep legacy entry guards working; do not clear unrelated payment/conversation data."""
    if user_id not in ADMIN_ID:
        return False
    await set_step(user_id, "panel")
    if await get_step(user_id) == "panel":
        return True
    await Kenzo.send_message(user_id, "وضعیت گفتگو ذخیره نشد؛ کمی بعد دوباره /panel را بفرستید.")
    return False


async def send_admin_category(user_id: int, key: str) -> None:
    category = CATEGORIES_BY_KEY.get(key)
    if category is None or not await prepare_admin_navigation(user_id):
        return
    await Kenzo.send_message(
        user_id,
        f"پنل مدیریت › {category.title}\n\n{category.description}\n\nیکی از ابزارهای این بخش را انتخاب کنید.",
        buttons=admin_category_buttons(key),
        parse_mode=None,
    )


async def open_admin_shortcut(event, action: str) -> None:
    """Open existing, independently authorized entry points; never synthesize a user command."""
    if not event.is_private or not await prepare_admin_navigation(event.sender_id):
        return
    if action == "miniapp":
        from app.telegram.user.admin_app.module import open_app

        await open_app(event)
    elif action == "autorenew":
        from app.telegram.user.auto_renew.handlers import admin

        await admin(event)
    elif action in {"tickets", "cx", "cxstats"}:
        from app.telegram.user.customer_experience.handlers import control_panel, show_inbox, stats

        if action == "tickets":
            await show_inbox(event, staff=True)
        elif action == "cx":
            await control_panel(event)
        else:
            await stats(event)
