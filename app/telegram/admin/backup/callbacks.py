"""Callback handlers for admin backup."""

from pathlib import Path

from telethon import events

from app.db.crud.log_channels import LogChannelManager
from app.db.crud.settings import SettingsManager
from app.db.redis import get_redis
from app.logger import LogType, get_logger
from app.services.backup import run_backup_and_send
from app.telegram.admin.backup import keyboards, states, texts
from app.telegram.state import clear_step, get_step, set_step
from config import ADMIN_ID

logger = get_logger(__name__)

_BACKUP_CALLBACKS = frozenset({
    "backup_run_now",
    "backup_set_interval",
    "backup_menu",
    "backup_restore_start",
    "backup_restore_confirm",
    "backup_restore_cancel",
})


def _backup_callback_filter(event: events.CallbackQuery.Event) -> bool:
    if event.sender_id not in ADMIN_ID:
        return False
    return event.data.decode("utf-8") in _BACKUP_CALLBACKS


async def _current_interval() -> int:
    settings = await SettingsManager().get_settings()
    if not settings:
        return 24
    return max(0, int(getattr(settings, "backup_interval_hours", 24) or 0))


async def _channel_configured() -> bool:
    dest = await LogChannelManager().get_log_channel_destination(LogType.BACKUP.value)
    return dest is not None


async def _edit_menu(event: events.CallbackQuery.Event) -> None:
    hours = await _current_interval()
    await set_step(event.sender_id, states.BACKUP_STEP)
    await event.edit(
        texts.menu_text(hours, await _channel_configured()),
        buttons=keyboards.menu_buttons(hours),
        parse_mode="md",
    )


async def _cleanup_restore_file(admin_id: int) -> None:
    """Clean up stored restore ZIP path and file.

    Uses both Redis (if available) and deterministic temp directory for cleanup.
    """
    import shutil

    # Try Redis-based cleanup first
    redis = await get_redis()
    if redis:
        try:
            key = f"pasarguardbot:restore_zip:{admin_id}"
            zip_path_str = await redis.get(key)
            if zip_path_str:
                zip_path = Path(zip_path_str)
                if zip_path.is_file():
                    zip_path.unlink(missing_ok=True)
                try:
                    zip_path.parent.rmdir()
                except OSError:
                    pass
                await redis.delete(key)
        except Exception as exc:
            logger.warning("Redis cleanup for restore file failed: %s", exc)

    # Always clean up deterministic temp directory too
    try:
        import tempfile
        d = Path(tempfile.gettempdir()) / "pasarguardbot-restores" / str(admin_id)
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
    except Exception:
        pass


async def _get_restore_path(admin_id: int) -> str | None:
    """Get the stored restore ZIP path.

    Checks Redis first, then falls back to deterministic temp directory.
    """
    # Try Redis
    redis = await get_redis()
    if redis:
        key = f"pasarguardbot:restore_zip:{admin_id}"
        path_str = await redis.get(key)
        if path_str:
            return path_str

    # Fallback: check deterministic temp directory
    import tempfile
    d = Path(tempfile.gettempdir()) / "pasarguardbot-restores" / str(admin_id)
    if d.exists():
        # Find any .zip file in the directory
        for f in d.glob("*.zip"):
            return str(f)

    return None


async def callback_backup(event: events.CallbackQuery.Event):
    data = event.data.decode("utf-8")

    if data == "backup_menu":
        await _edit_menu(event)
        return

    if data == "backup_set_interval":
        hours = await _current_interval()
        await set_step(event.sender_id, states.SET_BACKUP_INTERVAL_STEP)
        await event.edit(
            (
                "⏱ **تنظیم فاصله بکاپ خودکار**\n\n"
                f"مقدار فعلی: `{hours}` ساعت\n"
                "عدد ساعت را ارسال کنید (مثال: `1` یا `24`).\n"
                "برای خاموش کردن بکاپ خودکار عدد `0` را بفرستید."
            ),
            buttons=keyboards.interval_prompt_buttons(),
            parse_mode="md",
        )
        return

    if data == "backup_run_now":
        if not await _channel_configured():
            await event.answer(texts.CHANNEL_NOT_SET_ALERT, alert=True)
            hours = await _current_interval()
            await event.edit(
                f"{texts.CHANNEL_NOT_SET}\n\n{texts.menu_text(hours, False)}",
                buttons=keyboards.menu_buttons(hours),
                parse_mode="md",
            )
            return

        await event.answer()
        await event.edit(texts.WORKING)
        result = await run_backup_and_send(trigger="manual")
        hours = await _current_interval()
        await event.edit(
            f"{result.message}\n\n{texts.menu_text(hours, await _channel_configured())}",
            buttons=keyboards.menu_buttons(hours),
            parse_mode="md",
        )
        return

    if data == "backup_restore_start":
        await event.answer()
        await set_step(event.sender_id, states.RESTORE_WAITING_FILE_STEP)
        await event.edit(
            texts.RESTORE_WAITING_FILE,
            buttons=keyboards.restore_waiting_buttons(),
            parse_mode="md",
        )
        return

    if data == "backup_restore_confirm":
        await event.answer()
        step = await get_step(event.sender_id)
        if step != states.RESTORE_CONFIRM_STEP:
            await event.edit(
                "❌ جلسه ریستور منقضی شده. لطفاً دوباره تلاش کنید.",
                buttons=keyboards.menu_buttons(await _current_interval()),
                parse_mode="md",
            )
            return

        await event.edit(texts.RESTORE_WORKING, parse_mode="md")

        # Get stored zip path from Redis (or deterministic fallback)
        zip_path_str = await _get_restore_path(event.sender_id)

        if not zip_path_str:
            await event.edit(
                "❌ فایل بکاپ پیدا نشد. لطفاً دوباره تلاش کنید.",
                buttons=keyboards.menu_buttons(await _current_interval()),
                parse_mode="md",
            )
            await clear_step(event.sender_id)
            return

        zip_path = Path(zip_path_str)
        if not zip_path.is_file():
            await event.edit(
                "❌ فایل بکاپ حذف شده. لطفاً دوباره تلاش کنید.",
                buttons=keyboards.menu_buttons(await _current_interval()),
                parse_mode="md",
            )
            await clear_step(event.sender_id)
            return

        from app.services.restore import restore_from_zip

        result = await restore_from_zip(zip_path)

        # Clean up temp file and Redis key
        await _cleanup_restore_file(event.sender_id)

        hours = await _current_interval()
        await event.edit(
            result.message,
            buttons=keyboards.menu_buttons(hours),
            parse_mode="md",
        )

        await clear_step(event.sender_id)
        return

    if data == "backup_restore_cancel":
        await event.answer()
        # Clean up stored file
        await _cleanup_restore_file(event.sender_id)
        await clear_step(event.sender_id)
        hours = await _current_interval()
        await event.edit(
            f"{texts.RESTORE_CANCELLED}\n\n{texts.menu_text(hours, await _channel_configured())}",
            buttons=keyboards.menu_buttons(hours),
            parse_mode="md",
        )
        return


def register(client):
    client.add_event_handler(
        callback_backup,
        events.CallbackQuery(func=_backup_callback_filter),
    )
