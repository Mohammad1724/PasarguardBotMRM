import secrets
import time

from sqlalchemy import Float, cast, func, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.future import select

from app.db.base import AsyncSessionLocal as Session
from app.db.crud.settings import SettingsManager
from app.db.models.cryptopayments import CryptoPayments
from app.db.models.user import User


class CryptoPaymentsCRUD:
    """CRUD operations for CryptoPayments"""

    async def get_pending_by_arz(self, arz: str):
        try:
            async with Session() as session:
                stmt = select(CryptoPayments).where(
                    CryptoPayments.status.in_(
                        ("Pending", "Reconcile", "Expired") if arz.upper() == "ZARINPAL" else ("Pending",)
                    ),
                    CryptoPayments.arz == arz.upper(),
                )
                if arz.upper() == "ZARINPAL":
                    stmt = (
                        stmt.where(CryptoPayments.next_check_at <= int(time.time()))
                        .order_by(CryptoPayments.next_check_at)
                        .limit(25)
                    )
                result = await session.execute(stmt)
                return result.scalars().all()
        except SQLAlchemyError:
            return []

    async def get_by_order_id(self, order_id: int):
        try:
            async with Session() as session:
                result = await session.execute(select(CryptoPayments).filter(CryptoPayments.order_id == order_id))
                return result.scalar_one_or_none()
        except SQLAlchemyError:
            return None

    async def update_payment_status(self, order_id: int, status: str, paytime: int | None = None):
        try:
            async with Session() as session:
                result = await session.execute(select(CryptoPayments).filter(CryptoPayments.order_id == order_id))
                payment = result.scalar_one_or_none()
                if payment:
                    payment.status = status
                    if paytime:
                        payment.paytime = paytime
                    await session.commit()
                    await session.refresh(payment)
                    return payment
                return None
        except SQLAlchemyError:
            return None

    async def expire_payment(self, order_id: int):
        async with Session() as session, session.begin():
            result = await session.execute(
                update(CryptoPayments)
                .where(CryptoPayments.order_id == order_id, CryptoPayments.status == "Pending")
                .values(status="Expired")
            )
            return bool(result.rowcount)

    async def approve_and_credit(
        self,
        order_id: int,
        total_amount: int,
        paytime: int | None = None,
        *,
        payment_ref: str | None = None,
        gateway_ref_id: str | None = None,
        user_id: int | None = None,
    ):
        settings = await SettingsManager().get_settings()
        if settings is None:
            raise RuntimeError("Payment settings unavailable; retry after recovery")
        try:
            async with Session() as session, session.begin():
                payment_stmt = select(CryptoPayments).where(CryptoPayments.order_id == order_id)
                dialect = session.bind.dialect if session.bind is not None else None
                if dialect and dialect.name != "sqlite":
                    payment_stmt = payment_stmt.with_for_update()
                payment = (await session.execute(payment_stmt)).scalar_one_or_none()
                if not payment:
                    return None
                allowed = ("Pending", "Processing", "Expired", "Reconcile") if payment_ref else ("Pending",)
                if payment.status not in allowed or (user_id is not None and int(payment.user_id) != user_id):
                    return None
                if total_amount <= 0:
                    return None
                if payment_ref:
                    payment.payment_ref = payment_ref
                    payment.gateway_ref_id = gateway_ref_id

                user_stmt = select(User).where(User.id == payment.user_id)
                if dialect and dialect.name != "sqlite":
                    user_stmt = user_stmt.with_for_update()
                user = (await session.execute(user_stmt)).scalar_one_or_none()
                if not user:
                    return None

                user.amount = int(user.amount or 0) + int(total_amount)
                payment.status = "Paid"
                if paytime:
                    payment.paytime = paytime
                from app.services.billing.referral_ledger import credit_referral

                await session.flush()
                await credit_referral(session, user, int(payment.amount_irt), f"crypto:{order_id}", settings)
                return payment, int(user.amount or 0)
        except SQLAlchemyError:
            return None

    async def reserve_invoice(
        self,
        user_id: int,
        arz: str,
        amount: str,
        amount_irt: int,
        *,
        merchant: str | None = None,
        sandbox: bool | None = None,
    ):
        """Persist before exposing a payable invoice. Serialize the per-user order limit."""
        if int(amount_irt) <= 0:
            raise ValueError("Invoice amount must be positive")
        for _ in range(3):
            try:
                async with Session() as session, session.begin():
                    user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
                    if not user:
                        raise ValueError("User not found")
                    count = await session.scalar(
                        select(func.count())
                        .select_from(CryptoPayments)
                        .where(
                            CryptoPayments.user_id == user_id,
                            CryptoPayments.status.in_(("Pending", "Creating", "Processing")),
                        )
                    )
                    if int(count or 0) >= 3:
                        raise ValueError("Too many pending invoices")
                    payment = CryptoPayments(
                        order_id=secrets.randbits(62) + 1,
                        user_id=user_id,
                        arz=arz.upper(),
                        amount=amount,
                        amount_irt=amount_irt,
                        createtime=int(time.time()),
                        status="Creating" if arz.upper() == "ZARINPAL" else "Pending",
                        gateway_merchant=merchant,
                        gateway_sandbox=None if sandbox is None else str(int(sandbox)),
                    )
                    session.add(payment)
                    await session.flush()
                    return payment
            except IntegrityError:
                continue
        raise RuntimeError("Could not reserve a unique invoice")

    async def has_unpaid_stars(self) -> bool:
        async with Session() as session:
            return (
                await session.scalar(
                    select(CryptoPayments.order_id)
                    .where(
                        CryptoPayments.arz == "STARS", CryptoPayments.status.in_(("Pending", "Processing", "Expired"))
                    )
                    .limit(1)
                )
            ) is not None

    async def schedule_gateway_retry(self, payment):
        age = int(time.time()) - payment.createtime
        delay = 60 if age < 1800 else (3600 if age < 7 * 86400 else 86400)
        async with Session() as session, session.begin():
            await session.execute(
                update(CryptoPayments)
                .where(CryptoPayments.order_id == payment.order_id)
                .values(next_check_at=int(time.time()) + delay)
            )

    async def set_gateway_authority(self, order_id: int, authority: str) -> None:
        async with Session() as session, session.begin():
            result = await session.execute(
                update(CryptoPayments)
                .where(CryptoPayments.order_id == order_id, CryptoPayments.status == "Creating")
                .values(amount=authority, status="Pending")
            )
            if result.rowcount != 1:
                raise RuntimeError("Gateway invoice was not reserved")

    async def accept_stars_checkout(
        self, order_id: int, user_id: int, amount: int, currency: str, query_id: int | None = None
    ) -> bool:
        if currency != "XTR" or amount <= 0:
            return False
        async with Session() as session, session.begin():
            row = await session.scalar(
                select(CryptoPayments).where(CryptoPayments.order_id == order_id).with_for_update()
            )
            if not row or row.arz != "STARS" or row.status not in ("Pending", "Processing"):
                return False
            if row.user_id != user_id or str(amount) != row.amount or row.createtime < int(time.time()) - 1800:
                return False
            if row.status == "Processing" and row.checkout_query_id != query_id:
                return False
            row.checkout_query_id = query_id
            row.status = "Processing"
            return True

    async def age_invoices(self) -> None:
        now = int(time.time())
        async with Session() as session, session.begin():
            await session.execute(
                update(CryptoPayments)
                .where(
                    CryptoPayments.arz == "STARS",
                    CryptoPayments.status.in_(("Pending", "Processing")),
                    CryptoPayments.createtime < now - 1800,
                )
                .values(status="Expired")
            )
            await session.execute(
                update(CryptoPayments)
                .where(
                    CryptoPayments.arz == "ZARINPAL",
                    CryptoPayments.status == "Pending",
                    CryptoPayments.createtime < now - 1800,
                )
                .values(status="Reconcile")
            )
            await session.execute(
                update(CryptoPayments)
                .where(CryptoPayments.status == "Creating", CryptoPayments.createtime < now - 1800)
                .values(status="Failed")
            )


async def age_online_invoices():
    await CryptoPaymentsCRUD().age_invoices()


async def add_order_crypto_payment(order_id, user_id, arz, amount, amount_irt, createtime, msg_id=None):
    try:
        arz_upper = arz.upper() if arz else arz

        async with Session() as session:
            new_record = CryptoPayments(
                order_id=order_id,
                user_id=user_id,
                arz=arz_upper,
                amount=amount,
                amount_irt=amount_irt,
                createtime=createtime,
                msg_id=msg_id,
            )
            session.add(new_record)
            await session.commit()

            return {
                "success": True,
                "message": f"Record added successfully for Order ID {order_id}.",
                "record": {
                    "order_id": order_id,
                    "user_id": user_id,
                    "arz": arz_upper,
                    "amount": amount,
                    "amount_irt": amount_irt,
                    "createtime": createtime,
                    "msg_id": msg_id,
                },
            }
    except SQLAlchemyError as e:
        return {
            "success": False,
            "message": f"Error adding record: {e!s}",
        }


async def count_pending_orders(user_id: int) -> int:
    try:
        async with Session() as session:
            result = await session.execute(
                select(func.count())
                .select_from(CryptoPayments)
                .where(
                    CryptoPayments.user_id == user_id,
                    CryptoPayments.status == "Pending",
                )
            )
            return result.scalar() or 0
    except SQLAlchemyError:
        return 0


async def get_user_crypto_stats(user_id: int) -> dict:
    try:
        async with Session() as session:
            count_stmt = (
                select(func.count())
                .select_from(CryptoPayments)
                .where(CryptoPayments.user_id == user_id, CryptoPayments.status == "Paid")
            )
            count_result = await session.execute(count_stmt)
            count = count_result.scalar() or 0

            sum_stmt = select(func.sum(CryptoPayments.amount_irt)).where(
                CryptoPayments.user_id == user_id, CryptoPayments.status == "Paid"
            )
            sum_result = await session.execute(sum_stmt)
            total_amount = sum_result.scalar() or 0

            return {"count": count, "total_amount": total_amount}
    except SQLAlchemyError:
        return {"count": 0, "total_amount": 0}


async def get_user_all_crypto_transactions(user_id: int):
    try:
        async with Session() as session:
            result = await session.execute(
                select(CryptoPayments)
                .where(CryptoPayments.user_id == user_id)
                .order_by(CryptoPayments.createtime.desc())
            )
            return result.scalars().all()
    except SQLAlchemyError:
        return []


async def get_user_crypto_transaction_by_id(user_id: int, order_id: int):
    try:
        async with Session() as session:
            result = await session.execute(
                select(CryptoPayments).where(CryptoPayments.user_id == user_id, CryptoPayments.order_id == order_id)
            )
            return result.scalar_one_or_none()
    except SQLAlchemyError:
        return None


async def get_global_crypto_stats() -> dict:
    """Global stats: count and total amount (IRT) of Paid crypto payments."""
    try:
        async with Session() as session:
            stmt = select(
                func.count().label("count"),
                func.sum(CryptoPayments.amount_irt).label("total_amount"),
            ).where(CryptoPayments.status == "Paid")
            result = await session.execute(stmt)
            row = result.one()
            return {"count": int(row.count or 0), "total_amount": int(row.total_amount or 0)}
    except SQLAlchemyError:
        return {"count": 0, "total_amount": 0}


async def get_crypto_period_stats(start_ts: int, end_ts: int | None = None) -> dict:
    """Paid crypto payments in [start_ts, end_ts) by paytime."""
    try:
        async with Session() as session:
            cond = (CryptoPayments.status == "Paid") & (CryptoPayments.paytime >= start_ts)
            if end_ts is not None:
                cond = cond & (CryptoPayments.paytime < end_ts)
            stmt = select(
                func.count().label("count"),
                func.sum(CryptoPayments.amount_irt).label("total_amount"),
            ).where(cond)
            row = (await session.execute(stmt)).one()
            return {"count": int(row.count or 0), "total_amount": int(row.total_amount or 0)}
    except SQLAlchemyError:
        return {"count": 0, "total_amount": 0}


def _paid_crypto_cond(start_ts: int, end_ts: int | None = None):
    cond = (CryptoPayments.status == "Paid") & (CryptoPayments.paytime >= start_ts)
    if end_ts is not None:
        cond = cond & (CryptoPayments.paytime < end_ts)
    return cond


async def get_crypto_period_breakdown(start_ts: int, end_ts: int | None = None) -> dict:
    """Paid crypto per currency: count, crypto volume, IRT total."""
    try:
        async with Session() as session:
            cond = _paid_crypto_cond(start_ts, end_ts)
            stmt = (
                select(
                    CryptoPayments.arz,
                    func.count().label("count"),
                    func.sum(cast(CryptoPayments.amount, Float)).label("crypto_sum"),
                    func.sum(CryptoPayments.amount_irt).label("amount_irt"),
                )
                .where(cond)
                .group_by(CryptoPayments.arz)
                .order_by(func.sum(CryptoPayments.amount_irt).desc())
            )
            rows = (await session.execute(stmt)).all()
            currencies = []
            total_count = 0
            total_irt = 0
            for row in rows:
                arz = (row.arz or "UNKNOWN").upper()
                count = int(row.count or 0)
                crypto_sum = float(row.crypto_sum or 0)
                amount_irt = int(row.amount_irt or 0)
                total_count += count
                total_irt += amount_irt
                currencies.append(
                    {
                        "arz": arz,
                        "count": count,
                        "crypto_sum": crypto_sum,
                        "amount_irt": amount_irt,
                    }
                )
            return {"count": total_count, "total_amount": total_irt, "currencies": currencies}
    except SQLAlchemyError:
        return {"count": 0, "total_amount": 0, "currencies": []}


async def get_global_crypto_breakdown() -> dict:
    """All-time paid crypto breakdown by currency."""
    try:
        async with Session() as session:
            stmt = (
                select(
                    CryptoPayments.arz,
                    func.count().label("count"),
                    func.sum(cast(CryptoPayments.amount, Float)).label("crypto_sum"),
                    func.sum(CryptoPayments.amount_irt).label("amount_irt"),
                )
                .where(CryptoPayments.status == "Paid")
                .group_by(CryptoPayments.arz)
                .order_by(func.sum(CryptoPayments.amount_irt).desc())
            )
            rows = (await session.execute(stmt)).all()
            currencies = []
            total_count = 0
            total_irt = 0
            for row in rows:
                arz = (row.arz or "UNKNOWN").upper()
                count = int(row.count or 0)
                crypto_sum = float(row.crypto_sum or 0)
                amount_irt = int(row.amount_irt or 0)
                total_count += count
                total_irt += amount_irt
                currencies.append(
                    {
                        "arz": arz,
                        "count": count,
                        "crypto_sum": crypto_sum,
                        "amount_irt": amount_irt,
                    }
                )
            return {"count": total_count, "total_amount": total_irt, "currencies": currencies}
    except SQLAlchemyError:
        return {"count": 0, "total_amount": 0, "currencies": []}
