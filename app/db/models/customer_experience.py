"""Persistent customer journeys, private support tickets and conversion intents."""

from sqlalchemy import JSON, BigInteger, Boolean, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

PK = BigInteger().with_variant(Integer, "sqlite")


class SupportTicket(Base):
    __tablename__ = "cx_tickets"
    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    service_code: Mapped[int | None] = mapped_column(BigInteger)
    topic: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24), default="open")
    assigned_to: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[int] = mapped_column(BigInteger)
    updated_at: Mapped[int] = mapped_column(BigInteger)
    __table_args__ = (Index("ix_cx_ticket_queue", "status", "updated_at"),)


class TicketMessage(Base):
    __tablename__ = "cx_ticket_messages"
    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(BigInteger, index=True)
    author_id: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(16))  # customer / staff / system
    text: Mapped[str] = mapped_column(Text, default="")
    file_id: Mapped[str | None] = mapped_column(Text)  # Telegram reusable media reference; no download
    source_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    source_message_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[int] = mapped_column(BigInteger)
    __table_args__ = (
        UniqueConstraint("source_chat_id", "source_message_id", name="uq_cx_ticket_source"),
        Index("ix_cx_message_author_time", "author_id", "created_at"),
    )


class TrialJourney(Base):
    __tablename__ = "cx_trial_journeys"
    service_code: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    panel_code: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[int] = mapped_column(BigInteger)
    expires_at: Mapped[int] = mapped_column(BigInteger)
    ended_at: Mapped[int | None] = mapped_column(BigInteger)
    retain_until: Mapped[int] = mapped_column(BigInteger)
    opted_in: Mapped[bool] = mapped_column(Boolean, default=False)
    experiment_group: Mapped[str] = mapped_column(String(16))
    followup_status: Mapped[str] = mapped_column(String(24), default="pending")
    consented_at: Mapped[int | None] = mapped_column(BigInteger)
    attempted_at: Mapped[int | None] = mapped_column(BigInteger)
    sent_at: Mapped[int | None] = mapped_column(BigInteger)
    clicked_at: Mapped[int | None] = mapped_column(BigInteger)
    first_purchase_at: Mapped[int | None] = mapped_column(BigInteger)
    __table_args__ = (Index("ix_cx_journey_due", "followup_status", "ended_at"),)


class CustomerEvent(Base):
    __tablename__ = "cx_events"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    service_code: Mapped[int] = mapped_column(BigInteger)
    name: Mapped[str] = mapped_column(String(40), index=True)
    created_at: Mapped[int] = mapped_column(BigInteger, index=True)


class TrialConversion(Base):
    __tablename__ = "cx_trial_conversions"
    token: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    service_code: Mapped[int] = mapped_column(BigInteger, index=True)
    active_service_code: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    panel_code: Mapped[int] = mapped_column(BigInteger)
    panel_userid: Mapped[int] = mapped_column(BigInteger)
    username: Mapped[str] = mapped_column(String(64))
    plan_id: Mapped[int] = mapped_column(Integer)
    price: Mapped[int] = mapped_column(BigInteger)
    plan_snapshot: Mapped[dict] = mapped_column(JSON)
    old_values: Mapped[dict | None] = mapped_column(JSON)
    target_values: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(24), default="quoted", index=True)
    attempted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[int] = mapped_column(BigInteger)
    expires_at: Mapped[int] = mapped_column(BigInteger)
    completed_at: Mapped[int | None] = mapped_column(BigInteger)


class CustomerExperienceAudit(Base):
    __tablename__ = "cx_audit"
    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    actor_id: Mapped[int] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(String(40))
    detail: Mapped[str] = mapped_column(Text)
    created_at: Mapped[int] = mapped_column(BigInteger)
