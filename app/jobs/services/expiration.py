import asyncio
import time
from datetime import datetime

from telethon import errors

from app import Kenzo
from app.db.crud.panels import PanelsManager
from app.db.crud.services import ServiceCRUD
from app.db.crud.user import set_user_status
from app.logger import LogTag, LogType, get_logger
from app.services.panels.settings import panel_webhook_notifications_enabled
from app.telegram.shared.utils.logging import send_log_message

logger = get_logger(__name__)


async def cleanup_expired_test_services():
    from app.services.customer_experience.lifecycle import cleanup_trials

    return await cleanup_trials()


async def cleanup_expired_paid_services(panel_codes: list[int], current_time: int) -> int:
    from app.services.auto_renew.cleanup import cleanup_paid

    return await cleanup_paid(panel_codes, current_time)


async def handle_service_expiration():
    start_time = time.time()
    logger.debug("%s handle_service_expiration started", LogTag.JOB)

    service_crud = ServiceCRUD()
    current_time = int(datetime.now().timestamp())
    expiring_time = current_time + 24 * 60 * 60

    all_panels = await PanelsManager().get_all_panels()
    panels_without_webhook = [p for p in all_panels if not panel_webhook_notifications_enabled(p)]
    all_panel_codes = [p.code for p in all_panels]

    # Always run cleanup: test services + paid services expired 3+ days (all panels, regardless of webhook)
    await cleanup_expired_test_services()
    cleanup_deletions = await cleanup_expired_paid_services(all_panel_codes, current_time)

    if not panels_without_webhook:
        elapsed = time.time() - start_time
        logger.debug(
            f"{LogTag.JOB} handle_service_expiration: All panels have webhooks enabled, skipping notifications. "
            f"Cleanup deleted {cleanup_deletions} paid services. Elapsed: {elapsed:.2f}s"
        )
        return

    panel_codes_without_webhook = [p.code for p in panels_without_webhook]
    logger.debug(f"{LogTag.JOB} handle_service_expiration: {len(panels_without_webhook)} panels without webhooks")

    expiry_notifications = 0
    warning_notifications = 0
    services_checked = 0

    # Process in batches of 500 (paid services only; test services already cleaned)
    batch_size = 500
    offset = 0
    total_processed = 0

    while True:
        # Fetch batch
        batch = await service_crud.get_services_for_expiration_check_batch(
            panel_codes_without_webhook, current_time, expiring_time, batch_size, offset
        )

        if not batch:
            break

        logger.debug(f"{LogTag.JOB} handle_service_expiration: Processing batch {offset}-{offset + len(batch)}")
        total_processed += len(batch)

        for service in batch:
            if getattr(service, "is_test", False) is True:
                continue
            services_checked += 1
            if service.warning_time is None:
                service.warning_time = 0
            if service.warning is None:
                service.warning = 0
            if (
                service.expiration_time
                and current_time < service.expiration_time <= expiring_time
                and not service.expire_notified
            ):
                time_diff_seconds = service.expiration_time - current_time
                days = time_diff_seconds // 86400
                hours = (time_diff_seconds % 86400) // 3600
                minutes = (time_diff_seconds % 3600) // 60

                time_parts = []
                if days > 0:
                    time_parts.append(f"{days} روز")
                if hours > 0:
                    time_parts.append(f"{hours} ساعت")
                if minutes > 0 or len(time_parts) == 0:
                    time_parts.append(f"{minutes} دقیقه")
                time_text = " و ".join(time_parts)

                message = (
                    f"<b>#اطلاع_رسانی</b>\n\n"
                    f"<b>#⃣ کد سرویس(در ربات): {service.code}</b>\n"
                    f"<b>🔷 اسم کانفیگ: {service.username}</b>\n"
                    f"<b>⌛️ سرویس شما تا {time_text} دیگر منقضی می‌شود.</b>\n"
                    f"<b>👈🏻 شما می‌توانید سرویس خود را در بخش (سرویس های من) تمدید کنید.</b>\n\n"
                    f"<b>#notification_{service.code}</b>"
                )
                try:
                    await Kenzo.send_message(service.id, message, parse_mode="html")
                    expiry_notifications += 1
                except errors.FloodWaitError as e:
                    await asyncio.sleep(e.seconds)
                except errors.InputUserDeactivatedError:
                    await set_user_status(service.id, "DeleteAccount")
                except errors.UserIsBlockedError:
                    await set_user_status(service.id, "BlockedBot")
                except Exception as e:
                    logger.error(f"expire notify failed for {service.id}: {e}")
                finally:
                    await service_crud.update_service(service.code, expire_notified=True)
                    await send_log_message(LogType.OTHER, message=message, parse_mode="html")

            if service.expiration_time <= current_time and service.warning == 0:
                await service_crud.update_service(service.code, warning=1, warning_time=current_time)
                days_remaining = 3
                try:
                    await Kenzo.send_message(
                        service.id,
                        f"<b>#اطلاع_رسانی</b>\n\n"
                        f"<b>#⃣ کد سرویس(در ربات): {service.code}</b>\n"
                        f"<b>🔷 اسم کانفیگ: {service.username}</b>\n"
                        f"<b>📅 سرویس شما به دلیل انقضا غیرفعال شده است.</b>\n"
                        f"<b>👈🏻 شما می‌توانید سرویس خود را در بخش (سرویس های من) تمدید کنید.</b>\n"
                        f"<b>⚠️ نکته: اگر در {days_remaining} روز آینده تمدید نکنید، سرویس شما حذف خواهد شد.</b>\n\n"
                        f"<b>#notification_{service.code}</b>",
                        parse_mode="html",
                    )
                    warning_notifications += 1

                except errors.FloodWaitError as e:
                    await asyncio.sleep(e.seconds)
                except errors.InputUserDeactivatedError:
                    await set_user_status(service.id, "DeleteAccount")
                except errors.UserIsBlockedError:
                    await set_user_status(service.id, "BlockedBot")
                except Exception as e:
                    logger.error(f"low volume warn failed for {service.id}: {e}")
                finally:
                    log_text = (
                        f"<b>#اطلاع_رسانی</b>\n\n"
                        f"<b>#⃣ کد سرویس(در ربات): {service.code}</b>\n"
                        f"<b>🔷 اسم کانفیگ: {service.username}</b>\n"
                        f"<b>📅 سرویس شما به دلیل انقضا غیرفعال شده است.</b>\n"
                        f"<b>👈🏻 شما می‌توانید سرویس خود را در بخش (سرویس های من) تمدید کنید.</b>\n"
                        f"<b>⚠️ نکته: اگر در {days_remaining} روز آینده تمدید نکنید، سرویس شما حذف خواهد شد.</b>\n\n"
                        f"<b>#notification_{service.code}</b>"
                    )
                    await send_log_message(LogType.OTHER, message=log_text, parse_mode="html")

        offset += batch_size

    elapsed = time.time() - start_time
    total_deletions = cleanup_deletions
    logger.info(
        f"{LogTag.JOB} handle_service_expiration | duration={elapsed:.2f}s, "
        f"total={total_processed}, checked={services_checked}, "
        f"expiry_notify={expiry_notifications}, warn_notify={warning_notifications}, deleted={total_deletions}"
    )
