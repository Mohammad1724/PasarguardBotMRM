"""Explicit recurring consent, immutable wallet renewal attempts and notice outbox."""

from sqlalchemy import JSON, BigInteger, Boolean, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AutoRenewPolicy(Base):
    __tablename__ = "auto_renew_policies"
    service_code: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    panel_code: Mapped[int] = mapped_column(BigInteger)
    panel_userid: Mapped[int] = mapped_column(BigInteger)
    username: Mapped[str] = mapped_column(String(64))
    panel_url: Mapped[str] = mapped_column(String(255))
    plan_id: Mapped[int] = mapped_column(Integer)
    plan_snapshot: Mapped[dict] = mapped_column(JSON)
    expected_values: Mapped[dict] = mapped_column(JSON)
    per_charge_cap: Mapped[int] = mapped_column(BigInteger)
    monthly_cap: Mapped[int] = mapped_column(BigInteger)
    revision: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(16))  # enabled, off, paused
    reason: Mapped[str] = mapped_column(String(40), default="")
    next_check_at: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[int] = mapped_column(BigInteger)
    updated_at: Mapped[int] = mapped_column(BigInteger)
    __table_args__ = (Index("ix_auto_renew_due", "state", "next_check_at"),)


class AutoRenewConsent(Base):
    __tablename__ = "auto_renew_consents"
    token: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    service_code: Mapped[int] = mapped_column(BigInteger, index=True)
    revision: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(JSON)
    monthly_cap: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[int] = mapped_column(BigInteger)
    expires_at: Mapped[int] = mapped_column(BigInteger)
    confirmed_at: Mapped[int | None] = mapped_column(BigInteger)


class AutoRenewAttempt(Base):
    __tablename__ = "auto_renew_attempts"
    token: Mapped[str] = mapped_column(String(32), primary_key=True)
    service_code: Mapped[int] = mapped_column(BigInteger, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    active_service_code: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    policy_revision: Mapped[int] = mapped_column(Integer)
    cycle_expire: Mapped[int] = mapped_column(BigInteger)
    identity: Mapped[dict] = mapped_column(JSON)
    price: Mapped[int] = mapped_column(BigInteger)
    month: Mapped[str] = mapped_column(String(7))
    old_values: Mapped[dict] = mapped_column(JSON)
    target_values: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16))  # applying, review, applied, refunded
    attempted: Mapped[bool] = mapped_column(Boolean, default=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[int] = mapped_column(BigInteger)
    error: Mapped[str] = mapped_column(String(40), default="")
    created_at: Mapped[int] = mapped_column(BigInteger)
    completed_at: Mapped[int | None] = mapped_column(BigInteger)
    __table_args__ = (
        UniqueConstraint("service_code", "cycle_expire", name="uq_auto_renew_cycle"),
        Index("ix_auto_renew_retry", "status", "next_retry_at"),
        Index("ix_auto_renew_budget", "service_code", "month", "status"),
    )


class AutoRenewNotice(Base):
    __tablename__ = "auto_renew_notices"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    service_code: Mapped[int] = mapped_column(BigInteger)
    revision: Mapped[int] = mapped_column(Integer)
    cycle_expire: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(24))
    payload: Mapped[dict] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(16), default="pending")
    created_at: Mapped[int] = mapped_column(BigInteger)
    sent_at: Mapped[int | None] = mapped_column(BigInteger)
    __table_args__ = (Index("ix_auto_renew_notice_queue", "state", "created_at"),)
