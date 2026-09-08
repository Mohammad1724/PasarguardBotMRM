"""Finite, local Chromium UI smoke with synthetic API/Telegram data, never real accounts.

Run with Playwright installed and its Chromium browser available.
Optional UI_SMOKE_OUTPUT selects the directory for synthetic screenshots.
"""

import json
import mimetypes
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
root = ROOT / "app/assets"
OUT = Path(os.getenv("UI_SMOKE_OUTPUT", str(ROOT / ".cache/customer-ui")))
OUT.mkdir(parents=True, exist_ok=True)
NOW = int(time.time())


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlsplit(self.path).path
        if path in ["/admin/account", "/admin/account/"]:
            file = root / "customer_app/index.html"
        elif path in ["/admin", "/admin/"]:
            file = root / "admin_app/index.html"
        elif path.startswith("/admin/account/assets/"):
            file = root / "customer_app" / path.rsplit("/", 1)[-1]
        elif path.startswith("/admin/assets/"):
            file = root / "admin_app" / path.rsplit("/", 1)[-1]
        else:
            self.send_error(404)
            return
        if not file.is_file():
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(file.name)[0] or "application/octet-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(file.read_bytes())

    def log_message(self, *args):
        pass


sdk = 'window.Telegram={WebApp:{initData:"synthetic-preview-only",ready(){},expand(){},setHeaderColor(){},close(){window.didClose=true;}}};'
state = {
    "role": "customer",
    "amount": "1250000",
    "fail_page": False,
    "offline_logout": False,
    "drafts": [],
    "publishes": 0,
    "pages": [],
}
rows = [
    {
        "code": str(100 + i),
        "username": ["Germany • Personal", "Finland • Monthly"][i],
        "enabled": True,
        "is_test": False,
        "package_bytes": str((50 + i * 30) * 1073741824),
        "created_at": NOW - 86400,
        "expires_at": NOW + (12 + i * 5) * 86400,
    }
    for i in range(2)
]


def api(route):
    p = urlsplit(route.request.url)
    body = route.request.post_data_json
    code = 200
    data = {}
    path = p.path
    if "/account/api/" in path:
        endpoint = path.split("/account/api/")[1]
        if endpoint == "auth":
            data = {"token": "synthetic-customer-token"}
        elif endpoint == "logout":
            if state["offline_logout"]:
                return route.abort()
            data = {"logged_out": True}
        elif endpoint == "me":
            data = {"user_id": "12345678", "balance": state["amount"], "services_count": 31, "active_count": 2}
        elif endpoint == "services":
            page = int(parse_qs(p.query).get("page", ["1"])[0])
            state["pages"].append(page)
            if state["fail_page"] and page == 2:
                code = 503
                data = {"detail": "Synthetic temporary outage"}
            else:
                data = {"items": rows, "page": page, "total": 31}
        else:
            data = rows[0]
    else:
        endpoint = path.split("/admin/api/")[1]
        if endpoint == "auth":
            if state["role"] == "customer":
                code = 403
                data = {"detail": "No admin access"}
            else:
                data = {"token": "synthetic-admin-token"}
        elif endpoint == "me":
            data = {
                "actor": {"id": 1, "owner": True, "name": "مالک آزمایشی", "permissions": []},
                "sections": {},
                "permissions": {},
                "buttons": [],
                "texts": {},
                "home_keys": [],
            }
        elif endpoint == "dashboard":
            data = {
                "counts": {"users": 1, "services": 2, "plans": 0, "tickets": 0, "wallet_liability": "1250000"},
                "activity": [0] * 7,
            }
        elif endpoint.startswith("list/users"):
            data = {
                "items": [{"id": 2, "amount": state["amount"], "status": None, "time_s": NOW, "tested": True}],
                "total": 1,
                "page": 1,
            }
        elif endpoint.startswith("list/"):
            data = {"items": [], "total": 0, "page": 1}
        elif endpoint == "document/wallet/2":
            data = {"value": {"amount": state["amount"], "currency": "IRT"}, "version": "synthetic-version"}
        elif endpoint == "changes":
            state["drafts"].append(body)
            data = {
                **body,
                "token": "synthetic-draft",
                "before": {"amount": state["amount"], "currency": "IRT"},
                "after": body["value"],
                "warning": "داده آزمایشی؛ تغییری در حساب واقعی انجام نمی‌شود.",
            }
        elif endpoint == "changes/synthetic-draft/publish":
            state["publishes"] += 1
            state["amount"] = state["drafts"][-1]["value"]["amount"]
            data = {"replayed": False, "result": {"balance_after": state["amount"]}}
        else:
            raise AssertionError("Unexpected API path " + path)
    route.fulfill(status=code, content_type="application/json", body=json.dumps(data))
    return None


with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
    BASE = f"http://127.0.0.1:{server.server_port}"
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--no-sandbox"])
            context = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=1)
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.route("https://telegram.org/**", lambda r: r.fulfill(content_type="text/javascript", body=sdk))
            page.route("**/admin/account/api/**", api)
            page.route("**/admin/api/**", api)
            page.goto(BASE + "/admin#tgWebAppData=synthetic")
            page.wait_for_selector(".balance")
            assert "/admin/account#tgWebAppData=synthetic" in page.url
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path=str(OUT / "customer-mobile.png"), full_page=True)
            page.click("[data-action=privacy]")
            assert "••••••" in page.locator(".balance").inner_text()
            page.click("[data-action=privacy]")
            page.locator("[data-action=details]").first.click()
            page.locator("#details").wait_for(state="visible")
            page.click("#close-detail")
            state["fail_page"] = True
            page.click("[data-action=next]")
            page.wait_for_selector("#notice:not([hidden])")
            state["fail_page"] = False
            page.click("[data-action=next]")
            page.wait_for_function('document.querySelector(".pagination").innerText.includes("۲")')
            assert state["pages"][-2:] == [2, 2], state["pages"]
            rows[0]["username"] = '<img src=x onerror="window.injected=true">'
            page.click("[data-action=refresh]")
            page.wait_for_function('document.querySelector(".service h3").textContent.startsWith("<img")')
            assert not page.locator("img[src=x]").count() and not page.evaluate("window.injected")
            rows[0]["username"] = "Germany • Personal"
            page.click("[data-action=refresh]")
            page.wait_for_function('document.querySelector(".service h3").textContent.startsWith("Germany")')
            page.set_viewport_size({"width": 1440, "height": 1000})
            page.screenshot(path=str(OUT / "customer-desktop.png"), full_page=True)
            state["offline_logout"] = True
            page.click("[data-action=logout]")
            page.wait_for_selector(".loading")
            assert not page.locator(".balance").count() and not page.locator(".service").count()
            assert page.evaluate("Object.keys(localStorage).length + Object.keys(sessionStorage).length") == 0
            state["role"] = "owner"
            page.goto(BASE + "/admin")
            page.wait_for_selector("[data-nav=users]", state="attached")
            page.locator("[data-nav=users]").evaluate("e=>e.closest('details').open=true")
            page.click("[data-nav=users]")
            page.wait_for_selector("[data-action=edit-wallet]")
            page.click("[data-action=edit-wallet]")
            page.locator("#wallet-amount").fill("۱۵۰۰۰۰۰")
            page.locator("#wallet-reason").fill("اصلاح آزمایشی موجودی")
            page.click("[data-action=save]")
            page.wait_for_selector("#confirm-change")
            assert state["drafts"][-1]["value"]["amount"] == "1500000"
            assert page.locator("[data-action=publish]").is_disabled()
            page.screenshot(path=str(OUT / "wallet-review-desktop.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            page.screenshot(path=str(OUT / "wallet-review-mobile.png"), full_page=True)
            page.check("#confirm-change")
            page.click("[data-action=publish]")
            page.wait_for_function('!document.querySelector("#dialog").open')
            assert state["publishes"] == 1
            assert not errors, errors
            browser.close()
        print(
            "PASS: customer/admin browser flows, 390px/1440px layouts, admin-403 fallback preserving Telegram fragment, exact Persian amount conversion, final confirmation, private detail, privacy toggle, XSS escaping, failed pagination retry, offline logout clears data, no token storage; synthetic data only."
        )
    finally:
        server.shutdown()
        worker.join(timeout=5)
