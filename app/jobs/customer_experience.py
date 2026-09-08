"""Limited, consent-based trial follow-up; registration does not enable sending."""

from telethon import Button

from app import Kenzo
from app.services.customer_experience.journeys import send_followups
from app.services.locks import distributed_lock


async def followup_job():
    async def send(user_id, code):
        await Kenzo.send_message(
            user_id,
            "تست شما تمام شده است. اگر مایلید ادامه دهید، می‌توانید پلن‌های همان سرویس را ببینید.\n"
            "این تنها پیام پیگیری این تست است؛ خرید کاملاً اختیاری است.",
            buttons=[
                [Button.inline("مشاهده پلن و ادامه", f"cx:follow:{code}")],
                [Button.inline("لغو یادآوری‌ها", "cx:optout")],
            ],
            parse_mode=None,
        )

    async with distributed_lock("cx-followups"):
        return await send_followups(send)
