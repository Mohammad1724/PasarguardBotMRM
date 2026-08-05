"""Message handlers for admin backup."""

import shutil
import tempfile
from pathlib import Path

from telethon import events
from telethon.tl.custom import Message

from app.db.crud.log_channels import LogChannelManager
from app.db.crud.settings import SettingsManager
from app.db.redis import get_redis
from app.jobs.backup import reschedule_backup_job
from app.logger import LogTag, LogType, get_logger
from app.services.restore import validate_backup_zip
from app.telegram.admin.backup import keyboards, states, texts
from app.telegram.state import get_step, set_step
from config import ADMIN_ID

logger = get_logger(__name__)

_MAX_RESTORE_FILE_SIZE = 500 * 1024 * 1024  # 500 MB

# Deterministic temp directory for restore files — allows cleanup without Redis.
_RESTORE_TMP_BASE = Path(tempfile.gettempdir()) / "pasarguardbot-restores"


def _restore_temp_dir(admin_id: int) -> Path:
    """Return a deterministic temp directory for a given admin's restore upload."""
    d = _RESTORE_TMP_BASE / str(admin_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cleanup_restore_dir(admin_id: int) -> None:
    """Remove the temp directory for an admin's restore upload."""
    try:
        d = _RESTORE_TMP_BASE / str(admin_id)
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
    except Exception:
        pass


async def _current_interval() -> int:
    settings = await SettingsManager().get_settings()
    if not settings:
        return 24
    return max(0, int(getattr(settings, "backup_interval_hours", 24) or 0))


async def _channel_configured() -> bool:
    dest = await LogChannelManager().get_log_channel_destination(LogType.BACKUP.value)
    return dest is not None


async def _show_menu(event: Message) -> None:
    hours = await _current_interval()
    await set_step(event.sender_id, states.BACKUP_STEP)
    await event.respond(
        texts.menu_text(hours, await _channel_configured()),
        buttons=keyboards.menu_buttons(hours),
        parse_mode="md",
    )


async def _backup_message_filter(event: Message) -> bool:
    if event.sender_id not in ADMIN_ID or not event.is_private:
        return False
    msg = (event.message.text or "").strip()
    if msg == states.BACKUP_MENU_TRIGGER:
        return True
    step = await get_step(event.sender_id)
    if step == states.SET_BACKUP_INTERVAL_STEP and bool(msg):
        return True
    return False


async def _backup_document_filter(event: Message) -> bool:
    """Filter for document messages when in restore waiting state."""
    if event.sender_id not in ADMIN_ID or not event.is_private:
        return False
    if not event.document:
        return False
    step = await get_step(event.sender_id)
    return step == states.RESTORE_WAITING_FILE_STEP


async def message_handler_backup_document(event: Message):
    """Handle ZIP file upload for restore."""
    step = await get_step(event.sender_id)
    if step != states.RESTORE_WAITING_FILE_STEP:
        return

    # Check file size
    file_size = event.document.size or 0
    if file_size > _MAX_RESTORE_FILE_SIZE:
        await event.respond(
            texts.RESTORE_TOO_LARGE,
            buttons=keyboards.restore_waiting_buttons(),
            parse_mode="md",
        )
        raise events.StopPropagation

    # Check file extension (must be .zip)
    filename = ""
    for attr in event.document.attributes:
        if hasattr(attr, "file_name") and attr.file_name:
            filename = attr.file_name
            break

    if not filename.lower().endswith(".zip"):
        await event.respond(
            texts.RESTORE_NOT_ZIP,
            buttons=keyboards.restore_waiting_buttons(),
            parse_mode="md",
        )
        raise events.StopPropagation

    # Download the file — use deterministic temp dir so cleanup works without Redis
    progress_msg = await event.respond("⏳ در حال دانلود فایل بکاپ...")

    temp_dir = _restore_temp_dir(event.sender_id)
    zip_path = temp_dir / filename

    try:
        await event.client.download_media(event.document, file=str(zip_path))
    except Exception as exc:
        logger.error("%s Failed to download restore file: %s", LogTag.JOB, exc)
        _cleanup_restore_dir(event.sender_id)
        await progress_msg.edit(
            f"❌ خطا در دانلود فایل: {exc}",
            buttons=keyboards.restore_waiting_buttons(),
            parse_mode="md",
        )
        raise events.StopPropagation

    if not zip_path.is_file() or zip_path.stat().st_size == 0:
        _cleanup_restore_dir(event.sender_id)
        await progress_msg.edit(
            texts.RESTORE_INVALID_FILE,
            buttons=keyboards.restore_waiting_buttons(),
            parse_mode="md",
        )
        raise events.StopPropagation

    # Validate the ZIP
    info = await validate_backup_zip(zip_path)

    if not info["has_sql"]:
        await progress_msg.edit(
            texts.RESTORE_NO_SQL,
            buttons=keyboards.restore_waiting_buttons(),
            parse_mode="md",
        )
        _cleanup_restore_dir(event.sender_id)
        raise events.StopPropagation

    # Store zip path in Redis temporarily (fallback: deterministic path works without Redis)
    redis = await get_redis()
    if redis:
        key = f"pasarguardbot:restore_zip:{event.sender_id}"
        await redis.set(key, str(zip_path), ex=300)  # 5 minutes

    # Show confirmation
    zip_size_mb = info["zip_size"] / (1024 * 1024)
    sql_size_mb = info["sql_size"] / (1024 * 1024)
    crypto_status = texts.RESTORE_CRYPTO_FOUND if info["crypto_key"] else texts.RESTORE_CRYPTO_NOT_FOUND

    await set_step(event.sender_id, states.RESTORE_CONFIRM_STEP)
    await progress_msg.edit(
        texts.RESTORE_CONFIRM_TEMPLATE.format(
            filename=filename,
            zip_size_mb=zip_size_mb,
            sql_size_mb=sql_size_mb,
            crypto_status=crypto_status,
        ),
        buttons=keyboards.restore_confirm_buttons(),
        parse_mode="md",
    )
    raise events.StopPropagation


async def message_handler_backup(event: Message):
    msg = (event.message.text or "").strip()
    step = await get_step(event.sender_id)

    if msg == states.BACKUP_MENU_TRIGGER:
        await _show_menu(event)
        raise events.StopPropagation

    if step == states.SET_BACKUP_INTERVAL_STEP and msg:
        if not msg.isdigit():
            await event.respond(texts.NUMERIC_ONLY, buttons=keyboards.interval_prompt_buttons())
            raise events.StopPropagation

        hours = int(msg)
        settings = await SettingsManager().get_settings()
        if not settings:
            await event.respond("❌ تنظیمات ربات یافت نشد.")
            raise events.StopPropagation

        await SettingsManager().update_setting(settings.id, backup_interval_hours=hours)
        reschedule_backup_job(hours)

        if hours <= 0:
            await event.respond(texts.INTERVAL_DISABLED, parse_mode="md")
        else:
            await event.respond(texts.INTERVAL_SAVED_TEMPLATE.format(hours=hours), parse_mode="md")

        await _show_menu(event)
        raise events.StopPropagation


def register(client):
    # Register document handler first (higher priority for file uploads)
    client.add_event_handler(
        message_handler_backup_document,
        events.NewMessage(incoming=True, func=_backup_document_filter),
    )
    client.add_event_handler(
        message_handler_backup,
        events.NewMessage(incoming=True, func=_backup_message_filter),
    )
