"""Callback handlers for user balance."""

from telethon import events

from app.db.crud.cryptopayments import CryptoPaymentsCRUD
from app.db.crud.settings import SettingsManager
from app.db.crud.user import UserManager
from app.db.crud.wallets import WalletCRUD
from app.services.billing.direct_pay_flow import (
    CALLBACK_DIRECT_PAY_TOPUP,
    clamp_deposit_amount,
    get_direct_pay_prefilled_amount,
    is_direct_pay_active,
    start_direct_pay_topup,
)
from app.telegram.admin.settings_payment.texts import is_manual_card_visible
from app.telegram.keyboards.balance import (
    balance_flow_cancel_rows,
    create_inline_crypto_payment_buttons,
)
from app.telegram.shared.guards.callback_guards import notify_session_expired
from app.telegram.shared.utils.maintenance import bot_is_offline
from app.telegram.shared.utils.rate_limit import debounce_callback
from app.telegram.state import get_data, get_step, set_data, set_step
from app.telegram.user.balance import states, texts
from app.telegram.user.balance.messages import (
    _prompt_crypto_amount,
    _request_phone_for_balance_payment,
    _require_balance_payment_step,
    create_crypto_invoice,
    create_zarinpal_invoice,
    manual_card_amount_placeholders,
    manual_card_prompt_amount,
    manual_card_send_channel_info,
    remember_balance_flow_message,
    return_to_balance_menu,
    return_to_home_menu,
)
from app.utils.text.bot_texts import get_bot_text


@bot_is_offline
@debounce_callback()
async def crypto_payments_callback(event: events.CallbackQuery.Event):
    if not await _require_balance_payment_step(event):
        return
    settings = await SettingsManager().get_settings()
    if not settings.arz_mode:
        await event.answer(texts.PAYMENT_DISABLED_ALERT, alert=True)
        raise events.StopPropagation
    trx_wallet = await WalletCRUD().get_wallet_by_type("TRX")
    usdt_wallet = await WalletCRUD().get_wallet_by_type("USDT")
    ton_wallet = await WalletCRUD().get_wallet_by_type("TON")

    if not trx_wallet and not usdt_wallet and not ton_wallet:
        await event.answer(texts.NO_CRYPTO_WALLET_ALERT, alert=True)
        raise events.StopPropagation

    buttons = await create_inline_crypto_payment_buttons(
        has_trx=bool(trx_wallet),
        has_usdt=bool(usdt_wallet),
        has_ton=bool(ton_wallet),
    )
    await event.edit(texts.CRYPTO_SELECT_PROMPT, buttons=buttons)
    await remember_balance_flow_message(event.sender_id, event.message_id)
    raise events.StopPropagation


@bot_is_offline
@debounce_callback()
async def crypto_payments_trx_callback(event: events.CallbackQuery.Event):
    if not await _require_balance_payment_step(event):
        return
    if await is_direct_pay_active(event.sender_id):
        settings = await SettingsManager().get_settings()
        amount = await get_direct_pay_prefilled_amount(event.sender_id)
        if amount is None:
            await event.answer(texts.ENTER_AMOUNT_FIRST_ALERT, alert=True)
            raise events.StopPropagation
        amount = clamp_deposit_amount(amount, settings.crypto_deposit_min, settings.crypto_deposit_max)
        await set_data(event.sender_id, "mablagh", amount)
        await create_crypto_invoice(event, arz="trx", amount_irt=amount)
        raise events.StopPropagation
    await _prompt_crypto_amount(event, step=states.STEP_CRYPTO_TRX_2, currency_text=texts.CRYPTO_TRX_CURRENCY)
    raise events.StopPropagation


@bot_is_offline
@debounce_callback()
async def crypto_payments_usdt_callback(event: events.CallbackQuery.Event):
    if not await _require_balance_payment_step(event):
        return
    if await is_direct_pay_active(event.sender_id):
        settings = await SettingsManager().get_settings()
        amount = await get_direct_pay_prefilled_amount(event.sender_id)
        if amount is None:
            await event.answer(texts.ENTER_AMOUNT_FIRST_ALERT, alert=True)
            raise events.StopPropagation
        amount = clamp_deposit_amount(amount, settings.crypto_deposit_min, settings.crypto_deposit_max)
        await set_data(event.sender_id, "mablagh", amount)
        await create_crypto_invoice(event, arz="usdt", amount_irt=amount)
        raise events.StopPropagation
    await _prompt_crypto_amount(event, step=states.STEP_CRYPTO_USDT_2, currency_text=texts.CRYPTO_USDT_CURRENCY)
    raise events.StopPropagation


@bot_is_offline
@debounce_callback()
async def crypto_payments_ton_callback(event: events.CallbackQuery.Event):
    if not await _require_balance_payment_step(event):
        return
    if await is_direct_pay_active(event.sender_id):
        settings = await SettingsManager().get_settings()
        amount = await get_direct_pay_prefilled_amount(event.sender_id)
        if amount is None:
            await event.answer(texts.ENTER_AMOUNT_FIRST_ALERT, alert=True)
            raise events.StopPropagation
        amount = clamp_deposit_amount(amount, settings.crypto_deposit_min, settings.crypto_deposit_max)
        await set_data(event.sender_id, "mablagh", amount)
        await create_crypto_invoice(event, arz="ton", amount_irt=amount)
        raise events.StopPropagation
    await _prompt_crypto_amount(event, step=states.STEP_CRYPTO_TON_2, currency_text=texts.CRYPTO_TON_CURRENCY)
    raise events.StopPropagation


@bot_is_offline
@debounce_callback()
async def manual_card_payment_callback(event: events.CallbackQuery.Event):
    if not await _require_balance_payment_step(event):
        return
    settings = await SettingsManager().get_settings()
    user = await UserManager().get_user_by_id(event.sender_id)
    if not is_manual_card_visible(settings, user):
        await event.answer(texts.PAYMENT_DISABLED_ALERT, alert=True)
        raise events.StopPropagation

    pay_phone_verify = bool(getattr(settings, "pay_phone_verify", True))
    if pay_phone_verify and not user.number:
        await _request_phone_for_balance_payment(event)
    elif await is_direct_pay_active(event.sender_id):
        amount = await get_direct_pay_prefilled_amount(event.sender_id)
        if amount is None:
            await event.answer(texts.ENTER_AMOUNT_FIRST_ALERT, alert=True)
            raise events.StopPropagation
        amount = clamp_deposit_amount(amount, settings.manual_deposit_min, settings.manual_deposit_max)
        await set_data(event.sender_id, "mablagh", amount)
        await manual_card_send_channel_info(event, amount, edit=True)
        await set_step(event.sender_id, step=states.STEP_CART_B_CART2)
    else:
        await manual_card_prompt_amount(event)
        await set_step(user_id=event.sender_id, step=states.STEP_CART_B_CART_AMOUNT)
    raise events.StopPropagation


@bot_is_offline
@debounce_callback()
async def manual_card_send_photo_callback(event: events.CallbackQuery.Event):
    if await get_step(event.sender_id) != states.STEP_CART_B_CART2:
        await notify_session_expired(event)
        return
    mablagh = await get_data(event.sender_id, "mablagh")
    if not mablagh:
        await event.answer(texts.ENTER_AMOUNT_FIRST_ALERT, alert=True)
        raise events.StopPropagation
    settings = await SettingsManager().get_settings()
    ph = await manual_card_amount_placeholders(int(mablagh), settings)
    receipt_request_text = await get_bot_text(
        key="manual_card_receipt_request",
        default=texts.MANUAL_CARD_RECEIPT_REQUEST_DEFAULT,
        lang="fa",
    )
    await event.edit(
        receipt_request_text.format(**ph),
        buttons=await balance_flow_cancel_rows(),
    )
    await remember_balance_flow_message(event.sender_id, event.message_id)
    await set_step(user_id=event.sender_id, step=states.STEP_MABLAGH_SHARJ)
    raise events.StopPropagation


@bot_is_offline
@debounce_callback()
async def zarinpal_payment_callback(event: events.CallbackQuery.Event):
    if not await _require_balance_payment_step(event):
        return
    settings = await SettingsManager().get_settings()
    if not settings or not getattr(settings, "zarinpal_mode", False) or not settings.zarinpal_merchant:
        await event.answer(texts.ZARINPAL_GATEWAY_DISABLED_ALERT, alert=True)
        raise events.StopPropagation
    if await is_direct_pay_active(event.sender_id):
        amount = await get_direct_pay_prefilled_amount(event.sender_id)
        if amount is None:
            await event.answer(texts.ENTER_AMOUNT_FIRST_ALERT, alert=True)
            raise events.StopPropagation
        amount = clamp_deposit_amount(amount, settings.zarinpal_deposit_min, settings.zarinpal_deposit_max)
        await set_data(event.sender_id, "mablagh", amount)
        await create_zarinpal_invoice(event, amount_irt=amount)
        raise events.StopPropagation
    await event.edit(
        texts.ZARINPAL_AMOUNT_PROMPT_TEMPLATE.format(
            min=f"{settings.zarinpal_deposit_min:,}",
            max=f"{settings.zarinpal_deposit_max:,}",
        ),
        buttons=await balance_flow_cancel_rows(),
    )
    await remember_balance_flow_message(event.sender_id, event.message_id)
    await set_step(user_id=event.sender_id, step=states.STEP_ZARINPAL_2)
    raise events.StopPropagation


@bot_is_offline
@debounce_callback()
async def zarinpal_check_callback(event: events.CallbackQuery.Event):
    """Manual on-demand verification for a pending ZarinPal invoice."""
    data = event.data.decode("utf-8")
    try:
        order_id = int(data.split(":")[1])
    except (IndexError, ValueError):
        raise events.StopPropagation from None

    settings = await SettingsManager().get_settings()
    payment = await CryptoPaymentsCRUD().get_by_order_id(order_id)
    if not payment or (payment.arz or "").upper() != "ZARINPAL":
        await event.answer(texts.ZARINPAL_NOT_FOUND, alert=True)
        raise events.StopPropagation
    if payment.status != "Pending":
        await event.answer(texts.ZARINPAL_CHECK_PENDING, alert=True)
        raise events.StopPropagation

    from app.jobs.payments.zarinpal import confirm_zarinpal_payment
    from app.services.billing.gateways import zarinpal

    result = await zarinpal.verify_payment(
        settings.zarinpal_merchant,
        int(payment.amount_irt),
        str(payment.amount),
        sandbox=settings.zarinpal_sandbox,
    )
    if not result.ok:
        await event.answer(texts.ZARINPAL_CHECK_PENDING, alert=True)
        raise events.StopPropagation
    await confirm_zarinpal_payment(payment, settings, result.ref_id)
    await event.answer("✅ پرداخت با موفقیت تایید شد", alert=True)
    raise events.StopPropagation


@bot_is_offline
@debounce_callback()
async def stars_payment_callback(event: events.CallbackQuery.Event):
    if not await _require_balance_payment_step(event):
        return
    settings = await SettingsManager().get_settings()
    if not settings or not getattr(settings, "stars_mode", False) or int(getattr(settings, "stars_rate", 0) or 0) <= 0:
        await event.answer(texts.STARS_RATE_UNSET, alert=True)
        raise events.StopPropagation
    if await is_direct_pay_active(event.sender_id):
        amount = await get_direct_pay_prefilled_amount(event.sender_id)
        if amount is None:
            await event.answer(texts.ENTER_AMOUNT_FIRST_ALERT, alert=True)
            raise events.StopPropagation
        amount = clamp_deposit_amount(amount, settings.stars_deposit_min, settings.stars_deposit_max)
        await set_data(event.sender_id, "mablagh", amount)
        from app.telegram.user.balance.stars import create_stars_invoice

        await create_stars_invoice(event, amount_irt=amount)
        raise events.StopPropagation
    await event.edit(
        texts.STARS_AMOUNT_PROMPT_TEMPLATE.format(
            min=f"{settings.stars_deposit_min:,}",
            max=f"{settings.stars_deposit_max:,}",
        ),
        buttons=await balance_flow_cancel_rows(),
    )
    await remember_balance_flow_message(event.sender_id, event.message_id)
    await set_step(user_id=event.sender_id, step=states.STEP_STARS_2)
    raise events.StopPropagation


@bot_is_offline
@debounce_callback()
async def balance_flow_cancel_callback(event: events.CallbackQuery.Event):
    if await get_step(event.sender_id) not in states.BALANCE_FLOW_CANCEL_STEPS:
        await event.answer(texts.FLOW_NOT_CANCELLABLE_ALERT, alert=True)
        raise events.StopPropagation
    await return_to_balance_menu(event)
    raise events.StopPropagation


@bot_is_offline
@debounce_callback()
async def balance_return_home_callback(event: events.CallbackQuery.Event):
    await return_to_home_menu(event)
    raise events.StopPropagation


@bot_is_offline
@debounce_callback()
async def back_to_balance_callback(event: events.CallbackQuery.Event):
    await return_to_balance_menu(event)
    raise events.StopPropagation


@bot_is_offline
@debounce_callback()
async def direct_pay_topup_callback(event: events.CallbackQuery.Event):
    started = await start_direct_pay_topup(event)
    if not started:
        await return_to_balance_menu(event)
    raise events.StopPropagation


@bot_is_offline
@debounce_callback()
async def cart_b_cart_callback(event: events.CallbackQuery.Event):
    if await get_step(event.sender_id) != states.STEP_CART_B_CART:
        await notify_session_expired(event)
        return
    await return_to_balance_menu(event)
    raise events.StopPropagation


def register(client):
    client.add_event_handler(
        crypto_payments_callback,
        events.CallbackQuery(data=states.CALLBACK_CRYPTO),
    )
    client.add_event_handler(
        crypto_payments_trx_callback,
        events.CallbackQuery(data=states.CALLBACK_CRYPTO_TRX),
    )
    client.add_event_handler(
        crypto_payments_usdt_callback,
        events.CallbackQuery(data=states.CALLBACK_CRYPTO_USDT),
    )
    client.add_event_handler(
        crypto_payments_ton_callback,
        events.CallbackQuery(data=states.CALLBACK_CRYPTO_TON),
    )
    client.add_event_handler(
        zarinpal_payment_callback,
        events.CallbackQuery(data=states.CALLBACK_ZARINPAL),
    )
    client.add_event_handler(
        zarinpal_check_callback,
        events.CallbackQuery(pattern=rb"^zarinpal_check:\d+$"),
    )
    client.add_event_handler(
        stars_payment_callback,
        events.CallbackQuery(data=states.CALLBACK_STARS),
    )
    client.add_event_handler(
        manual_card_payment_callback,
        events.CallbackQuery(data=states.CALLBACK_CART_PAYMENT),
    )
    client.add_event_handler(
        manual_card_send_photo_callback,
        events.CallbackQuery(data=states.CALLBACK_CART_PAYMENT_SENDPHOTO),
    )
    client.add_event_handler(
        balance_flow_cancel_callback,
        events.CallbackQuery(data=states.CALLBACK_FLOW_CANCEL),
    )
    client.add_event_handler(
        balance_return_home_callback,
        events.CallbackQuery(data=states.CALLBACK_RETURN_HOME),
    )
    client.add_event_handler(
        back_to_balance_callback,
        events.CallbackQuery(data=states.CALLBACK_BACK_TO_BALANCE),
    )
    client.add_event_handler(
        direct_pay_topup_callback,
        events.CallbackQuery(data=CALLBACK_DIRECT_PAY_TOPUP),
    )
    client.add_event_handler(
        cart_b_cart_callback,
        events.CallbackQuery(data=states.CALLBACK_CART_B_CART),
    )
