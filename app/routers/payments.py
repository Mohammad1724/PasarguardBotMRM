"""Informational return page. Query parameters NEVER authorize wallet credit."""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

payment_router = APIRouter()


@payment_router.get("/payments/zarinpal/return", response_class=HTMLResponse)
async def zarinpal_return():
    return """<!doctype html><html lang="fa" dir="rtl"><meta charset="utf-8">
<title>بازگشت از درگاه</title><body><h1>بازگشت از درگاه پرداخت</h1>
<p>برای مشاهده نتیجه به ربات برگردید و «بررسی پرداخت» را بزنید.
تأیید پرداخت فقط پس از استعلام سرور از زرین‌پال انجام می‌شود.</p></body></html>"""
