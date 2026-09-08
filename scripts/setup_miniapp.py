#!/usr/bin/env python3
"""Opt-in Docker Mini App setup for Debian/Ubuntu hosts; system Python 3.10+.

Only the bot container, two .env keys and scoped Nginx routes are changed.
No database restore, image pull, DNS-provider changes or firewall flushing.
"""

from __future__ import annotations

import argparse
import fcntl
import glob
import hashlib
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path("/opt/pasarguardbot")
NGINX_ROOT = Path("/etc/nginx")
MARKER = "# Managed by PasarguardBot Mini App setup"


class SetupError(Exception):
    pass


def domain_name(value):
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise SetupError("کاراکتر کنترلی در دامنه مجاز نیست.")
    value = value.strip().lower()
    try:
        parsed = urlsplit(value if "://" in value else "https://" + value)
        port = parsed.port
    except ValueError as exc:
        raise SetupError("آدرس دامنه معتبر نیست.") from exc
    if parsed.scheme != "https" or parsed.username or parsed.password or port or parsed.query or parsed.fragment:
        raise SetupError("فقط نام دامنه یا آدرس HTTPS بدون پورت، رمز و پارامتر وارد کنید.")
    if parsed.path not in ("", "/", "/admin", "/admin/") or not parsed.hostname:
        raise SetupError("نمونه دامنه معتبر: admin.example.com")
    try:
        domain = parsed.hostname.rstrip(".").encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise SetupError("نام دامنه معتبر نیست.") from exc
    labels = domain.split(".")
    if (
        len(domain) > 253
        or len(labels) < 2
        or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels)
        or labels[-1].isdigit()
    ):
        raise SetupError("یک دامنه عمومی معتبر وارد کنید؛ IP و localhost پذیرفته نیستند.")
    if labels[-1] in {"localhost", "local", "internal", "invalid", "test", "example"}:
        raise SetupError("دامنه باید عمومی و قابل دریافت گواهی باشد.")
    return domain


def api_port(content):
    values = re.findall(r"(?m)^[ \t]*(?:export[ \t]+)?FASTAPI_PORT[ \t]*=[ \t]*([^\n]*)", content)
    if len(values) > 1:
        raise SetupError("FASTAPI_PORT چند بار در .env تعریف شده؛ ابتدا آن را یکسان کنید.")
    value = values[0].strip() if values else ""
    # Only simple numeric dotenv values are supported; never evaluate shell text.
    value = re.sub(r"\s+#.*$", "", value).strip().strip("\"'")
    if not value:
        return 6160
    if not value.isascii() or not value.isdecimal() or not 1024 <= int(value) <= 65535:
        raise SetupError("FASTAPI_PORT باید یک پورت عددی بین 1024 و 65535 باشد؛ پورت موجود را کورکورانه عوض نکنید.")
    return int(value)


def update_env(content, domain, port):
    values = {"FASTAPI_PORT": str(port), "ADMIN_MINI_APP_URL": "https://" + domain + "/admin"}
    output = []
    for line in content.splitlines(keepends=True):
        match = re.match(r"^\s*(?:export\s+)?([A-Z_]+)\s*=", line)
        if not match or match[1] not in values:
            output.append(line)
    text = "".join(output)
    return text.rstrip("\r\n") + "\n" + "".join(f"{key}={value}\n" for key, value in values.items())


TOKEN = re.compile(r'\s+|\#[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[{};]|[^\s{};\#"\']+')


def nginx_nodes(text):
    """Read a conservative subset; keep byte offsets for a single include insertion."""
    tokens = []
    cursor = 0
    for match in TOKEN.finditer(text):
        if match.start() != cursor:
            raise SetupError("قالب Nginx برای ویرایش خودکار پشتیبانی نمی‌شود.")
        cursor = match.end()
        value = match[0]
        if not value.isspace() and not value.startswith("#"):
            tokens.append((value.strip("\"'"), match.start(), match.end(), value in {"{", "}", ";"}))
    if cursor != len(text):
        raise SetupError("قالب Nginx برای ویرایش خودکار پشتیبانی نمی‌شود.")
    position = 0

    def parse(nested=False):
        nonlocal position
        nodes, words = [], []
        while position < len(tokens):
            value, start, end, control = tokens[position]
            position += 1
            if control and value == "}":
                if not nested or words:
                    raise SetupError("ساختار Nginx نامعتبر یا پیچیده است.")
                return nodes, start, end
            if control and value in (";", "{"):
                if not words:
                    raise SetupError("ساختار Nginx پشتیبانی نمی‌شود.")
                node = {"name": words[0], "args": words[1:], "children": None, "close": None}
                if value == "{":
                    node["children"], node["close"], _ = parse(True)
                nodes.append(node)
                words = []
            else:
                words.append(value)
        if nested or words:
            raise SetupError("ساختار Nginx ناقص است.")
        return nodes, None, None

    return parse()[0]


def descendants(nodes):
    for node in nodes:
        yield node
        if node["children"] is not None:
            yield from descendants(node["children"])


def expanded_includes(nodes, files, snippet, seen=()):
    result = []
    for node in nodes:
        if node["name"] != "include":
            result.append(node)
            continue
        if node["args"] == [str(snippet)]:
            continue
        if len(node["args"]) != 1 or "$" in node["args"][0]:
            raise SetupError("include پویا یا نامعتبر؛ تنظیم خودکار متوقف شد.")
        pattern = Path(node["args"][0])
        if not pattern.is_absolute():
            pattern = NGINX_ROOT / pattern
        matches = {Path(p).resolve() for p in glob.glob(str(pattern))}
        if pattern.resolve() in files:
            matches.add(pattern.resolve())
        for path in matches:
            if path in seen or path not in files:
                raise SetupError("include پیچیده یا خارج از پیکربندی بارگذاری‌شده؛ تنظیم خودکار متوقف شد.")
            result.extend(expanded_includes(nginx_nodes(files[path]), files, snippet, (*seen, path)))
    return result


def existing_server(files, domain, snippet):
    """Return one explicit HTTPS server. Refuse aliases/regex/collisions, never replace them."""
    found = []
    ambiguous = False
    for path, text in files.items():
        for node in descendants(nginx_nodes(text)):
            if node["name"] != "server" or node["children"] is None:
                continue
            children = node["children"]
            names = [arg for child in children if child["name"] == "server_name" for arg in child["args"]]
            if domain not in names:
                if any(
                    name.startswith("~")
                    or (name.startswith("*.") and domain.endswith(name[1:]))
                    or (name.startswith(".") and (domain == name[1:] or domain.endswith(name)))
                    for name in names
                ):
                    ambiguous = True
                continue
            if any(name != domain for name in names):
                raise SetupError("دامنه در میزبان مشترک با نام‌های دیگر است؛ از میزبان اختصاصی استفاده کنید.")
            tls = any(child["name"] == "listen" and "ssl" in child["args"] for child in children)
            found.append((path, text, node, tls))
    if not found:
        if ambiguous:
            raise SetupError("دامنه ممکن است با میزبان wildcard/regex موجود تداخل داشته باشد؛ تعریف اختصاصی لازم است.")
        return None
    targets = [row for row in found if row[3]]
    if len(targets) != 1:
        raise SetupError("دامنه در Nginx موجود است، ولی یک میزبان HTTPS مشخص ندارد. از زیردامنه اختصاصی استفاده کنید.")
    path, text, node, _ = targets[0]
    children = expanded_includes(node["children"], files, snippet)
    for child in children:
        if child["name"] in {"return", "rewrite", "auth_basic", "auth_request", "ssl_verify_client"}:
            raise SetupError("میزبان فعلی محدودیت یا redirect سراسری دارد؛ بدون تغییر آن، زیردامنه اختصاصی بدهید.")
    for child in descendants(children):
        if child["name"] == "location" and any("admin" in arg.lower() for arg in child["args"]):
            raise SetupError("مسیر admin از قبل در این دامنه استفاده شده؛ برای جلوگیری از تداخل، زیردامنه تازه بدهید.")
    if any(child["name"] == "include" and child["args"] == [str(snippet)] for child in node["children"]):
        return path, text
    return path, text[: node["close"]] + f"\n    include {snippet};\n" + text[node["close"] :]


def route_config(host_port, probe_path, proof):
    proxy = (
        f"        proxy_pass http://127.0.0.1:{host_port};\n"
        "        proxy_set_header Host $host;\n"
        "        proxy_set_header X-Forwarded-Proto $scheme;\n"
        "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
        "        proxy_cache off;\n"
        "        client_max_body_size 64k;\n"
    )
    return (
        MARKER + "\n"
        f'    location = {probe_path} {{ default_type text/plain; return 200 "{proof}"; }}\n'
        "    location = /admin {\n" + proxy + "    }\n"
        "    location ^~ /admin/ {\n" + proxy + "    }\n"
    )


def vhost(domain, webroot, snippet, cert_name=None):
    challenge = f"    location ^~ /.well-known/acme-challenge/ {{ root {webroot}; }}\n"
    http = MARKER + f"\nserver {{\n    listen 80;\n    listen [::]:80;\n    server_name {domain};\n" + challenge
    if cert_name is None:
        return http + "    location / { return 404; }\n}\n"
    cert = "/etc/letsencrypt/live/" + cert_name
    return (
        http + f"    location / {{ return 301 https://{domain}$request_uri; }}\n}}\n"
        f"server {{\n    listen 443 ssl;\n    listen [::]:443 ssl;\n    server_name {domain};\n"
        f"    ssl_certificate {cert}/fullchain.pem;\n    ssl_certificate_key {cert}/privkey.pem;\n"
        "    ssl_protocols TLSv1.2 TLSv1.3;\n    ssl_session_cache shared:PguardMiniApp:1m;\n"
        f"    include {snippet};\n"
        "    location = / { return 302 /admin; }\n    location / { return 404; }\n}\n"
    )


class Journal:
    def __init__(self, directory):
        self.directory = directory
        self.entries = {}
        self.written = {}
        directory.mkdir(mode=0o700, parents=True, exist_ok=False)

    def write(self, path, content, mode=0o644):
        if path.is_symlink():
            raise SetupError("فایل مقصد پیوند نمادین است؛ برای ایمنی متوقف شد.")
        if path not in self.entries:
            original = path.read_bytes() if path.exists() else None
            old_stat = path.stat() if path.exists() else None
            self.entries[path] = (original, old_stat)
            if original is not None:
                backup = self.directory / f"{len(self.entries)}-{path.name}"
                backup.write_bytes(original)
                backup.chmod(0o600)
            (self.directory / "manifest.json").write_text(json.dumps([str(p) for p in self.entries]))
        path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        path.write_text(content)
        self.written[path] = content.encode()
        path.chmod(mode)

    def restore(self):
        conflicts = []
        for path, (original, old_stat) in reversed(list(self.entries.items())):
            try:
                if path.is_symlink() or path.read_bytes() != self.written.get(path):
                    conflicts.append(path)
                    continue
                if original is None:
                    path.unlink()
                else:
                    path.write_bytes(original)
                    path.chmod(stat.S_IMODE(old_stat.st_mode))
                    os.chown(path, old_stat.st_uid, old_stat.st_gid)
            except OSError:
                conflicts.append(path)
        return conflicts


class Setup:
    def __init__(self, domain, root=ROOT):
        self.domain, self.root = domain, root
        self.env = root / ".env"
        self.compose = [
            "docker",
            "compose",
            "--project-directory",
            str(root),
            "-f",
            str(root / "docker-compose.yml"),
            "--env-file",
            str(self.env),
        ]
        self.state = root / "miniapp-setup"
        self.log = None
        self.journal = None
        self.bot_touched = False
        self.new_nginx = False
        self.nginx = shutil.which("nginx") or "/usr/sbin/nginx"

    def run(self, args, timeout=120, check=True):
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "LC_ALL": "C", "DEBIAN_FRONTEND": "noninteractive"},
        )
        if self.log:
            with self.log.open("a") as handle:
                handle.write("\n$ " + " ".join(args) + "\n" + result.stdout + result.stderr)
        if result.returncode and check:
            raise SetupError(f"مرحله {args[0]} ناموفق بود. گزارش خصوصی سرور را بررسی کنید.")
        return result

    def preflight(self):
        if os.geteuid() != 0:
            raise SetupError("این ابزار را با sudo اجرا کنید.")
        if not shutil.which("apt-get") or not Path("/run/systemd/system").is_dir():
            raise SetupError("راه‌اندازی خودکار فعلاً برای Docker روی Debian/Ubuntu دارای systemd است.")
        if not self.env.is_file() or self.env.is_symlink() or not (self.root / "docker-compose.yml").is_file():
            raise SetupError("نصب استاندارد Docker در /opt/pasarguardbot پیدا نشد.")
        if self.root.is_symlink() or self.root.stat().st_uid != 0 or self.root.stat().st_mode & 0o022:
            raise SetupError("پوشه نصب باید متعلق به root و غیرقابل‌نوشتن برای دیگران باشد.")
        if self.state.is_symlink() or (self.state.exists() and self.state.stat().st_uid != 0):
            raise SetupError("پوشه گزارش/بکاپ برای نوشتن امن نیست.")
        self.content = self.env.read_text()
        self.port = api_port(self.content)
        addresses = {row[4][0] for row in socket.getaddrinfo(self.domain, 443, type=socket.SOCK_STREAM)}
        if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
            raise SetupError("DNS دامنه باید عمومی و متصل به این سرور باشد؛ IP خصوصی پذیرفته نیست.")
        if self.run(["docker", "inspect", "pasarguardbot", "--format", "{{.State.Running}}"]).stdout.strip() != "true":
            raise SetupError("ابتدا ربات نسخه سوم را روشن کنید؛ این ابزار نصب اولیه ربات نیست.")
        self.run(["docker", "exec", "pasarguardbot", "test", "-f", "/app/app/assets/admin_app/app.js"])
        self.image = self.run(["docker", "inspect", "pasarguardbot", "--format", "{{.Image}}"]).stdout.strip()
        self.check_image()
        ports = json.loads(
            self.run(["docker", "inspect", "pasarguardbot", "--format", "{{json .NetworkSettings.Ports}}"]).stdout
        )
        bindings = ports.get(f"{self.port}/tcp") or []
        local = [b for b in bindings if b.get("HostIp") in ("127.0.0.1", "0.0.0.0", "")]
        if len(local) != 1 or not str(local[0]["HostPort"]).isdigit():
            raise SetupError("پورت API ربات به لوپ‌بک میزبان نگاشت نشده؛ Compose سفارشی را دستی بررسی کنید.")
        self.host_port = int(local[0]["HostPort"])
        for port in (80, 443):
            listening = self.run(["ss", "-H", "-ltnp", f"sport = :{port}"]).stdout
            if listening and (
                not Path(self.nginx).is_file() or any('"nginx"' not in line for line in listening.splitlines())
            ):
                raise SetupError(
                    f"پورت {port} در اختیار برنامه دیگری است (مثلاً Caddy/Nginx Proxy Manager). چیزی متوقف نشد؛ مسیر را در همان ابزار تنظیم کنید."
                )
        if Path(self.nginx).is_file():
            if self.run(["systemctl", "is-active", "nginx"], check=False).returncode:
                raise SetupError(
                    "Nginx موجود خاموش است؛ برای جلوگیری از فعال‌کردن تنظیمات نامعلوم، ابتدا آن را بررسی کنید."
                )
            self.run([self.nginx, "-t"])

    def check_image(self):
        # Recreating for env must never silently upgrade to a newly pulled dev image.
        config = json.loads(self.run([*self.compose, "config", "--format", "json"]).stdout)
        reference = config["services"]["bot"].get("image")
        if not isinstance(reference, str) or not reference:
            raise SetupError("ایمیج bot در Compose مشخص نیست.")
        current = self.run(["docker", "image", "inspect", reference, "--format", "{{.Id}}"]).stdout.strip()
        if current != self.image:
            raise SetupError("ایمیج محلی با ربات در حال اجرا یکسان نیست؛ ابتدا ارتقای ربات را کامل کنید.")

    def recreate_bot(self):
        self.check_image()
        self.bot_touched = True
        self.run([*self.compose, "up", "-d", "--no-deps", "--force-recreate", "--pull", "never", "bot"], timeout=180)

    def loaded_files(self):
        result = self.run([self.nginx, "-T"])
        output = result.stdout + result.stderr
        paths = set(re.findall(r"(?m)^# configuration file ([^\n]+):$", output))
        return {Path(path).resolve(): Path(path).read_text() for path in paths}

    def reload(self):
        self.run([self.nginx, "-t"])
        self.run(["systemctl", "reload", "nginx"])

    def probe(self, url, expected=None, attempts=1):
        # Trust normal certificate validation; never use an insecure TLS fallback.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for attempt in range(attempts):
            try:
                with opener.open(url, timeout=10) as response:
                    body = response.read(131072)
                    if response.status == 200 and (expected is None or expected.encode() in body):
                        return
            except (OSError, ValueError):
                pass
            if attempt + 1 < attempts:
                time.sleep(2)
        raise SetupError("آزمون آدرس موفق نشد؛ DNS، HTTPS و دسترسی پورت‌ها از اینترنت را بررسی کنید.")

    def execute(self):
        self.preflight()
        self.state.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.state.chmod(0o700)
        stamp = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
        self.journal = Journal(self.state / ("backup-" + stamp))
        self.log = self.journal.directory / "setup.log"
        self.log.touch(mode=0o600)
        key = hashlib.sha256(self.domain.encode()).hexdigest()[:16]
        snippet = NGINX_ROOT / "snippets" / f"pasarguardbot-miniapp-{key}.conf"
        site = NGINX_ROOT / "conf.d" / f"pasarguardbot-miniapp-{key}.conf"
        cert_name = "pasarguardbot-miniapp-" + key
        webroot = Path("/var/lib/pasarguardbot-acme")
        probe_path = "/admin/__setup_" + secrets.token_hex(16)
        proof = secrets.token_hex(24)
        try:
            if not Path(self.nginx).is_file():
                print("نصب Nginx…", flush=True)
                self.new_nginx = True
                self.run(["apt-get", "update"], timeout=600)
                self.run(["apt-get", "install", "-y", "--no-install-recommends", "nginx"], timeout=600)
                self.run(["systemctl", "enable", "--now", "nginx"])
            files = self.loaded_files()
            for managed in (snippet, site):
                if managed.exists() and not managed.read_text().startswith(MARKER):
                    raise SetupError("فایل هم‌نام موجود متعلق به این ابزار نیست؛ بازنویسی نشد.")
            own_site = site.exists()
            existing = None if own_site else existing_server(files, self.domain, snippet)
            if existing and NGINX_ROOT not in existing[0].parents:
                raise SetupError("فایل میزبان Nginx خارج از /etc/nginx است؛ خودکار ویرایش نشد.")
            self.journal.write(snippet, route_config(self.host_port, probe_path, proof))
            if existing:
                print("استفاده از HTTPS موجود؛ سایر مسیرهای دامنه حفظ می‌شوند…", flush=True)
                path, content = existing
                if path.read_text() != files[path]:
                    raise SetupError("پیکربندی Nginx هم‌زمان تغییر کرده؛ بازنویسی نشد.")
                self.journal.write(path, content, stat.S_IMODE(path.stat().st_mode))
            else:
                webroot.mkdir(mode=0o755, parents=True, exist_ok=True)
                self.journal.write(site, vhost(self.domain, webroot, snippet))
                self.reload()
                if site.resolve() not in self.loaded_files():
                    raise SetupError("Nginx پوشه conf.d را بارگذاری نمی‌کند؛ تغییری به فایل اصلی تحمیل نشد.")
                if shutil.which("ufw"):
                    status = self.run(["ufw", "status"]).stdout
                    if "Status: active" in status:
                        self.run(["ufw", "allow", "80/tcp"])
                        self.run(["ufw", "allow", "443/tcp"])
                if not shutil.which("certbot"):
                    self.run(["apt-get", "update"], timeout=600)
                    self.run(["apt-get", "install", "-y", "--no-install-recommends", "certbot"], timeout=600)
                print("دریافت/بررسی گواهی رایگان Let's Encrypt؛ DNS باید به همین سرور برسد…", flush=True)
                self.run(
                    [
                        "certbot",
                        "certonly",
                        "--webroot",
                        "-w",
                        str(webroot),
                        "-d",
                        self.domain,
                        "--cert-name",
                        cert_name,
                        "--non-interactive",
                        "--agree-tos",
                        "--register-unsafely-without-email",
                        "--keep-until-expiring",
                        "--deploy-hook",
                        "systemctl reload nginx",
                    ],
                    timeout=300,
                )
                self.run(["systemctl", "enable", "--now", "certbot.timer"])
                self.journal.write(site, vhost(self.domain, webroot, snippet, cert_name))
            self.reload()
            self.probe("https://" + self.domain + probe_path, proof, attempts=3)
            if self.env.read_text() != self.content:
                raise SetupError("فایل .env هم‌زمان تغییر کرده؛ تغییر شخص دیگری بازنویسی نشد.")
            self.journal.write(
                self.env, update_env(self.content, self.domain, self.port), mode=stat.S_IMODE(self.env.stat().st_mode)
            )
            print("اعمال تنظیمات؛ فقط کانتینر ربات بازسازی می‌شود، بدون pull یا حذف داده‌ها…", flush=True)
            self.recreate_bot()
            self.probe(f"http://127.0.0.1:{self.host_port}/admin/assets/app.js", "Telegram", attempts=30)
            self.probe("https://" + self.domain + "/admin", "/admin/assets/app.js", attempts=3)
            # Remove the temporary proof endpoint after verification.
            clean = route_config(self.host_port, probe_path, proof)
            clean = "\n".join(line for line in clean.splitlines() if probe_path not in line) + "\n"
            self.journal.write(snippet, clean)
            self.reload()
        except BaseException:
            print("راه‌اندازی کامل نشد؛ بازگردانی فایل‌های تنظیمات…", flush=True)
            conflicts = self.journal.restore()
            try:
                if self.new_nginx:
                    self.run(["systemctl", "disable", "--now", "nginx"], check=False)
                else:
                    self.reload()
                if self.bot_touched:
                    if conflicts:
                        self.run(["docker", "stop", "pasarguardbot"], check=False)
                    else:
                        self.recreate_bot()
                if conflicts:
                    print(
                        "تعارض یا خطا در بازگردانی فایل؛ فایل تازه شخص دیگر حفظ شد. بررسی دستی لازم است.",
                        file=sys.stderr,
                    )
            except Exception:
                print("بازگردانی سرویس کامل نشد؛ وضعیت ربات/Nginx و بکاپ خصوصی را بررسی کنید.", file=sys.stderr)
            print(f"بکاپ و گزارش خصوصی: {self.journal.directory}", file=sys.stderr)
            print("بسته‌های نصب‌شده، گواهی و مجوزهای افزوده UFW حذف نمی‌شوند. دیتابیس restore نشده است.", file=sys.stderr)
            raise
        print(f"\nآماده: https://{self.domain}/admin\nدر گفتگوی خصوصی ربات با حساب مالک /adminapp را بفرستید.")
        print("داده‌های ربات حذف نشدند. API/دیتابیس را مستقیم روی اینترنت عمومی نکنید.")
        print(f"بکاپ تنظیمات و گزارش خصوصی: {self.journal.directory}")


def main():
    parser = argparse.ArgumentParser(description="راه‌اندازی خودکار Mini App برای Docker روی Debian/Ubuntu")
    parser.add_argument("domain", nargs="?", help="مثال: admin.example.com")
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("با sudo اجرا کنید.")
    print("DNS باید به این سرور وصل باشد و پورت‌های 80/443 از اینترنت در دسترس باشند.")
    print("HTTPS موجود Nginx حفظ می‌شود؛ در صورت نیاز گواهی Let's Encrypt بدون ایمیل و با پذیرش شرایط آن گرفته می‌شود.")
    print("ربات کوتاه‌مدت بازسازی می‌شود؛ پیش از راه‌اندازی بکاپ داشته باشید. ابزار DNS یا پنل‌های دیگر را تغییر نمی‌دهد.")
    try:
        domain = domain_name(args.domain or input("دامنه مینی‌اپ: "))
        ROOT.mkdir(mode=0o755, parents=True, exist_ok=True)
        with (ROOT / ".miniapp-setup.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise SetupError("راه‌اندازی دیگری در حال اجراست.") from exc
            Setup(domain).execute()
    except (SetupError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"خطا: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("لغو شد.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
