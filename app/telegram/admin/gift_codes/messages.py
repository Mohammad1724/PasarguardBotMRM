"""Message handlers for admin gift code management."""

from __future__ import annotations

from telethon import events
from telethon.tl.custom import Message

from app.db.crud.gift_codes import GiftCodeCRUD
from app.telegram.admin.gift_codes import keyboards, service, states, texts
from app.telegram.state import clear_user, get_data, get_step, set_data, set_step
from config import ADMIN_ID


async def _gift_admin_message_filter(event: Message) -> bool:
    if event.sender_id not in ADMIN_ID:
        return False
    msg = (event.message.text or "").strip()
    if msg in (states.GIFT_MENU_MESSAGE, states.GIFT_MENU_MESSAGE_ALT):
        return True
    step = await get_step(event.sender_id)
    return step in states.GIFT_ADMIN_STEPS


async def message_handler_gift_admin(event: Message):
    msg = (event.message.text or "").strip()
    step = await get_step(event.sender_id)

    if msg in (states.GIFT_MENU_MESSAGE, states.GIFT_MENU_MESSAGE_ALT):
        await event.respond(texts.gift_main_menu_text(), buttons=keyboards.gift_main_menu_buttons())
        await set_step(event.sender_id, "gift_select")
        raise events.StopPropagation

    if step == "gift_manual_code":
        code = msg.strip().upper()
        if await GiftCodeCRUD().get_by_code(code):
            await event.respond("❌ این کد قبلاً ثبت شده است. کد دیگری انتخاب کنید:", buttons=keyboards.gift_back_row())
            raise events.StopPropagation
        await set_data(event.sender_id, "gift_code", code)
        await event.respond("مقدار کد را وارد کنید:", buttons=keyboards.gift_back_row())
        await set_step(event.sender_id, states.GIFT_VALUE_STEP_BY_TYPE[await get_data(event.sender_id, "gift_type")])
        raise events.StopPropagation

    if step in ("gift_value_balance", "gift_value_days", "gift_value_volume") and msg.isdigit():
        value = int(msg)
        gtype = {"gift_value_balance": "balance", "gift_value_days": "days", "gift_value_volume": "volume"}[step]
        if gtype == "balance" and value <= 0:
            await event.respond("مقدار باید بزرگ‌تر از صفر باشد:", buttons=keyboards.gift_back_row())
            raise events.StopPropagation
        await set_data(event.sender_id, "gift_value", value)
        await event.respond("سقف کل استفاده را وارد کنید (تعداد):", buttons=keyboards.gift_back_row())
        await set_step(event.sender_id, "gift_uses")
        raise events.StopPropagation
    if step in ("gift_value_balance", "gift_value_days", "gift_value_volume"):
        await event.respond(texts.GIFT_NUMERIC_ONLY, buttons=keyboards.gift_back_row())
        raise events.StopPropagation

    if step == "gift_uses" and msg.isdigit() and int(msg) > 0:
        await set_data(event.sender_id, "gift_uses", int(msg))
        await event.respond("سقف استفاده برای هر کاربر را وارد کنید (تعداد):", buttons=keyboards.gift_back_row())
        await set_step(event.sender_id, "gift_per_user")
        raise events.StopPropagation
    if step == "gift_uses":
        await event.respond(texts.GIFT_NUMERIC_ONLY, buttons=keyboards.gift_back_row())
        raise events.StopPropagation

    if step == "gift_per_user" and msg.isdigit() and int(msg) > 0:
        await set_data(event.sender_id, "gift_per_user", int(msg))
        await event.respond("مدت اعتبار کد را انتخاب کنید:", buttons=keyboards.gift_expiry_buttons())
        await set_step(event.sender_id, "gift_expiry")
        raise events.StopPropagation
    if step == "gift_per_user":
        await event.respond(texts.GIFT_NUMERIC_ONLY, buttons=keyboards.gift_back_row())
        raise events.StopPropagation

    if step == "gift_note":
        await set_data(event.sender_id, "gift_note", msg or None)
        code = await get_data(event.sender_id, "gift_code") or await service.generate_gift_code()
        expires_at = await get_data(event.sender_id, "gift_expires_at")
        gift_code = await service.create_gift_with_log(
            code=code,
            type=await get_data(event.sender_id, "gift_type"),
            value=int(await get_data(event.sender_id, "gift_value")),
            max_uses=int(await get_data(event.sender_id, "gift_uses")),
            per_user_limit=int(await get_data(event.sender_id, "gift_per_user")),
            expires_at=int(expires_at) if expires_at else None,
            note=msg or None,
        )
        await clear_user(event.sender_id)
        await set_step(event.sender_id, "gift_select")
        if gift_code:
            await event.respond(f"✅ کد هدیه ساخته شد: `{gift_code}`", buttons=keyboards.gift_main_menu_buttons())
        else:
            await event.respond("❌ خطا در ساخت کد (کد تکراری؟)", buttons=keyboards.gift_main_menu_buttons())
        raise events.StopPropagation


def register(client):
    client.add_event_handler(
        message_handler_gift_admin,
        events.NewMessage(incoming=True, func=_gift_admin_message_filter),
    )
