from sqlalchemy import BigInteger, Column, String

from app.db.base import Base


class CryptoPayments(Base):
    __tablename__ = "cryptopayments"

    order_id = Column(BigInteger, primary_key=True)
    status = Column(String(50), default="Pending")
    user_id = Column(BigInteger, nullable=False)
    arz = Column(String(20), nullable=True)
    amount = Column(String(50), nullable=False)
    amount_irt = Column(BigInteger, nullable=False)
    paytime = Column(BigInteger, default=0)
    createtime = Column(BigInteger, nullable=False)
    msg_id = Column(BigInteger, nullable=True)

    payment_ref = Column(String(255), nullable=True, unique=True)
    gateway_merchant = Column(String(64), nullable=True)
    gateway_sandbox = Column(String(5), nullable=True)

    gateway_ref_id = Column(String(128), nullable=True)

    next_check_at = Column(BigInteger, nullable=False, default=0, server_default="0")

    checkout_query_id = Column(BigInteger, nullable=True)
