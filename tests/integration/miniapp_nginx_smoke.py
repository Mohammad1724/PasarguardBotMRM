"""Local-only real Nginx/TLS/proxy smoke. No Docker, ACME, Telegram or production writes."""

import json
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.setup_miniapp import route_config, vhost


class Backend(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps(
            {"path": self.path, "host": self.headers.get("Host"), "proto": self.headers.get("X-Forwarded-Proto")}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    nginx = shutil.which("nginx") or "/usr/sbin/nginx"
    if not Path(nginx).exists() or not shutil.which("openssl"):
        raise RuntimeError("Install nginx and openssl for this opt-in smoke test")
    with tempfile.TemporaryDirectory(prefix="miniapp-nginx-test-") as folder:
        root = Path(folder)
        backend = ThreadingHTTPServer(("127.0.0.1", 0), Backend)
        worker = threading.Thread(target=backend.serve_forever, daemon=True)
        worker.start()
        http_port, tls_port = free_port(), free_port()
        cert, key = root / "fullchain.pem", root / "privkey.pem"
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-days",
                "1",
                "-subj",
                "/CN=admin.example.com",
                "-addext",
                "subjectAltName=DNS:admin.example.com,IP:127.0.0.1",
                "-keyout",
                str(key),
                "-out",
                str(cert),
            ],
            check=True,
            capture_output=True,
        )
        snippet = root / "snippet.conf"
        snippet.write_text(route_config(backend.server_port, "/admin/__setup_fixture", "fixture-proof"))
        webroot = root / "www"
        challenge = webroot / ".well-known/acme-challenge/test"
        challenge.parent.mkdir(parents=True)
        challenge.write_text("challenge-body")
        site = vhost("admin.example.com", webroot, snippet, "fixture", https_port=tls_port)
        site = site.replace("listen 80;", f"listen 127.0.0.1:{http_port};").replace("listen [::]:80;", "")
        site = site.replace(f"listen {tls_port} ssl;", f"listen 127.0.0.1:{tls_port} ssl;").replace(
            f"listen [::]:{tls_port} ssl;", ""
        )
        site = site.replace("/etc/letsencrypt/live/fixture/fullchain.pem", str(cert))
        site = site.replace("/etc/letsencrypt/live/fixture/privkey.pem", str(key))
        config = root / "nginx.conf"
        config.write_text(
            f"pid {root}/nginx.pid;\nerror_log {root}/error.log;\nevents {{}}\nhttp {{access_log off;\nclient_body_temp_path {root}/client_body;\nproxy_temp_path {root}/proxy;\nfastcgi_temp_path {root}/fastcgi;\nuwsgi_temp_path {root}/uwsgi;\nscgi_temp_path {root}/scgi;\n{site}}}\n"
        )
        command = [nginx, "-p", folder, "-c", str(config)]
        check = subprocess.run([*command, "-t"], capture_output=True, text=True)
        if check.returncode:
            raise RuntimeError(check.stderr)
        context = ssl.create_default_context(cafile=str(cert))
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context)
        )

        def get(path, secure=True, data=None):
            url = f"{'https' if secure else 'http'}://127.0.0.1:{tls_port if secure else http_port}{path}"
            return opener.open(
                urllib.request.Request(url, headers={"Host": f"admin.example.com:{tls_port}"}, data=data), timeout=3
            )

        with (root / "process.log").open("w") as log:
            process = subprocess.Popen([*command, "-g", "daemon off;"], stdout=log, stderr=log)
            try:
                for attempt in range(50):
                    try:
                        with get("/admin/__setup_fixture") as response:
                            assert response.read() == b"fixture-proof"
                        break
                    except urllib.error.URLError:
                        if process.poll() is not None or attempt == 49:
                            raise
                        time.sleep(0.05)
                for path in (
                    "/admin",
                    "/admin/",
                    "/admin/api/me",
                    "/admin/assets/app.js",
                    "/admin/account",
                    "/admin/account/api/me",
                    "/admin/account/assets/app.js",
                ):
                    with get(path) as response:
                        data = json.load(response)
                        assert data == {"path": path, "host": f"admin.example.com:{tls_port}", "proto": "https"}
                with get("/.well-known/acme-challenge/test", secure=False) as response:
                    assert response.read() == b"challenge-body"

                class NoRedirect(urllib.request.HTTPRedirectHandler):
                    def redirect_request(self, *args, **kwargs):
                        return None

                plain = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
                try:
                    plain.open(f"http://127.0.0.1:{http_port}/admin?check=1", timeout=3)
                except urllib.error.HTTPError as exc:
                    assert exc.code == 301
                    assert exc.headers["Location"] == f"https://admin.example.com:{tls_port}/admin?check=1"
                else:
                    raise AssertionError("HTTP redirect to custom HTTPS port missing")
                try:
                    get("/admin/api/auth", data=b"x" * 70000)
                except urllib.error.HTTPError as exc:
                    assert exc.code == 413
                else:
                    raise AssertionError("Request size limit missing")
                print(
                    "PASS: real Nginx syntax, trusted local TLS on a generated custom port, proof endpoint, admin and customer proxy paths with Host port preserved, custom-port HTTP redirect, HTTP ACME path and 64KiB limit"
                )
            finally:
                process.terminate()
                process.wait(timeout=10)
                backend.shutdown()
                backend.server_close()
                worker.join(timeout=3)


if __name__ == "__main__":
    main()
