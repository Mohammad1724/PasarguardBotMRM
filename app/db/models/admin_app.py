"""Administrative grants, published layout and durable review/audit records."""

from sqlalchemy import JSON, BigInteger, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

PK = BigInteger().with_variant(Integer, "sqlite")


class AdminGrant(Base):
    __tablename__ = "admin_app_grants"
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(60))
    revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    permissions: Mapped[list] = mapped_column(JSON)
    updated_at: Mapped[int] = mapped_column(BigInteger)


class AdminState(Base):
    __tablename__ = "admin_app_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    layout: Mapped[list] = mapped_column(JSON, default=list)


class AdminChange(Base):
    __tablename__ = "admin_app_changes"
    token: Mapped[str] = mapped_column(String(32), primary_key=True)
    actor_id: Mapped[int] = mapped_column(BigInteger, index=True)
    entity: Mapped[str] = mapped_column(String(24))
    target: Mapped[str] = mapped_column(String(100))
    before: Mapped[dict] = mapped_column(JSON)
    after: Mapped[dict] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    created_at: Mapped[int] = mapped_column(BigInteger, index=True)
    published_at: Mapped[int | None] = mapped_column(BigInteger)
    result: Mapped[dict | None] = mapped_column(JSON)
