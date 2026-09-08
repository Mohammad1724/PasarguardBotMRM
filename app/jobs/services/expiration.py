import asyncio
import time
from datetime import datetime

from pasarguard import PasarguardAPI
from telethon import errors

from app import Kenzo
from app.db.crud.panels import PanelsManager
from app.db.crud.services import ServiceCRUD
from app.db.crud.user import set_user_status
from app.logger import LogTag, LogType, get_logger
from app.services.billing.renewal import require_panel_userid
from app.services.panels.settings import panel_webhook_notifications_enabled
from app.telegram.shared.utils.logging import send_log_message
from app.utils.formatting.dates import timestamp_to_persian_expiry
from app.utils.formatting.traffic import format_size

logger = get_logger(__name__)


async def cleanup_expired_test_services():
    from app.services.customer_experience.lifecycle import cleanup_trials

    return await cleanup_trials()


async def cleanup_expired_paid_services(panel_codes: list[int], current_time: int) -> int:
    """
    Delete paid services expired 3+ days ago from panel and DB (all panels).
    Runs regardless of webhook so expired users are always cleaned.
    """
    if not panel_codes:
        return 0
    service_crud = ServiceCRUD()
    batch_size = 500
    offset = 0
    deletions = 0
    while True:
        batch = await service_crud.get_services_expired_grace_period_batch(
            panel_codes, current_time, batch_size, offset
        )
        if not batch:
            break
        for service in batch:
            user_info = None
            panel_info = None
            if service.in_panel and service.panel_userid:
                try:
                    panel = await PanelsManager().get_panel_by_code(service.in_panel)
                    if panel:
                        panel_info = panel
                        api = PasarguardAPI(panel.base_url)
                        try:
                            user_info = await api.get_user_by_id(
                                user_id=require_panel_userid(service), token=panel.cookie
                            )
                        except Exception as e:
                            logger.warning(f"Could not get user info for {service.username} from panel: {e}")
                        await api.remove_user_by_id(user_id=require_panel_userid(service), token=panel.cookie)
                except Exception as e:
                    logger.error(f"Failed to delete service {service.code} from Marzban panel: {e}")
            try:
                await service_crud.delete_service(service.code)
            except Exception as e:
                logger.error(f"Failed to delete service {service.code} from DB: {e}")
                continue
            deletions += 1
            try:
                await Kenzo.send_message(
                    service.id,
                    (
                        f"<b>#حذف_سرویس</b>\n\n"
                        f"<b>🚫 سرویس شما حذف شد</b>\n\n"
                        f"📋 <b>کد سرویس:</b> <code>{service.code}</code>\n"
                        f"👤 <b>نام کانفیگ:</b> <code>{service.username}</code>\n\n"
                        f"⚠️ <b>توضیحات:</b>\n"
                        f"سرویس شما به دلیل انقضای زمان و عدم تمدید پس از 3 روز از ربات حذف شد.\n\n"
                        f"💡 برای خرید سرویس جدید، از منوی اصلی ربات استفاده کنید.\n\n"
                        f"<b>#service_deleted_{service.code}</b>"
                    ),
                    parse_mode="html",
                )
            except errors.FloodWaitError as e:
                await asyncio.sleep(e.seconds)
            except errors.InputUserDeactivatedError:
                await set_user_status(service.id, "DeleteAccount")
            except errors.UserIsBlockedError:
                await set_user_status(service.id, "BlockedBot")
            except Exception as e:
                logger.error(f"Failed to send deletion message to user {service.id}: {e}")
            log_parts = [
                "✅ یک کانفیگ به دلیل انقضا پس از 3 روز حذف شد.\n\n",
                f"◾️ کد سرویس: <code>{service.code}</code>\n",
                f"◾️ شناسه کاربر: <code>{service.id}</code>\n",
                f"◾️ اسم کانفیگ: <code>{service.username}</code>\n",
            ]
            if panel_info:
                log_parts.append(f"◾️ پنل: {panel_info.name}\n")
            if service.expiration_time:
                log_parts.append(f"◾️ زمان انقضا: {timestamp_to_persian_expiry(service.expiration_time)}\n")
            if service.createtime:
                log_parts.append(f"◾️ زمان ایجاد: {timestamp_to_persian_expiry(service.createtime)}\n")
            log_parts.append(f"◾️ زمان حذف: {timestamp_to_persian_expiry(current_time)}\n")
            if user_info:
                used_traffic = getattr(user_info, "used_traffic", 0) or 0
                log_parts.append(f"◾️ حجم مصرفی: {format_size(used_traffic, decimal_places=2)}\n")
                if hasattr(user_info, "data_limit") and user_info.data_limit:
                    data_limit = user_info.data_limit
                    log_parts.append(f"◾️ حجم کل: {format_size(data_limit, decimal_places=2)}\n")
                    remaining = data_limit - used_traffic
                    if remaining > 0:
                        log_parts.append(f"◾️ حجم باقی‌مانده: {format_size(remaining, decimal_places=2)}\n")
                    else:
                        log_parts.append("◾️ حجم باقی‌مانده: 0 (تمام شده)\n")
                if hasattr(user_info, "expire") and user_info.expire:
                    log_parts.append(f"◾️ تاریخ انقضای مرزبان: {timestamp_to_persian_expiry(user_info.expire)}\n")
                if hasattr(user_info, "status") and user_info.status:
                    status_text = "فعال" if user_info.status == "active" else "غیرفعال"
                    log_parts.append(f"◾️ وضعیت مرزبان: {status_text}\n")
            elif service.package_size:
                log_parts.append(f"◾️ حجم پکیج: {format_size(service.package_size, decimal_places=2)}\n")
                log_parts.append("⚠️ اطلاعات مرزبان در دسترس نبود\n")
            await send_log_message(LogType.OTHER, message="".join(log_parts), parse_mode="html")
        offset += batch_size
    if deletions:
        logger.info(f"{LogTag.JOB} cleanup_expired_paid_services | deleted={deletions}")
    return deletions


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
