"""Plan term edits invalidate future consent, including an A -> B -> A price change."""

import time

from sqlalchemy import update

from app.db.models.auto_renew import AutoRenewPolicy


async def invalidate_renewals_for_plan(session, plan_id):
    result = await session.execute(
        update(AutoRenewPolicy)
        .where(
            AutoRenewPolicy.plan_id == plan_id,
            AutoRenewPolicy.state == "enabled",
        )
        .values(
            state="paused", reason="plan_changed", revision=AutoRenewPolicy.revision + 1, updated_at=int(time.time())
        )
    )
    return result.rowcount
