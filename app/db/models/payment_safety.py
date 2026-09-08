"""Durable financial idempotency and gift application records."""

from sqlalchemy import BigInteger, Column, String

from app.db.base import Base


class ReferralReward(Base):
    __tablename__ = "referral_rewards"
    source_key = Column(String(80), primary_key=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    referrer_id = Column(BigInteger, nullable=False)
    amount = Column(BigInteger, nullable=False)
    first_user_id = Column(BigInteger, nullable=True, unique=True)
    created_at = Column(BigInteger, nullable=False)


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"
    digest = Column(String(64), primary_key=True)
    completed_at = Column(BigInteger, nullable=False, index=True)
