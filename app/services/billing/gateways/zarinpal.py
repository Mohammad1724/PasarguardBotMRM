"""ZarinPal payment gateway (API v4).

Minimal async client for requesting and verifying payments.
Amounts are handled in Toman inside the bot; ZarinPal v4 expects Rial,
so every value sent to the gateway is multiplied by 10.

Docs: https://docs.zarinpal.com/
"""

from __future__ import annotations

from app.logger import get_logger

logger = get_logger(__name__)

PRODUCTION_BASE = "https://payment.zarinpal.com"
SANDBOX_BASE = "https://sandbox.zarinpal.com"

REQUEST_PATH = "/pg/v4/payment/request.json"
VERIFY_PATH = "/pg/v4/payment/verify.json"

REQUEST_OK = 100
VERIFY_OK = 100
VERIFY_ALREADY = 101


class ZarinpalResult:
    def __init__(self, ok: bool, code: int = 0, message: str = "", authority: str = "", ref_id: str = "", url: str = ""):
        self.ok = ok
        self.code = code
        self.message = message
        self.authority = authority
        self.ref_id = ref_id
        self.url = url


def _base_url(sandbox: bool) -> str:
    return SANDBOX_BASE if sandbox else PRODUCTION_BASE


def toman_to_rial(amount_toman: int) -> int:
    return int(amount_toman) * 10


def start_pay_url(authority: str, *, sandbox: bool = False) -> str:
    return f"{_base_url(sandbox)}/pg/StartPay/{authority}"


async def request_payment(
    merchant_id: str,
    amount_toman: int,
    callback_url: str,
    description: str,
    *,
    sandbox: bool = False,
    email: str | None = None,
    mobile: str | None = None,
) -> ZarinpalResult:
    """Create a payment session; returns the StartPay URL on success."""
    import httpx

    payload: dict = {
        "merchant_id": merchant_id,
        "amount": toman_to_rial(amount_toman),
        "callback_url": callback_url,
        "description": description,
    }
    if email:
        payload["email"] = email
    if mobile:
        payload["mobile"] = mobile

    base = _base_url(sandbox)
    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            response = await client.post(f"{base}{REQUEST_PATH}", json=payload)
            data = response.json()
    except Exception as exc:
        logger.error("ZarinPal request error: %s", exc)
        return ZarinpalResult(False, message=f"gateway_unreachable: {exc}")

    body = data.get("data") or {}
    errors = data.get("errors") or {}
    code = int(body.get("code") or 0)
    if code == REQUEST_OK and body.get("authority"):
        authority = str(body["authority"])
        return ZarinpalResult(True, code=code, authority=authority, url=f"{base}/pg/StartPay/{authority}")

    message = "unknown_error"
    if isinstance(errors, dict):
        message = str(errors.get("message") or errors.get("code") or message)
    elif isinstance(errors, list) and errors:
        first = errors[0] or {}
        message = str(first.get("message") or first.get("code") or message)
    logger.warning("ZarinPal request failed: code=%s message=%s", code, message)
    return ZarinpalResult(False, code=code, message=message)


async def verify_payment(
    merchant_id: str,
    amount_toman: int,
    authority: str,
    *,
    sandbox: bool = False,
) -> ZarinpalResult:
    """Verify a payment by authority. code 100 = paid, 101 = already verified."""
    import httpx

    payload = {
        "merchant_id": merchant_id,
        "amount": toman_to_rial(amount_toman),
        "authority": authority,
    }
    base = _base_url(sandbox)
    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            response = await client.post(f"{base}{VERIFY_PATH}", json=payload)
            data = response.json()
    except Exception as exc:
        logger.error("ZarinPal verify error: %s", exc)
        return ZarinpalResult(False, message=f"gateway_unreachable: {exc}")

    body = data.get("data") or {}
    errors = data.get("errors") or {}
    code = int(body.get("code") or 0)
    if code in (VERIFY_OK, VERIFY_ALREADY):
        ref_id = str(body.get("ref_id") or "")
        return ZarinpalResult(True, code=code, ref_id=ref_id, authority=authority)

    message = "unknown_error"
    if isinstance(errors, dict):
        message = str(errors.get("message") or errors.get("code") or message)
    elif isinstance(errors, list) and errors:
        first = errors[0] or {}
        message = str(first.get("message") or first.get("code") or message)
    return ZarinpalResult(False, code=code, message=message)
