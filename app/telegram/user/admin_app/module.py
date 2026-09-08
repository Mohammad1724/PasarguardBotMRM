from telethon import events
from telethon.tl.types import KeyboardButtonWebView

from app.db.base import AsyncSessionLocal as Session
from app.services.admin_app.auth import ADMIN_MINI_APP_URL, identity, origin

MODULE_NAME = "user.admin_app"
MODULE_ENABLED = True
MODULE_ORDER = 120
_registered_clients = set()


def setup(client):
    if id(client) in _registered_clients:
        return
    _registered_clients.add(id(client))

    @client.on(events.NewMessage(pattern=r"^/adminapp(?:@\w+)?$"))
    async def open_app(event):
        if not event.is_private:
            return
        try:
            origin()
            async with Session() as session:
                await identity(session, event.sender_id)
        except Exception:
            await event.respond("دسترسی یا آدرس مینی‌اپ آماده نیست. مالک باید ADMIN_MINI_APP_URL را تنظیم کند.")
            raise events.StopPropagation from None
        await event.respond(
            "🖥 پنل مدیریت\nورود امن با حساب تلگرام شما؛ نشست ۳۰ دقیقه اعتبار دارد.",
            buttons=[[KeyboardButtonWebView("باز کردن پنل مدیریت", ADMIN_MINI_APP_URL)]],
        )
        raise events.StopPropagation
