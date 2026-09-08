"""Categorized navigation uses the old actions/guards; never forwards menu text to users."""

import ast
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telethon import TelegramClient, events
from telethon.tl.types import ReplyInlineMarkup, ReplyKeyboardMarkup

from app.telegram.admin.admin_home import callbacks, messages, module, service
from app.telegram.keyboards.admin import Panel_Admin_Buttons, Panel_Admin_Inline_Buttons, admin_category_buttons
from app.telegram.keyboards.admin_navigation import (
    ADMIN_APP_LABEL,
    ADMIN_CATEGORIES,
    ADMIN_NAV_PREFIX,
    ADMIN_ROOT_LABEL,
    ADMIN_SHORTCUTS,
)

# Literal regression inventory: removing an old entry must fail, not update itself.
LEGACY_ACTIONS = {
    "💳 تنظیمات درگاه",
    "👥 آمار گیری",
    "📚 منوی پنل ها",
    "⚙️ تنظیمات ربات",
    "🎟 کدتخفیف",
    "🗞 ساخت پلن",
    "🎁 کد هدیه",
    "🏢 پلن نمایندگی",
    "👤 مدیریت کاربر",
    "📮 ارسال همگانی",
    "📥 فوروارد همگانی",
    "➖ کسر موجودی",
    "➕ افزودن موجودی",
    "💰 شارژ گروهی",
    "🔄 ریست دریافت تست",
    "📈 افزایش حجم و زمان همگانی",
    "🔐 قفل چنل ها",
    "📝 مدیریت لاگ‌ها",
    "📦 بکاپ ربات",
    "📝 متن‌های ربات",
    "⌨️ مدیریت دکمه‌های کیبورد",
    "🔗 لینک های آماده",
    "📚 مستندات ربات",
    "🈸 آپدیت برنامه ها",
}


def event(text="", *, uid=1, private=True, data=b""):
    return SimpleNamespace(
        sender_id=uid,
        is_private=private,
        raw_text=text,
        data=data,
        message=SimpleNamespace(text=text, message=text, media=None),
        respond=AsyncMock(),
        answer=AsyncMock(),
        delete=AsyncMock(),
        get_sender=AsyncMock(return_value=SimpleNamespace(username="synthetic_owner")),
    )


@pytest.fixture
def navigation(monkeypatch):
    state = {"step": "panel", "pending_payment": "untouched"}

    async def get_step(uid):
        return state["step"]

    async def set_step(user_id, step):
        state["step"] = step

    for target in (messages, service):
        monkeypatch.setattr(target, "get_step", get_step)
    monkeypatch.setattr(service, "set_step", set_step)
    monkeypatch.setattr(service, "LOG_CHANNEL", -10012345)
    send = AsyncMock()
    monkeypatch.setattr(service.Kenzo, "send_message", send)
    monkeypatch.setattr(messages, "get_button_text", AsyncMock(return_value="⚙️ پنل مدیریت"))
    return state, send


def test_inventory_and_telegram_markup_types():
    all_actions = Counter(label for category in ADMIN_CATEGORIES for row in category.rows for label in row)
    assert set(all_actions) == LEGACY_ACTIONS | ADMIN_SHORTCUTS.keys()
    assert set(all_actions.values()) == {1}
    assert len({category.key for category in ADMIN_CATEGORIES}) == 8
    assert len({category.title for category in ADMIN_CATEGORIES}) == 8
    reply = TelegramClient.build_reply_markup(Panel_Admin_Buttons)
    inline = TelegramClient.build_reply_markup(Panel_Admin_Inline_Buttons)
    assert isinstance(reply, ReplyKeyboardMarkup)
    assert isinstance(inline, ReplyInlineMarkup)
    assert len(reply.rows) == 6  # eight groups + Mini App + home, not the old flat list
    assert {b.text for row in reply.rows for b in row.buttons} == {
        *(category.title for category in ADMIN_CATEGORIES),
        ADMIN_APP_LABEL,
        "🏠",
    }
    for category in ADMIN_CATEGORIES:
        markup = TelegramClient.build_reply_markup(admin_category_buttons(category.key))
        assert isinstance(markup, ReplyKeyboardMarkup)
        assert [b.text for row in markup.rows for b in row.buttons] == [
            *(label for row in category.rows for label in row),
            ADMIN_ROOT_LABEL,
            "🏠",
        ]
    assert all(len(button.data) <= 64 for row in inline.rows for button in row.buttons)


@pytest.mark.parametrize("category", ADMIN_CATEGORIES, ids=lambda c: c.key)
async def test_each_category_preserves_panel_state_and_stops_dispatch(navigation, category):
    state, send = navigation
    state["step"] = "sendSupport"
    e = event(category.title)
    assert await messages.admin_navigation_filter(e)
    with pytest.raises(events.StopPropagation):
        await messages.admin_navigation_handler(e)
    assert state == {"step": "panel", "pending_payment": "untouched"}
    assert send.await_args.args[0] == 1  # never the support recipient
    assert category.title in send.await_args.args[1]
    assert isinstance(TelegramClient.build_reply_markup(send.await_args.kwargs["buttons"]), ReplyKeyboardMarkup)


@pytest.mark.parametrize("uid,private", [(2, True), (1, False), (2, False)])
async def test_nonowners_and_groups_cannot_navigate(navigation, uid, private):
    _, send = navigation
    e = event(ADMIN_CATEGORIES[0].title, uid=uid, private=private, data=b"admin_menu:finance")
    assert not await messages.admin_navigation_filter(e)
    await messages.admin_navigation_handler(e)
    await service.send_admin_category(uid if uid != 1 else 2, "finance")
    with pytest.raises(events.StopPropagation):
        await callbacks.callback_admin_navigation(e)
    send.assert_not_awaited()
    assert e.answer.await_args.kwargs["alert"]


@pytest.mark.parametrize(
    "step", ["edit_keyboard:bt.menu_buy_service", "help_btn_text", "help_download_app_config_name"]
)
async def test_keyboard_editor_can_receive_a_category_name_as_content(navigation, step):
    state, send = navigation
    state["step"] = step
    e = event(ADMIN_CATEGORIES[0].title)
    assert not await messages.admin_navigation_filter(e)
    await messages.admin_navigation_handler(e)
    assert state["step"] == step
    send.assert_not_awaited()


@pytest.mark.parametrize("text", ["/panel", "🔙 بازگشت به پنل", ADMIN_ROOT_LABEL])
async def test_back_and_command_restore_categorized_root(navigation, text):
    state, send = navigation
    state["step"] = "cx_reply:1"
    e = event(text)
    with pytest.raises(events.StopPropagation):
        await messages.message_handler_admin_panel(e)
    assert state["step"] == "panel"
    assert send.await_args.kwargs["buttons"] is Panel_Admin_Buttons


@pytest.mark.parametrize("category", ADMIN_CATEGORIES, ids=lambda c: c.key)
async def test_inline_completion_menus_open_same_reply_categories(navigation, category):
    state, send = navigation
    state["step"] = "some_legacy_wizard"
    e = event(data=(ADMIN_NAV_PREFIX + category.key).encode())
    with pytest.raises(events.StopPropagation):
        await callbacks.callback_admin_navigation(e)
    e.answer.assert_awaited_once()
    assert state["step"] == "panel"
    assert category.title in send.await_args.args[1]


async def test_invalid_callback_and_unknown_category_do_nothing(navigation):
    state, send = navigation
    with pytest.raises(events.StopPropagation):
        await callbacks.callback_admin_navigation(event(data=b"admin_menu:unsafe:action"))
    await service.send_admin_category(1, "unknown")
    send.assert_not_awaited()
    assert state["step"] == "panel"


async def test_redis_write_failure_does_not_offer_dead_submenu_or_fall_through(navigation, monkeypatch):
    state, send = navigation
    state["step"] = "sendSupport"
    monkeypatch.setattr(service, "set_step", AsyncMock())
    with pytest.raises(events.StopPropagation):
        await messages.admin_navigation_handler(event(ADMIN_CATEGORIES[0].title))
    assert state["step"] == "sendSupport"
    assert "buttons" not in send.await_args.kwargs
    assert send.await_count == 1


async def test_delivery_failure_still_consumes_navigation(navigation):
    _, send = navigation
    send.side_effect = RuntimeError("synthetic transport failure")
    e = event(ADMIN_CATEGORIES[3].title)
    e.respond.side_effect = RuntimeError("synthetic transport failure")
    with pytest.raises(events.StopPropagation):
        await messages.admin_navigation_handler(e)


async def test_category_entry_can_open_legacy_manage_user(navigation, monkeypatch):
    from app.telegram.admin.manage_user import messages as manage_user

    state, _ = navigation
    await service.send_admin_category(1, "users")
    monkeypatch.setattr(manage_user, "get_step", messages.get_step)
    monkeypatch.setattr(manage_user, "set_step", service.set_step)
    e = event("👤 مدیریت کاربر")
    # The wrapper tests service writes elsewhere; the entry is the original handler.
    await manage_user.msg_manage_user_admin.__wrapped__(e)
    assert state["step"] == "MToUser"
    assert "آیدی عددی" in e.respond.await_args.args[0]


@pytest.mark.parametrize("action", ["tickets", "cx", "cxstats", "autorenew", "miniapp"])
async def test_shortcuts_delegate_to_existing_authorized_entry(navigation, monkeypatch, action):
    from app.telegram.user.admin_app import module as web
    from app.telegram.user.auto_renew import handlers as ar
    from app.telegram.user.customer_experience import handlers as cx

    target, name = {
        "tickets": (cx, "show_inbox"),
        "cx": (cx, "control_panel"),
        "cxstats": (cx, "stats"),
        "autorenew": (ar, "admin"),
        "miniapp": (web, "open_app"),
    }[action]
    handler = AsyncMock()
    monkeypatch.setattr(target, name, handler)
    e = event()
    await service.open_admin_shortcut(e, action)
    handler.assert_awaited_once_with(e, **({"staff": True} if action == "tickets" else {}))
    assert navigation[0]["step"] == "panel"


def test_navigation_registered_before_free_text_consumers():
    assert module.MODULE_ORDER < 100
    root = Path(__file__).resolve().parents[1] / "app/telegram/admin"
    for name in ("channel_lock", "bulk_increase", "discounts", "logs", "wallets"):
        tree = ast.parse((root / name / "callbacks.py").read_text())
        # Reply markup must not creep back into message-edit callbacks.
        assert not any(isinstance(n, ast.Name) and n.id == "Panel_Admin_Buttons" for n in ast.walk(tree))
