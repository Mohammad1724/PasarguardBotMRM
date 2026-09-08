#!/usr/bin/env python3
"""Opt-in Docker/Native Mini App setup for Debian/Ubuntu hosts; system Python 3.10+.

Only the bot runtime, two .env keys and scoped Nginx routes are changed.
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
        raise SetupError("Control characters are not allowed in the domain.")
    value = value.strip().lower()
    try:
        parsed = urlsplit(value if "://" in value else "https://" + value)
        port = parsed.port
    except ValueError as exc:
        raise SetupError("Invalid domain address.") from exc
    if parsed.scheme != "https" or parsed.username or parsed.password or port or parsed.query or parsed.fragment:
        raise SetupError("Enter a domain or HTTPS URL without a port, credentials, query or fragment.")
    if parsed.path not in ("", "/", "/admin", "/admin/") or not parsed.hostname:
        raise SetupError("Example domain: admin.example.com")
    try:
        domain = parsed.hostname.rstrip(".").encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise SetupError("Invalid domain name.") from exc
    labels = domain.split(".")
    if (
        len(domain) > 253
        or len(labels) < 2
        or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels)
        or labels[-1].isdigit()
    ):
        raise SetupError("Enter a valid public domain; IP addresses and localhost are not supported.")
    if labels[-1] in {"localhost", "local", "internal", "invalid", "test", "example"}:
        raise SetupError("The domain must be public and eligible for a certificate.")
    return domain


def api_port(content):
    values = re.findall(r"(?m)^[ \t]*(?:export[ \t]+)?FASTAPI_PORT[ \t]*=[ \t]*([^\n]*)", content)
    if len(values) > 1:
        raise SetupError("FASTAPI_PORT is defined more than once in .env; resolve the duplicates first.")
    value = values[0].strip() if values else ""
    # Only simple numeric dotenv values are supported; never evaluate shell text.
    value = re.sub(r"\s+#.*$", "", value).strip().strip("\"'")
    if not value:
        return 6160
    if not value.isascii() or not value.isdecimal() or not 1024 <= int(value) <= 65535:
        raise SetupError("FASTAPI_PORT must be a number from 1024 to 65535; do not blindly change the existing port.")
    return int(value)


def public_port(value):
    text = str(value).strip()
    if not text.isascii() or not text.isdecimal() or not 1 <= int(text) <= 65535:
        raise SetupError("HTTPS port must be a number from 1 to 65535.")
    port = int(text)
    if port == 80:
        raise SetupError("Port 80 is reserved for HTTP certificate validation; choose another HTTPS port.")
    return port


def https_origin(domain, https_port=443):
    port = public_port(https_port)
    return "https://" + domain + (f":{port}" if port != 443 else "")


def update_env(content, domain, port, https_port=443):
    values = {"FASTAPI_PORT": str(port), "ADMIN_MINI_APP_URL": https_origin(domain, https_port) + "/admin"}
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
            raise SetupError("Unsupported Nginx syntax for automatic editing.")
        cursor = match.end()
        value = match[0]
        if not value.isspace() and not value.startswith("#"):
            tokens.append((value.strip("\"'"), match.start(), match.end(), value in {"{", "}", ";"}))
    if cursor != len(text):
        raise SetupError("Unsupported Nginx syntax for automatic editing.")
    position = 0

    def parse(nested=False):
        nonlocal position
        nodes, words = [], []
        while position < len(tokens):
            value, start, end, control = tokens[position]
            position += 1
            if control and value == "}":
                if not nested or words:
                    raise SetupError("Invalid or complex Nginx structure.")
                return nodes, start, end
            if control and value in (";", "{"):
                if not words:
                    raise SetupError("Unsupported Nginx structure.")
                node = {"name": words[0], "args": words[1:], "children": None, "close": None}
                if value == "{":
                    node["children"], node["close"], _ = parse(True)
                nodes.append(node)
                words = []
            else:
                words.append(value)
        if nested or words:
            raise SetupError("Incomplete Nginx structure.")
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
            raise SetupError("Dynamic or invalid include; automatic setup stopped.")
        pattern = Path(node["args"][0])
        if not pattern.is_absolute():
            pattern = NGINX_ROOT / pattern
        matches = {Path(p).resolve() for p in glob.glob(str(pattern))}
        if pattern.resolve() in files:
            matches.add(pattern.resolve())
        for path in matches:
            if path in seen or path not in files:
                raise SetupError("Complex include or file outside the loaded configuration; automatic setup stopped.")
            result.extend(expanded_includes(nginx_nodes(files[path]), files, snippet, (*seen, path)))
    return result


def existing_server(files, domain, snippet, https_port=443):
    """Return one explicit HTTPS server. Refuse aliases/regex/collisions, never replace them."""
    https_port = public_port(https_port)
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
                raise SetupError("The domain shares a virtual host with other names; use a dedicated host.")
            # Reuse only a TLS server actually listening on the requested port.
            # IPv4, IPv6 and wildcard TCP endpoints are supported, never Unix sockets.
            tls = any(
                child["name"] == "listen"
                and "ssl" in child["args"]
                and not child["args"][0].startswith("unix:")
                and child["args"][0].rsplit(":", 1)[-1] == str(https_port)
                for child in children
            )
            found.append((path, text, node, tls))
    if not found:
        if ambiguous:
            raise SetupError(
                "The domain may conflict with an existing wildcard/regex host; use an explicit dedicated host."
            )
        return None
    targets = [row for row in found if row[3]]
    if len(targets) != 1:
        raise SetupError(
            "The domain exists in Nginx but has no unique HTTPS host on the selected port. Use a dedicated subdomain or configure the proxy manually."
        )
    path, text, node, _ = targets[0]
    children = expanded_includes(node["children"], files, snippet)
    for child in children:
        if child["name"] in {"return", "rewrite", "auth_basic", "auth_request", "ssl_verify_client"}:
            raise SetupError("The existing host has server-wide restrictions or redirects; use a dedicated subdomain.")
    for child in descendants(children):
        if child["name"] == "location" and any("admin" in arg.lower() for arg in child["args"]):
            raise SetupError("An admin route already exists on this domain; use a new subdomain to avoid conflicts.")
    if any(child["name"] == "include" and child["args"] == [str(snippet)] for child in node["children"]):
        return path, text
    return path, text[: node["close"]] + f"\n    include {snippet};\n" + text[node["close"] :]


def route_config(host_port, probe_path, proof):
    proxy = (
        f"        proxy_pass http://127.0.0.1:{host_port};\n"
        "        proxy_set_header Host $http_host;\n"
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


def vhost(domain, webroot, snippet, cert_name=None, https_port=443):
    https_port = public_port(https_port)
    origin = https_origin(domain, https_port)
    challenge = f"    location ^~ /.well-known/acme-challenge/ {{ root {webroot}; }}\n"
    http = MARKER + f"\nserver {{\n    listen 80;\n    listen [::]:80;\n    server_name {domain};\n" + challenge
    if cert_name is None:
        return http + "    location / { return 404; }\n}\n"
    cert = "/etc/letsencrypt/live/" + cert_name
    return (
        http + f"    location / {{ return 301 {origin}$request_uri; }}\n}}\n"
        f"server {{\n    listen {https_port} ssl;\n    listen [::]:{https_port} ssl;\n    server_name {domain};\n"
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
            raise SetupError("The target file is a symlink; stopped for safety.")
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


def install_mode(root):
    marker = root / ".install_mode"
    if marker.exists():
        mode = marker.read_text().strip()
    elif (root / "docker-compose.yml").is_file():
        mode = "docker"
    elif (root / "app/main.py").is_file():
        mode = "native"
    else:
        mode = ""
    if mode not in {"docker", "native"}:
        raise SetupError("Unknown installation mode; check .install_mode against the actual installation.")
    return mode


def cgroup_contains(text, group):
    if not group or group == "/":
        return False
    return any(
        len(parts := line.split(":", 2)) == 3 and (parts[2] == group or parts[2].startswith(group + "/"))
        for line in text.splitlines()
    )


class Setup:
    def __init__(self, domain, root=ROOT, https_port=443):
        self.domain, self.root = domain, root
        self.https_port = public_port(https_port)
        self.origin = https_origin(domain, self.https_port)
        self.env = root / ".env"
        self.mode = "docker"
        self.unit = "pasarguardbot.service"
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
            raise SetupError(f"Command {args[0]} failed. Check the private server log.")
        return result

    def preflight(self):
        if os.geteuid() != 0:
            raise SetupError("Run this tool with sudo.")
        if not shutil.which("apt-get") or not Path("/run/systemd/system").is_dir():
            raise SetupError("Automatic setup supports Docker or Native on Debian/Ubuntu with systemd.")
        if not self.env.is_file() or self.env.is_symlink():
            raise SetupError("The standard installation .env was not found in /opt/pasarguardbot.")
        self.mode = install_mode(self.root)
        if self.root.is_symlink() or self.root.stat().st_uid != 0 or self.root.stat().st_mode & 0o022:
            raise SetupError("The installation directory must be root-owned and not writable by others.")
        if self.state.is_symlink() or (self.state.exists() and self.state.stat().st_uid != 0):
            raise SetupError("The log/backup directory is not safe to write.")
        self.content = self.env.read_text()
        self.port = api_port(self.content)
        addresses = {row[4][0] for row in socket.getaddrinfo(self.domain, self.https_port, type=socket.SOCK_STREAM)}
        if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
            raise SetupError("DNS must resolve publicly to this server; private IP addresses are not accepted.")
        if self.mode == "native":
            self.preflight_native()
        else:
            self.preflight_docker()
        self.check_public_port()
        if not Path(self.nginx).is_file():
            self.check_listener(80)
        else:
            if self.run(["systemctl", "is-active", "nginx"], check=False).returncode:
                raise SetupError(
                    "Existing Nginx is inactive; inspect it first to avoid activating unknown configuration."
                )
            self.run([self.nginx, "-t"])

    def check_listener(self, port):
        listening = self.run(["ss", "-H", "-ltnp", f"sport = :{port}"]).stdout
        if listening and (
            not Path(self.nginx).is_file() or any('"nginx"' not in line for line in listening.splitlines())
        ):
            raise SetupError(
                f"Port {port} is owned by another application. Nothing was stopped; "
                "choose another HTTPS port or configure routing in the existing proxy. "
                "HTTP-01 certificate validation still requires port 80."
            )

    def check_public_port(self):
        if self.https_port in {self.port, self.host_port}:
            raise SetupError("The public HTTPS port must differ from the bot API and its host-mapped port.")
        self.check_listener(self.https_port)

    def allow_firewall(self, ports):
        if shutil.which("ufw") and "Status: active" in self.run(["ufw", "status"]).stdout:
            for port in ports:
                self.run(["ufw", "allow", f"{port}/tcp"])

    def preflight_docker(self):
        if not (self.root / "docker-compose.yml").is_file():
            raise SetupError("The Docker Compose file was not found.")
        if self.run(["docker", "inspect", "pasarguardbot", "--format", "{{.State.Running}}"]).stdout.strip() != "true":
            raise SetupError("Start the version 3 bot first; this tool does not install the bot.")
        self.run(["docker", "exec", "pasarguardbot", "test", "-f", "/app/app/assets/admin_app/app.js"])
        self.image = self.run(["docker", "inspect", "pasarguardbot", "--format", "{{.Image}}"]).stdout.strip()
        self.check_image()
        ports = json.loads(
            self.run(["docker", "inspect", "pasarguardbot", "--format", "{{json .NetworkSettings.Ports}}"]).stdout
        )
        bindings = ports.get(f"{self.port}/tcp") or []
        local = [b for b in bindings if b.get("HostIp") in ("127.0.0.1", "0.0.0.0", "")]
        if len(local) != 1 or not str(local[0]["HostPort"]).isdigit():
            raise SetupError(
                "The bot API port is not mapped to the host loopback; inspect custom Compose settings manually."
            )
        self.host_port = int(local[0]["HostPort"])

    def native_unit(self, require_active=False):
        result = self.run(
            [
                "systemctl",
                "show",
                self.unit,
                "--no-pager",
                "--property=ActiveState,WorkingDirectory,EnvironmentFiles,ControlGroup",
            ]
        ).stdout
        properties = dict(line.split("=", 1) for line in result.splitlines() if "=" in line)
        workdir = properties.get("WorkingDirectory", "")
        env_files = properties.get("EnvironmentFiles", "")
        if not workdir or Path(workdir).resolve() != (self.root / "app").resolve():
            raise SetupError(
                "Native WorkingDirectory does not match the standard installation; the unit was not changed."
            )
        if env_files not in {str(self.env) + " (ignore_errors=yes)", str(self.env) + " (ignore_errors=no)"}:
            raise SetupError(
                "Native EnvironmentFile must use the installation .env; custom settings are not overwritten automatically."
            )
        if require_active and properties.get("ActiveState") != "active":
            raise SetupError("First ensure pasarguardbot.service is healthy and active.")
        if not (self.root / "app/app/assets/admin_app/app.js").is_file():
            raise SetupError(
                "Native source has no Mini App assets yet; run Update bot from main first, not a reinstall."
            )
        return properties

    def preflight_native(self):
        properties = self.native_unit(require_active=True)
        self.host_port = self.port
        listening = self.run(["ss", "-H", "-ltnp", f"sport = :{self.port}"]).stdout
        for line in listening.splitlines():
            pids = set(re.findall(r"pid=(\d+)", line))
            if not pids:
                raise SetupError("Cannot identify the API port owner; no other service was stopped.")
            for pid in pids:
                try:
                    groups = Path(f"/proc/{pid}/cgroup").read_text()
                except OSError as exc:
                    raise SetupError("The API port owner changed; try again.") from exc
                if not cgroup_contains(groups, properties.get("ControlGroup", "")):
                    raise SetupError(
                        "Another service owns the API port; neither the port nor that service was changed."
                    )

    def check_image(self):
        # Recreating for env must never silently upgrade to a newly pulled dev image.
        config = json.loads(self.run([*self.compose, "config", "--format", "json"]).stdout)
        reference = config["services"]["bot"].get("image")
        if not isinstance(reference, str) or not reference:
            raise SetupError("The bot image is not defined in Compose.")
        current = self.run(["docker", "image", "inspect", reference, "--format", "{{.Id}}"]).stdout.strip()
        if current != self.image:
            raise SetupError("The local image differs from the running bot; finish the bot upgrade first.")

    def recreate_bot(self):
        if self.mode == "native":
            # EnvironmentFile is reread on restart; do not rewrite units or restart stores.
            self.native_unit()
            self.bot_touched = True
            self.run(["systemctl", "restart", self.unit], timeout=180)
            return
        self.check_image()
        self.bot_touched = True
        self.run([*self.compose, "up", "-d", "--no-deps", "--force-recreate", "--pull", "never", "bot"], timeout=180)

    def stop_bot(self):
        command = ["systemctl", "stop", self.unit] if self.mode == "native" else ["docker", "stop", "pasarguardbot"]
        self.run(command, check=False)

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
        raise SetupError("URL verification failed; check DNS, trusted HTTPS and public port access.")

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
                print("Installing Nginx...", flush=True)
                self.new_nginx = True
                self.run(["apt-get", "update"], timeout=600)
                self.run(["apt-get", "install", "-y", "--no-install-recommends", "nginx"], timeout=600)
                self.run(["systemctl", "enable", "--now", "nginx"])
            files = self.loaded_files()
            for managed in (snippet, site):
                if managed.exists() and not managed.read_text().startswith(MARKER):
                    raise SetupError("An existing file with the same name is not owned by this tool; not overwritten.")
            own_site = site.exists()
            existing = None if own_site else existing_server(files, self.domain, snippet, self.https_port)
            if existing and NGINX_ROOT not in existing[0].parents:
                raise SetupError("The Nginx host file is outside /etc/nginx; not edited automatically.")
            if not existing:
                self.check_listener(80)
            self.journal.write(snippet, route_config(self.host_port, probe_path, proof))
            if existing:
                print("Reusing existing HTTPS; other routes on the domain are preserved...", flush=True)
                path, content = existing
                if path.read_text() != files[path]:
                    raise SetupError("Nginx configuration changed concurrently; not overwritten.")
                self.journal.write(path, content, stat.S_IMODE(path.stat().st_mode))
            else:
                webroot.mkdir(mode=0o755, parents=True, exist_ok=True)
                self.journal.write(site, vhost(self.domain, webroot, snippet, https_port=self.https_port))
                self.reload()
                if site.resolve() not in self.loaded_files():
                    raise SetupError("Nginx does not load conf.d; the main configuration was not forcibly changed.")
                self.allow_firewall((80, self.https_port))
                if not shutil.which("certbot"):
                    self.run(["apt-get", "update"], timeout=600)
                    self.run(["apt-get", "install", "-y", "--no-install-recommends", "certbot"], timeout=600)
                print("Obtaining/checking a free Let's Encrypt certificate; DNS must reach this server...", flush=True)
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
                self.journal.write(site, vhost(self.domain, webroot, snippet, cert_name, self.https_port))
            if existing:
                self.allow_firewall((self.https_port,))
            self.reload()
            self.probe(self.origin + probe_path, proof, attempts=3)
            if self.env.read_text() != self.content:
                raise SetupError(".env changed concurrently; another administrator's changes were not overwritten.")
            self.journal.write(
                self.env,
                update_env(self.content, self.domain, self.port, self.https_port),
                mode=stat.S_IMODE(self.env.stat().st_mode),
            )
            print(
                f"Applying settings for {self.mode}; restarting only the bot, without image pulls, source upgrades or data deletion...",
                flush=True,
            )
            self.recreate_bot()
            self.probe(f"http://127.0.0.1:{self.host_port}/admin/assets/app.js", "Telegram", attempts=30)
            self.probe(self.origin + "/admin", "/admin/assets/app.js", attempts=3)
            # Remove the temporary proof endpoint after verification.
            clean = route_config(self.host_port, probe_path, proof)
            clean = "\n".join(line for line in clean.splitlines() if probe_path not in line) + "\n"
            self.journal.write(snippet, clean)
            self.reload()
        except BaseException:
            print("Setup did not complete; restoring configuration files...", flush=True)
            conflicts = self.journal.restore()
            try:
                if self.new_nginx:
                    self.run(["systemctl", "disable", "--now", "nginx"], check=False)
                else:
                    self.reload()
                if self.bot_touched:
                    if conflicts:
                        self.stop_bot()
                    else:
                        self.recreate_bot()
                if conflicts:
                    print(
                        "File restore conflict or error; concurrent edits were preserved. Manual review is required.",
                        file=sys.stderr,
                    )
            except Exception:
                print(
                    "Service rollback did not complete; check the bot/Nginx status and private backup.", file=sys.stderr
                )
            print(f"Private backup and log: {self.journal.directory}", file=sys.stderr)
            print(
                "Installed packages, certificates and added UFW rules are not removed. No database restore was performed.",
                file=sys.stderr,
            )
            raise
        print(f"\nReady: {self.origin}/admin\nSend /adminapp in a private chat with the bot using the owner account.")
        print("Bot data was not deleted. Do not expose the API/database directly to the public Internet.")
        print(f"Configuration backup and private log: {self.journal.directory}")


def main():
    parser = argparse.ArgumentParser(description="Automatic Mini App setup for Docker/Native on Debian/Ubuntu")
    parser.add_argument("domain", nargs="?", help="Example: admin.example.com")
    parser.add_argument(
        "--https-port", metavar="PORT", help="Public HTTPS port (default: 443; separate from the bot API port)"
    )
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("Run with sudo.")
    print(
        "DNS must point to this server. Allow the chosen HTTPS port publicly; new certificates require inbound port 80 for HTTP-01 issuance and renewal."
    )
    print(
        "Compatible existing Nginx HTTPS is preserved; when needed, Let's Encrypt terms are accepted and a certificate is requested without email."
    )
    print("The bot will restart briefly; take a backup first. This tool does not change DNS or other control panels.")
    try:
        domain = domain_name(args.domain or input("Mini App domain: "))
        # Interactive users choose a port; scripted domain-only calls keep the old default.
        value = args.https_port
        if value is None:
            value = input("HTTPS port [443]: ") if sys.stdin.isatty() else "443"
            value = value.strip() or "443"
        https_port = public_port(value)
        print(f"Mini App URL: {https_origin(domain, https_port)}/admin", flush=True)
        ROOT.mkdir(mode=0o755, parents=True, exist_ok=True)
        with (ROOT / ".miniapp-setup.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise SetupError("Another setup is already running.") from exc
            Setup(domain, https_port=https_port).execute()
    except (SetupError, OSError, ValueError, EOFError, subprocess.SubprocessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Cancelled.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
