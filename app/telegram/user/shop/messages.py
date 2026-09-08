"""Message handlers for user shop purchase flow."""

from __future__ import annotations

import httpx
from httpx import HTTPStatusError
from pasarguard import PasarguardAPI
from telethon import Button, events
from telethon.tl.custom import Message

from app import Kenzo
from app.db.crud.discount_codes import DiscountCodeManager
from app.db.crud.keyboards import get_button_text
from app.db.crud.panels import PanelsManager
from app.db.crud.plans import PlanManager
from app.db.crud.settings import SettingsManager
from app.logger import get_logger
from app.services.panels.nodes import filter_nodes_by_plan_type
from app.telegram.keyboards.buy import (
    build_buy_confirm_button_rows,
    build_buy_service_selection_rows,
    buy_default_username_button,
)
from app.telegram.keyboards.common import is_keyboard_config_step
from app.telegram.keyboards.home import bhome_buttons
from app.telegram.shared.guards.channel_gate import ensure_channel_membership, extract_start_param
from app.telegram.shared.keyboards.panel_buttons import build_panel_display_button
from app.telegram.shared.utils.maintenance import bot_is_offline
from app.telegram.shared.utils.username import (
    is_valid_username,
)
from app.telegram.state import clear_user, get_data, get_step, set_data, set_step
from app.telegram.user.shop.callbacks import (
    buy_discount_code_filter,
    buy_username_message_filter,
)
from app.telegram.user.shop.helpers import (
    _buy_intro_text,
    _buy_username_context,
    _confirm_buy_username,
    _user_lang,
    show_buy_vpn_plans,
)
from app.utils.formatting.conversions import convert_storage
from app.utils.formatting.traffic import format_ip_limit
from app.utils.text.bot_texts import get_bot_text

logger = get_logger(__name__)


@bot_is_offline
async def buy_service_handler(event: Message):
    if not await ensure_channel_membership(event):
        raise events.StopPropagation

    user_id = event.sender_id
    lang = await _user_lang(user_id)
    setting = await SettingsManager().get_settings()
    if setting and not setting.sale_mode:
        await event.respond("⛔️ فروش توسط ادمین بسته است.", buttons=await bhome_buttons(user_id, lang))
        raise events.StopPropagation

    panel_manager = PanelsManager()
    panels = await panel_manager.get_available_panels()

    remove_keyboard_msg = await event.respond("⏳", buttons=Button.clear())
    await remove_keyboard_msg.delete()

    if setting and setting.single_panel_buy_mode and len(panels) == 1:
        await set_step(user_id, "selectService")
        await show_buy_vpn_plans(event, panels[0], lang=lang, back_data="DataCancel")
        raise events.StopPropagation

    service_buttons = []
    for panel in panels:
        service_buttons.append(await build_panel_display_button(panel, f"BuyVPN_{panel.code}"))

    service_rows = await build_buy_service_selection_rows(service_buttons)
    await set_step(user_id, "selectService")
    buy_intro = await _buy_intro_text(lang)
    await Kenzo.send_message(entity=user_id, message=buy_intro, buttons=service_rows)
    raise events.StopPropagation


@bot_is_offline
async def buy_username_message_handler(event: Message):
    username = (event.message.message or "").strip()
    panel, _gig, _plan = await _buy_username_context(event.sender_id)
    if not is_valid_username(username):
        await event.respond(
            "❌ نام کاربری باید بین ۳ تا ۳۲ کاراکتر و فقط شامل حروف انگلیسی، اعداد و زیرخط باشد.",
            buttons=[[await buy_default_username_button(b"generate_username")]],
        )
        raise events.StopPropagation
    try:
        await PasarguardAPI(panel.base_url).get_user_by_username(username=username, token=panel.cookie)
        await event.respond(
            "❌ نام کاربری توسط شخص دیگری ساخته شده\n\n^q^لطفا نام کاربری دیگری ارسال کنید یا اینکه روی دکمه زیر کلیک کنید تا اسم رندوم ساخته شود^q^",
            buttons=[[await buy_default_username_button(b"generate_username")]],
        )
        raise events.StopPropagation
    except HTTPStatusError as e:
        if e.response.status_code != 404:
            await event.respond("خطا در ارتباط با پنل")
            raise events.StopPropagation from None

    await _confirm_buy_username(event, username, edit=False)
    raise events.StopPropagation


@bot_is_offline
async def buy_discount_code_handler(event: Message):
    msg = event.message.message
    lang = await _user_lang(event.sender_id)
    status, res = await DiscountCodeManager().validate_discount_code(code=msg, user_id=event.sender_id)
    msgid_buy = await get_data(event.sender_id, "msgid_Buy")
    await event.client.delete_messages(event.chat_id, msgid_buy)
    if not status:
        await event.respond(f"{res}", buttons=await bhome_buttons(event.sender_id, lang))
        await clear_user(event.sender_id)
        await set_step(event.sender_id, "home")
        raise events.StopPropagation

    panel_code = await get_data(event.sender_id, "panel")
    gig = await get_data(event.sender_id, "gig")
    panel = await PanelsManager().get_panel_by_code(code=panel_code)
    plan_id = await get_data(event.sender_id, "selected_plan_id")
    if plan_id:
        plan = await PlanManager().get_plan(plan_id)
    else:
        plan = await PlanManager().get_plan_by_volume_for_display(
            gb=float(gig), enabled_only=True, panel_code=panel_code
        )
    if not plan:
        await set_step(event.sender_id, "home")
        await event.respond("پلن غیرفعال یا حذف شده است؛ دوباره یک پلن انتخاب کنید.")
        return
    new_amount = int(plan.price - (plan.price * (res.discount_percentage / 100)))

    try:
        api = PasarguardAPI(base_url=panel.base_url)
        nodes_stats = await api.get_nodes(token=panel.cookie)
        filtered_nodes = filter_nodes_by_plan_type(nodes_stats.nodes, plan, panel)
        locations = " ⌁ ".join([f"{node.name}" for node in filtered_nodes]) or " "
    except httpx.HTTPStatusError as e:
        locations = (
            "🇺🇸 🇹🇷 🇫🇮 🇩🇪 🇦🇲 " if e.response.status_code == 403 else "❌ خطا در دریافت نودها، لطفاً دوباره تلاش کنید."
        )

    ip_limit_text = format_ip_limit(getattr(plan, "ip_limit", 0))
    volume_text = convert_storage(
        float(gig), getattr(plan, "plan_type", None), getattr(plan, "data_limit_reset_strategy", None)
    )
    confirm_text_template = await get_bot_text(
        key="config_purchase_discount_confirm",
        default=(
            "**ساخت کانفیگ اختصاصی V2Ray با مشخصات زیر را تأیید می‌کنید؟**\n\n"
            "**▪️ حجم سرویس :** {volume}\n"
            "**⏰ مدت زمان :** {duration} روز\n"
            "**▫️نوع کانفیگ :** {config_type}\n"
            "**▫️ لوکیشن های موجودسرویس :** \n**^qc^{locations}^qc^**\n"
            "**🔌 محدودیت کاربر :** {user_limit}\n"
            "**💸 مبلغ قبل:** `{original_price}` **مبلغ جدید:** `{new_price}`\n"
            "❗️ نکته؛\n"
            "(پس از خرید؛ امکان افزایش حجم وجود دارد و همچنین مقدار باقیمانده حجم و روز از بخش سرویس‌های من قابل مشاهده است)"
        ),
        lang="fa",
    )
    confirm_text = (
        confirm_text_template.replace("{volume}", volume_text)
        .replace("{duration}", str(plan.duration))
        .replace("{config_type}", panel.name)
        .replace("{locations}", locations)
        .replace("{user_limit}", ip_limit_text)
        .replace("{original_price}", f"{int(plan.price):,}")
        .replace("{new_price}", f"{int(new_amount):,}")
    )
    confirm_buttons = [
        [Button.inline("🎉 کد تخفیف اعمال شد", "none")],
        *(await build_buy_confirm_button_rows(confirm_data="Confirm_buy", with_discount=False)),
    ]
    await event.respond(confirm_text, buttons=confirm_buttons, link_preview=False)
    await set_data(event.sender_id, "codetakhfif", res.code)
    await set_data(event.sender_id, "codetakhfif_newprice", new_amount)
    await set_step(event.sender_id, "Takhfif_confirm_purchase")
    raise events.StopPropagation


async def buy_service_filter(event: Message) -> bool:
    if event.is_channel or not event.is_private:
        return False
    if await get_step(event.sender_id) == "ban":
        return False
    if is_keyboard_config_step(await get_step(event.sender_id)):
        return False

    msg = event.message.text or event.message.message or ""
    if not msg:
        return False

    param = extract_start_param(event)
    if param and param.lower() == "buy":
        return True
    if msg == "/buy":
        return True
    menu_text = await get_button_text("bt.menu_buy_service", "🛍 خرید سرویس")
    return msg in {menu_text, "🛍 خرید سرویس"}


async def account_discount_message_filter(event: Message) -> bool:
    if event.is_channel or not event.is_private:
        return False
    if not (event.message.message or event.message.text or ""):
        return False
    return await get_step(event.sender_id) == "WhatingForAccountCodeTakhfif"


def register(client):
    client.add_event_handler(buy_service_handler, events.NewMessage(incoming=True, func=buy_service_filter))
    client.add_event_handler(
        buy_username_message_handler, events.NewMessage(incoming=True, func=buy_username_message_filter)
    )
    client.add_event_handler(buy_discount_code_handler, events.NewMessage(incoming=True, func=buy_discount_code_filter))
