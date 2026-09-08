"""Central job registration — single source of truth for all scheduled jobs."""

from datetime import datetime

from app.jobs.scheduler import scheduler
from app.logger import LogTag, get_logger

logger = get_logger(__name__)

_JOBS_REGISTERED = False


def register_all_jobs() -> None:
    global _JOBS_REGISTERED
    if _JOBS_REGISTERED:
        return

    from app.db.crud.cryptopayments import age_online_invoices
    from app.jobs.auto_renew import auto_renew_job
    from app.jobs.backup import bootstrap_backup_job, register_backup_job_placeholder
    from app.jobs.customer_experience import followup_job
    from app.jobs.panels.cleanup import get_cookies
    from app.jobs.payments.stars import reconcile_stars
    from app.jobs.payments.transactions import (
        auto_confirm_job,
        ton_checking,
        trx_checking,
        usdt_checking,
        zarinpal_checking,
    )
    from app.jobs.prices import get_prices_and_update
    from app.jobs.reseller.billing import run_reseller_billing
    from app.jobs.services.expiration import handle_service_expiration
    from app.jobs.services.low_volume import check_low_volume
    from app.jobs.webhooks import prune_webhook_deliveries

    now = datetime.now()
    job_defs = [
        (auto_renew_job, "interval", {"seconds": 60}, "wallet_auto_renew"),
        (followup_job, "interval", {"minutes": 5}, "cx_trial_followups"),
        (prune_webhook_deliveries, "interval", {"hours": 24}, "prune_webhook_deliveries"),
        (reconcile_stars, "interval", {"minutes": 5}, "reconcile_stars"),
        (age_online_invoices, "interval", {"seconds": 60}, "age_online_invoices"),
        (handle_service_expiration, "interval", {"seconds": 60}, "service_expiration_handler"),
        (check_low_volume, "interval", {"seconds": 60}, "check_low_volume"),
        (get_cookies, "interval", {"hours": 5}, "get_cookies"),
        (get_prices_and_update, "interval", {"minutes": 10}, "get_prices_and_update"),
        (auto_confirm_job, "interval", {"seconds": 60}, "auto_confirm_job"),
        (trx_checking, "interval", {"seconds": 60}, "trx_checking"),
        (usdt_checking, "interval", {"seconds": 60}, "usdt_checking"),
        (ton_checking, "interval", {"seconds": 60}, "ton_checking"),
        (zarinpal_checking, "interval", {"seconds": 60}, "zarinpal_checking"),
        (run_reseller_billing, "interval", {"seconds": 60}, "reseller_billing"),
    ]

    for func, trigger, trigger_args, job_id in job_defs:
        scheduler.add_job(
            func,
            trigger,
            id=job_id,
            replace_existing=True,
            next_run_time=now,
            **trigger_args,
        )

    register_backup_job_placeholder()
    scheduler.add_job(
        bootstrap_backup_job,
        "date",
        run_date=now,
        id="backup_bootstrap",
        replace_existing=True,
    )

    _JOBS_REGISTERED = True
    logger.debug("%s Registered %s scheduled jobs (+ backup)", LogTag.SCHEDULER, len(job_defs))
