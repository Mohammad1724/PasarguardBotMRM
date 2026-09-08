from sqlalchemy import BigInteger, Column, Integer, String

from app.db.base import Base


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False)
    amount = Column(BigInteger, nullable=False)
    method = Column(String(20), nullable=False)  # manual or auto
    status = Column(String(20), nullable=False, default="pending")
    created_at = Column(BigInteger, nullable=False)
    completed_at = Column(BigInteger, nullable=True)
    message_id = Column(BigInteger, nullable=True)  # log channel message id
    message_chat_id = Column(BigInteger, nullable=True)
    auto_approve_at = Column(BigInteger, nullable=True)
    auto_approve_rule_id = Column(BigInteger, nullable=True)
