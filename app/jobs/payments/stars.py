"""Reconcile successful Stars payments missed during restart or a database outage.

One bounded page per run, with a durable cursor; completed scans restart at newest.
The order state and unique receipt reference make replay safe.
"""

from telethon import functions, types

from app import Kenzo
from app.db.crud.cryptopayments import CryptoPaymentsCRUD
from app.db.crud.settings import SettingsManager
from app.logger import get_logger

logger = get_logger(__name__)


async def reconcile_stars():
    from app.telegram.user.balance.stars import _parse_stars_payload, confirm_stars_payment

    settings = await SettingsManager().get_settings()
    if not settings:
        return
    crud = CryptoPaymentsCRUD()
    if not await crud.has_unpaid_stars():
        return
    offset = settings.stars_history_offset or ""
    result = await Kenzo(
        functions.payments.GetStarsTransactionsRequest(
            peer=types.InputPeerSelf(), offset=offset, limit=100, inbound=True
        )
    )
    for tx in result.history:
        if tx.refund or tx.pending or tx.failed or not tx.bot_payload:
            continue
        order_id = _parse_stars_payload(tx.bot_payload)
        peer = getattr(tx.peer, "peer", None)
        if (
            order_id is None
            or not isinstance(peer, types.PeerUser)
            or not isinstance(tx.amount, types.StarsAmount)
            or tx.amount.nanos
        ):
            continue
        row = await crud.get_by_order_id(order_id)
        if not row or row.arz != "STARS" or row.status == "Paid":
            continue
        if row.user_id != peer.user_id or row.amount != str(tx.amount.amount):
            logger.error("Stars history mismatch for order %s; manual review required", order_id)
            continue
        await confirm_stars_payment(order_id, tx.amount.amount, payer_id=peer.user_id, charge_id=tx.id)
        refreshed = await crud.get_by_order_id(order_id)
        if not refreshed or refreshed.status != "Paid":
            raise RuntimeError("Stars reconciliation credit failed; retaining history cursor")
    await SettingsManager().update_setting_by_name("stars_history_offset", result.next_offset or "")
