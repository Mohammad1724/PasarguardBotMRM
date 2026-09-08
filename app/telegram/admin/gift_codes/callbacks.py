"""Callback handlers for admin gift code management."""

from __future__ import annotations

import time

from telethon import events

from app.db.crud.gift_codes import GiftCodeCRUD
from app.telegram.admin.gift_codes import keyboards, states, texts
from app.telegram.state import clear_user, get_data, get_step, set_data, set_step
from config import ADMIN_ID


def _gift_admin_callback_filter(event: events.CallbackQuery.Event) -> bool:
    if event.sender_id not in ADMIN_ID:
        return False
    data = event.data.decode("utf-8", errors="ignore")
    return data.startswith(
        (
            "gift_create",
            "gift_list:",
            "gift_view:",
            "gift_toggle:",
            "gift_delete:",
            "gift_type:",
            "gift_exp:",
            "gift_cancel",
            "gift_back_main",
            "gift_back_panel",
            "gift_skip_code",
        )
    )


async def _prompt_gift_value(event, gtype: str) -> None:
    await set_data(event.sender_id, "gift_type", gtype)
    prompts = {
        "balance": "مبلغ شارژ کیف پول را به تومان وارد کنید (مثال: 50000):",
        "days": "تعداد روز رایگان را وارد کنید (مثال: 30):",
        "volume": "مقدار حجم رایگان را به گیگابایت وارد کنید (مثال: 10):",
    }

    await event.edit(prompts[gtype], buttons=keyboards.gift_back_row())
    await set_step(event.sender_id, states.GIFT_VALUE_STEP_BY_TYPE[gtype])


async def callback_gift_admin(event: events.CallbackQuery.Event):
    data = event.data.decode("utf-8", errors="ignore")
    crud = GiftCodeCRUD()

    if data == "gift_create":
        await set_data(event.sender_id, "gift_code", None)
        await event.edit("نوع کد هدیه را انتخاب کنید:", buttons=keyboards.gift_type_buttons())
        await set_step(event.sender_id, "gift_type")

    elif data.startswith("gift_type:"):
        if (await get_step(event.sender_id)) == "gift_type":
            await _prompt_gift_value(event, data.split(":")[1])
        else:
            await event.answer(texts.GIFT_INVALID_CODE, alert=True)

    elif data == "gift_skip_code":
        await event.delete()
        await event.respond("مقدار کد را وارد کنید:", buttons=keyboards.gift_back_row())
        await set_step(event.sender_id, states.GIFT_VALUE_STEP_BY_TYPE[await get_data(event.sender_id, "gift_type")])

    elif data.startswith("gift_exp:"):
        days = int(data.split(":")[1])
        expires_at = int(time.time()) + days * 86400 if days > 0 else 0
        await set_data(event.sender_id, "gift_expires_at", expires_at)
        await event.edit("یک یادداشت اختیاری برای این کد ارسال کنید (یا `-` بفرستید):", buttons=keyboards.gift_back_row())
        await set_step(event.sender_id, "gift_note")

    elif data == "gift_cancel" or data == "gift_back_main":
        await clear_user(event.sender_id)
        await set_step(event.sender_id, "gift_select")
        await event.edit(texts.gift_main_menu_text(), buttons=keyboards.gift_main_menu_buttons())

    elif data == "gift_back_panel":
        await clear_user(event.sender_id)
        await set_step(event.sender_id, "panel")
        await event.delete()

    elif data.startswith("gift_list:"):
        page = int(data.split(":")[1])
        gifts = await crud.get_all()
        if not gifts:
            await event.edit("هنوز کد هدیه‌ای ساخته نشده است.", buttons=keyboards.gift_main_menu_buttons())
        else:
            await event.edit(f"🎛 **لیست کدهای هدیه** ({len(gifts)} کد)", buttons=keyboards.gift_list_buttons(gifts, page))

    elif data.startswith("gift_view:"):
        _, gift_id, page = data.split(":")
        gift = await crud.get(int(gift_id))
        if not gift:
            await event.answer(texts.GIFT_INVALID_CODE, alert=True)
            return
        uses = await crud.list_uses(gift.id)
        uses_count = gift.times_used or 0
        await event.edit(
            texts.gift_info_text(gift, uses, uses_count),
            buttons=keyboards.gift_view_buttons(gift, int(page)),
        )

    elif data.startswith("gift_toggle:"):
        _, gift_id, page = data.split(":")
        gift = await crud.get(int(gift_id))
        if gift:
            await crud.update(gift.id, is_active=not gift.is_active)
            gift = await crud.get(gift.id)
        if gift:
            await event.edit(
                texts.gift_info_text(gift, await crud.list_uses(gift.id), gift.times_used or 0),
                buttons=keyboards.gift_view_buttons(gift, int(page)),
            )

    elif data.startswith("gift_delete:"):
        _, gift_id, page = data.split(":")
        await crud.delete(int(gift_id))
        gifts = await crud.get_all()
        await event.edit("🗑 کد حذف شد.", buttons=keyboards.gift_list_buttons(gifts, int(page)) if gifts else keyboards.gift_main_menu_buttons())


def register(client):
    client.add_event_handler(
        callback_gift_admin,
        events.CallbackQuery(func=_gift_admin_callback_filter),
    )
