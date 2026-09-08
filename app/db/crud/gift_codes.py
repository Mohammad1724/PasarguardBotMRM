"""CRUD operations for gift codes and their redemptions."""

from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.future import select

from app.db.base import AsyncSessionLocal as Session
from app.db.models.gift_codes import GiftCode, GiftCodeUse
from app.logger import get_logger

log = get_logger(__name__)

REDEEM_OK = "ok"
REDEEM_NOT_FOUND = "not_found"
REDEEM_INACTIVE = "inactive"
REDEEM_EXPIRED = "expired"
REDEEM_EXHAUSTED = "exhausted"
REDEEM_USER_LIMIT = "user_limit"


class GiftCodeCRUD:
    async def create(
        self,
        code: str,
        type: str,
        value: int,
        max_uses: int = 1,
        per_user_limit: int = 1,
        expires_at: int | None = None,
        note: str | None = None,
        created_at: int = 0,
    ) -> GiftCode | None:
        try:
            async with Session() as session:
                gift = GiftCode(
                    code=code.strip().upper(),
                    type=type,
                    value=int(value),
                    max_uses=int(max_uses),
                    per_user_limit=int(per_user_limit),
                    expires_at=expires_at,
                    note=note,
                    created_at=created_at,
                )
                session.add(gift)
                await session.commit()
                await session.refresh(gift)
                return gift
        except SQLAlchemyError as e:
            log.error("GiftCode create error: %s", e)
            return None

    async def get_by_code(self, code: str) -> GiftCode | None:
        try:
            async with Session() as session:
                result = await session.execute(select(GiftCode).filter_by(code=code.strip().upper()))
                return result.scalar_one_or_none()
        except SQLAlchemyError as e:
            log.error("GiftCode get_by_code error: %s", e)
            return None

    async def get(self, gift_id: int) -> GiftCode | None:
        try:
            async with Session() as session:
                result = await session.execute(select(GiftCode).filter_by(id=gift_id))
                return result.scalar_one_or_none()
        except SQLAlchemyError as e:
            log.error("GiftCode get error: %s", e)
            return None

    async def get_all(self) -> list[GiftCode]:
        try:
            async with Session() as session:
                result = await session.execute(select(GiftCode).order_by(GiftCode.id.desc()))
                return list(result.scalars().all())
        except SQLAlchemyError as e:
            log.error("GiftCode get_all error: %s", e)
            return []

    async def update(self, gift_id: int, **kwargs) -> GiftCode | None:
        try:
            async with Session() as session:
                result = await session.execute(select(GiftCode).filter_by(id=gift_id))
                gift = result.scalar_one_or_none()
                if not gift:
                    return None
                for key, value in kwargs.items():
                    if hasattr(gift, key):
                        setattr(gift, key, value)
                await session.commit()
                await session.refresh(gift)
                return gift
        except SQLAlchemyError as e:
            log.error("GiftCode update error: %s", e)
            return None

    async def delete(self, gift_id: int) -> bool:
        try:
            async with Session() as session, session.begin():
                result = await session.execute(select(GiftCode).filter_by(id=gift_id))
                gift = result.scalar_one_or_none()
                if not gift:
                    return False
                await session.delete(gift)
                uses = await session.execute(select(GiftCodeUse).filter_by(code_id=gift_id))
                for use in uses.scalars().all():
                    await session.delete(use)
                return True
        except SQLAlchemyError as e:
            log.error("GiftCode delete error: %s", e)
            return False

    async def count_user_uses(self, code_id: int, user_id: int) -> int:
        try:
            async with Session() as session:
                result = await session.execute(
                    select(func.count()).select_from(GiftCodeUse).where(
                        GiftCodeUse.code_id == code_id,
                        GiftCodeUse.user_id == user_id,
                    )
                )
                return result.scalar() or 0
        except SQLAlchemyError:
            return 0

    async def list_uses(self, code_id: int, limit: int = 20) -> list[GiftCodeUse]:
        try:
            async with Session() as session:
                result = await session.execute(
                    select(GiftCodeUse).where(GiftCodeUse.code_id == code_id).order_by(GiftCodeUse.id.desc()).limit(limit)
                )
                return list(result.scalars().all())
        except SQLAlchemyError as e:
            log.error("GiftCode list_uses error: %s", e)
            return []

    async def sum_user_gift_balance(self, user_id: int) -> int:
        """Sum balance-type gift values redeemed by a user (for profile stats)."""
        try:
            async with Session() as session:
                result = await session.execute(
                    select(func.coalesce(func.sum(GiftCodeUse.value), 0)).where(
                        GiftCodeUse.user_id == user_id,
                        GiftCodeUse.service_code.is_(None),
                    )
                )
                return int(result.scalar() or 0)
        except SQLAlchemyError:
            return 0

    async def redeem(self, code: str, user_id: int, *, service_code: str | None = None) -> tuple[str, GiftCode | None]:
        """Atomically validate and record a redemption.

        Returns (status, gift). Status is one of REDEEM_* constants. The caller
        applies the actual effect (wallet credit / panel modify) only on REDEEM_OK.
        """
        try:
            async with Session() as session, session.begin():
                stmt = select(GiftCode).where(GiftCode.code == code.strip().upper())
                dialect = session.bind.dialect if session.bind is not None else None
                if dialect and dialect.name != "sqlite":
                    stmt = stmt.with_for_update()
                gift = (await session.execute(stmt)).scalar_one_or_none()
                if not gift:
                    return REDEEM_NOT_FOUND, None
                if not gift.is_active:
                    return REDEEM_INACTIVE, gift
                now = int(__import__("time").time())
                if gift.expires_at and gift.expires_at < now:
                    return REDEEM_EXPIRED, gift
                if gift.times_used >= gift.max_uses:
                    return REDEEM_EXHAUSTED, gift
                user_uses_stmt = select(func.count()).select_from(GiftCodeUse).where(
                    GiftCodeUse.code_id == gift.id,
                    GiftCodeUse.user_id == user_id,
                )
                user_uses = (await session.execute(user_uses_stmt)).scalar() or 0
                if user_uses >= max(int(gift.per_user_limit or 1), 1):
                    return REDEEM_USER_LIMIT, gift

                gift.times_used = int(gift.times_used or 0) + 1
                session.add(
                    GiftCodeUse(
                        code_id=gift.id,
                        code=gift.code,
                        user_id=user_id,
                        service_code=service_code,
                        value=int(gift.value),
                        used_at=now,
                    )
                )
                return REDEEM_OK, gift
        except SQLAlchemyError as e:
            log.error("GiftCode redeem error: %s", e)
            return REDEEM_NOT_FOUND, None
