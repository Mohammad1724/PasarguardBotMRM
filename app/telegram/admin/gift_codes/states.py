"""State constants for admin gift code management."""

GIFT_PER_PAGE = 8

GIFT_MENU_MESSAGE = "🎁 کد هدیه"
GIFT_MENU_MESSAGE_ALT = "🎁 کدهای هدیه"

GIFT_ADMIN_STEPS = frozenset(
    {
        "gift_select",
        "gift_info_view",
        "gift_type",
        "gift_value_balance",
        "gift_value_days",
        "gift_value_volume",
        "gift_uses",
        "gift_per_user",
        "gift_expiry",
        "gift_note",
        "gift_manual_code",
    }
)

GIFT_VALUE_STEP_BY_TYPE = {
    "balance": "gift_value_balance",
    "days": "gift_value_days",
    "volume": "gift_value_volume",
}
