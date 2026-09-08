from sqlalchemy import BigInteger, Boolean, Column, Integer, String, Text

from app.db.base import Base


class GiftCode(Base):
    __tablename__ = "gift_codes"

    GIFT_BALANCE = "balance"
    GIFT_DAYS = "days"
    GIFT_VOLUME = "volume"
    GIFT_TYPES = (GIFT_BALANCE, GIFT_DAYS, GIFT_VOLUME)

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(60), unique=True, nullable=False)
    type = Column(String(20), nullable=False)  # balance | days | volume
    value = Column(BigInteger, nullable=False)  # Toman | days | GB
    max_uses = Column(Integer, nullable=False, default=1)
    times_used = Column(Integer, nullable=False, default=0)
    per_user_limit = Column(Integer, nullable=False, default=1)
    expires_at = Column(BigInteger, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    note = Column(String(255), nullable=True)
    created_at = Column(BigInteger, nullable=False, default=0)

    def __repr__(self):
        return f"<GiftCode(code={self.code}, type={self.type}, value={self.value}, times_used={self.times_used})>"


class GiftCodeUse(Base):
    __tablename__ = "gift_code_uses"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    code_id = Column(BigInteger, nullable=False)
    code = Column(String(60), nullable=True)
    user_id = Column(BigInteger, nullable=False)
    service_code = Column(String(100), nullable=True)
    value = Column(BigInteger, nullable=False, default=0)
    used_at = Column(BigInteger, nullable=False, default=0)

    request_id = Column(String(32), nullable=True, unique=True)
    status = Column(String(20), nullable=False, default="applied")
    plan = Column(Text, nullable=True)

    def __repr__(self):
        return f"<GiftCodeUse(code_id={self.code_id}, user_id={self.user_id}, used_at={self.used_at})>"
