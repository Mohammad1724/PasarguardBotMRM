"""State constants for admin settings_payment."""

SETTINGS_CARD_TO_CARD_STEP = "SettingsCardToCard"

ADD_CARD_NUMBER_STEP = "add_card_number"
ADD_CARD_NAME_STEP = "add_card_name"

SET_MANUAL_MIN_STEP = "set_manual_min"
SET_MANUAL_MAX_STEP = "set_manual_max"
SET_CRYPTO_MIN_STEP = "set_crypto_min"
SET_CRYPTO_MAX_STEP = "set_crypto_max"
SET_RESELLER_MIN_WALLET_STEP = "set_reseller_min_wallet"

SET_MANUAL_BONUS_PERCENT_STEP = "set_manual_bonus_percent"
SET_CRYPTO_BONUS_PERCENT_STEP = "set_crypto_bonus_percent"

SET_ZARINPAL_MERCHANT_STEP = "set_zarinpal_merchant"
SET_ZARINPAL_MIN_STEP = "set_zarinpal_min"
SET_ZARINPAL_MAX_STEP = "set_zarinpal_max"
SET_STARS_RATE_STEP = "set_stars_rate"
SET_STARS_MIN_STEP = "set_stars_min"
SET_STARS_MAX_STEP = "set_stars_max"
SET_REFERRAL_PERCENT_STEP = "set_referral_percent"
SET_REFERRAL_FIRST_BONUS_STEP = "set_referral_first_bonus"
SET_REFERRAL_MIN_DEPOSIT_STEP = "set_referral_min_deposit"

MAAR_ADD_MIN_STEP = "maar_add_min"
MAAR_ADD_MAX_STEP = "maar_add_max"
MAAR_ADD_DELAY_STEP = "maar_add_delay"
MAAR_EDIT_STEPS = frozenset({"maar_edit_min", "maar_edit_max", "maar_edit_delay"})

PAYMENT_INPUT_STEPS = frozenset(
    {
        ADD_CARD_NUMBER_STEP,
        ADD_CARD_NAME_STEP,
        SET_MANUAL_MIN_STEP,
        SET_MANUAL_MAX_STEP,
        SET_CRYPTO_MIN_STEP,
        SET_CRYPTO_MAX_STEP,
        SET_RESELLER_MIN_WALLET_STEP,
        SET_MANUAL_BONUS_PERCENT_STEP,
        SET_CRYPTO_BONUS_PERCENT_STEP,
        SET_ZARINPAL_MERCHANT_STEP,
        SET_ZARINPAL_MIN_STEP,
        SET_ZARINPAL_MAX_STEP,
        SET_STARS_RATE_STEP,
        SET_STARS_MIN_STEP,
        SET_STARS_MAX_STEP,
        SET_REFERRAL_PERCENT_STEP,
        SET_REFERRAL_FIRST_BONUS_STEP,
        SET_REFERRAL_MIN_DEPOSIT_STEP,
        MAAR_ADD_MIN_STEP,
        MAAR_ADD_MAX_STEP,
        MAAR_ADD_DELAY_STEP,
        *MAAR_EDIT_STEPS,
    }
)
