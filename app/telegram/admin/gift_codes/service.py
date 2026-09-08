"""Service helpers for admin gift code management."""

import secrets
import string

from app.db.crud.gift_codes import GiftCodeCRUD
from app.utils.formatting.dates import Time_Date


def generate_gift_code(length: int = 8) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


async def create_gift_with_log(code, type, value, max_uses, per_user_limit, expires_at, note) -> str:
    crud = GiftCodeCRUD()
    gift = await crud.create(
        code=code,
        type=type,
        value=value,
        max_uses=max_uses,
        per_user_limit=per_user_limit,
        expires_at=expires_at or None,
        note=note or None,
        created_at=Time_Date()["stamp"],
    )
    return gift.code if gift else ""
