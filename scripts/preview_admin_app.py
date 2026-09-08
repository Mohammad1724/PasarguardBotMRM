"""Static-only demo. No bot imports, credentials, database or production API."""

import mimetypes
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "app/assets/admin_app"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/admin", "/admin/"):
            data = re.sub(
                r'<script\b[^>]*src="https://telegram.org/js/telegram-web-app.js"[^>]*>\s*</script>',
                '<script defer src="/admin/assets/demo.js"></script>',
                (ROOT / "index.html").read_text(),
                flags=re.S,
            ).encode()
            mime = "text/html; charset=utf-8"
        elif path == "/preview-meta.json":
            data = (ROOT / "demo-meta.json").read_bytes()
            mime = "application/json"
        elif path.startswith("/admin/assets/") and path.rsplit("/", 1)[-1] in (
            "style.css",
            "app.js",
            "demo.js",
            "Vazirmatn.woff2",
        ):
            p = ROOT / path.rsplit("/", 1)[-1]
            data = p.read_bytes()
            mime = mimetypes.guess_type(str(p))[0]
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
