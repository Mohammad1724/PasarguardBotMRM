"""Host-side setup regressions: no real Docker, DNS, packages or ACME requests."""

import ast
import json
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import setup_miniapp as m


@pytest.mark.parametrize(
    "value", ["ADMIN.EXAMPLE.COM", "https://admin.example.com/admin", "admin.example.com/", "admin.example.com."]
)
def test_domain_normalization(value):
    assert m.domain_name(value) == "admin.example.com"


@pytest.mark.parametrize(
    "value",
    [
        "localhost",
        "127.0.0.1",
        "https://[::1]",
        "http://admin.example.com",
        "https://user:pass@admin.example.com",
        "admin.example.com:443",
        "admin.example.com:abc",
        "admin.example.com/x",
        "admin.example.com?token=secret",
        "admin.example.com#fragment",
        "a.invalid",
        "a.local",
        "-a.example.com",
        "a..example.com",
        "admin.example.com;touch /tmp/no",
        "a\n.example.com",
        "* .example.com",
        "x" * 64 + ".com",
    ],
)
def test_invalid_domain_no_configuration_injection(value):
    with pytest.raises(m.SetupError):
        m.domain_name(value)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("", 6160),
        ("FASTAPI_PORT=\n", 6160),
        ("FASTAPI_PORT=\nBOT_TOKEN=synthetic\n", 6160),
        ("FASTAPI_PORT=8123\n", 8123),
        ("FASTAPI_PORT='8123' # keep port\n", 8123),
        ("export FASTAPI_PORT=8123\n", 8123),
    ],
)
def test_existing_port_preserved(text, expected):
    assert m.api_port(text) == expected


@pytest.mark.parametrize(
    "text",
    ["FASTAPI_PORT=80\n", "FASTAPI_PORT=99999\n", "FASTAPI_PORT=foo\n", "FASTAPI_PORT=6160\nFASTAPI_PORT=8000\n"],
)
def test_invalid_port_fails_closed(text):
    with pytest.raises(m.SetupError):
        m.api_port(text)


def test_env_only_two_keys_change_and_idempotent():
    original = "# keep\nBOT_TOKEN='private:synthetic'\nMYSQL_PASSWORD='do not touch'\nFASTAPI_PORT=8123\nADMIN_MINI_APP_URL=\nexport ADMIN_MINI_APP_URL=old\n"
    updated = m.update_env(original, "admin.example.com", 8123)
    assert "BOT_TOKEN='private:synthetic'\nMYSQL_PASSWORD='do not touch'\n" in updated
    assert updated.count("ADMIN_MINI_APP_URL=") == 1
    assert m.api_port(updated) == 8123
    assert m.update_env(updated, "admin.example.com", 8123) == updated


def site(body="", domain="admin.example.com"):
    return f"server {{\n listen 443 ssl;\n server_name {domain};\n ssl_certificate /certificate.pem;\n {body}\n}}\n"


def test_existing_https_preserves_payments_cert_and_comments(tmp_path):
    path, snippet = tmp_path / "existing.conf", tmp_path / "new.conf"
    original = (
        "# literal braces { }\nserver {listen 80; server_name admin.example.com; return 301 https://$host$request_uri;}\n"
        + site('location /payments { proxy_pass http://127.0.0.1:9000; }\nlocation = /literal {return 200 "}";}')
    )
    target, result = m.existing_server({path: original}, "admin.example.com", snippet)
    assert target == path
    assert result.replace(f"\n    include {snippet};\n", "") == original
    assert m.existing_server({path: result}, "admin.example.com", snippet) == (path, result)


@pytest.mark.parametrize(
    "body",
    [
        "location /admin {return 404;}",
        "location /admin/api/ {proxy_pass http://other;}",
        'auth_basic "Protected";',
        "return 301 https://other.example.com;",
        "ssl_verify_client on;",
        "auth_request /auth;",
    ],
)
def test_conflicting_https_not_modified(tmp_path, body):
    with pytest.raises(m.SetupError):
        m.existing_server({tmp_path / "old": site(body)}, "admin.example.com", tmp_path / "snippet")


def test_conflict_in_included_file_or_after_owned_include_detected(tmp_path):
    snippet, external, main = tmp_path / "our.conf", tmp_path / "external.conf", tmp_path / "site.conf"
    external.write_text("location /admin/api/ {proxy_pass http://old;}")
    original = site(f"include {snippet}; include {external};")
    with pytest.raises(m.SetupError):
        m.existing_server({main: original, external: external.read_text()}, "admin.example.com", snippet)


def test_safe_certificate_include_allowed(tmp_path):
    cert = tmp_path / "ssl.conf"
    cert.write_text("ssl_protocols TLSv1.2 TLSv1.3;")
    main = tmp_path / "site.conf"
    original = site(f"include {cert};")
    assert (
        m.existing_server({main: original, cert: cert.read_text()}, "admin.example.com", tmp_path / "ours")[0] == main
    )


@pytest.mark.parametrize(
    "original",
    [
        "server { listen 80; server_name admin.example.com; }",
        site() + site(),
        site(domain="*.example.com"),
        site(domain="~^admin.*"),
    ],
)
def test_ambiguous_or_http_only_host_refused(tmp_path, original):
    with pytest.raises(m.SetupError):
        m.existing_server({tmp_path / "site": original}, "admin.example.com", tmp_path / "ours")


def test_new_domain_does_not_modify_unrelated_sites(tmp_path):
    assert (
        m.existing_server({tmp_path / "site": site(domain="other.example.com")}, "admin.example.com", tmp_path / "ours")
        is None
    )


def test_route_keeps_api_assets_prefix_and_no_proxy_cache():
    config = m.route_config(8123, "/admin/__setup_test", "proof")
    assert "location = /admin" in config and "location ^~ /admin/" in config
    assert config.count("proxy_pass http://127.0.0.1:8123;") == 2
    assert "proxy_pass http://127.0.0.1:8123/;" not in config
    assert "proxy_cache off;" in config
    assert "X-Forwarded-Proto $scheme" in config


def test_http_challenge_survives_https_redirect_and_tls_versions(tmp_path):
    http = m.vhost("admin.example.com", tmp_path, tmp_path / "snippet")
    https = m.vhost("admin.example.com", tmp_path, tmp_path / "snippet", "cert-name")
    assert "ssl_certificate" not in http
    assert "location ^~ /.well-known/acme-challenge/" in http
    assert "location ^~ /.well-known/acme-challenge/" in https
    assert "TLSv1.2 TLSv1.3" in https
    m.nginx_nodes(http)
    m.nginx_nodes(https)


def test_journal_private_backup_and_exact_rollback(tmp_path):
    env = tmp_path / ".env"
    original = b"BOT_TOKEN=synthetic\r\n# original\r\n"
    env.write_bytes(original)
    env.chmod(0o640)
    new = tmp_path / "new.conf"
    journal = m.Journal(tmp_path / "private")
    journal.write(env, "changed\n", 0o600)
    journal.write(env, "changed twice\n", 0o600)
    journal.write(new, "new\n")
    assert stat.S_IMODE(journal.directory.stat().st_mode) == 0o700
    assert (journal.directory / "1-.env").read_bytes() == original
    assert stat.S_IMODE((journal.directory / "1-.env").stat().st_mode) == 0o600
    journal.restore()
    assert env.read_bytes() == original and stat.S_IMODE(env.stat().st_mode) == 0o640
    assert not new.exists()


def test_journal_refuses_symlink(tmp_path):
    actual = tmp_path / "actual"
    actual.write_text("unchanged")
    link = tmp_path / "link"
    link.symlink_to(actual)
    with pytest.raises(m.SetupError):
        m.Journal(tmp_path / "private").write(link, "overwrite")
    assert actual.read_text() == "unchanged"


class FakeSetup(m.Setup):
    def __init__(self, root, nginx_root, *, existing=True, fail=None):
        super().__init__("admin.example.com", root)
        self.nginx = str(nginx_root / "nginx")
        Path(self.nginx).touch()
        self.original_site = nginx_root / "site.conf"
        if existing:
            self.original_site.write_text(site("location /payment { return 200 'preserved'; }"))
        self.commands, self.probes = [], []
        self.fail = fail
        self.cert_requested = False

    def preflight(self):
        self.content = self.env.read_text()
        self.port = m.api_port(self.content)
        self.host_port = self.port
        self.image = "sha256:synthetic"

    def run(self, args, timeout=120, check=True):
        self.commands.append(args)
        if args[0] == "certbot":
            self.cert_requested = True
            if self.fail == "cert":
                raise m.SetupError("synthetic ACME failure")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def check_image(self):
        pass

    def loaded_files(self):
        return {p: p.read_text() for p in Path(self.nginx).parent.rglob("*.conf")}

    def probe(self, url, expected=None, attempts=1):
        self.probes.append(url)
        if self.fail == "dns" or (self.fail == "backend" and url.startswith("http:")):
            raise m.SetupError("synthetic probe failure")


@pytest.fixture
def host(tmp_path, monkeypatch):
    root, nginx_root = tmp_path / "bot", tmp_path / "nginx"
    root.mkdir()
    nginx_root.mkdir()
    (root / ".env").write_text("BOT_TOKEN=synthetic-private\nFASTAPI_PORT=8123\n")
    monkeypatch.setattr(m, "NGINX_ROOT", nginx_root)
    # New-host tests must never create the real ACME webroot.
    real_path = m.Path
    monkeypatch.setattr(
        m,
        "Path",
        lambda value: tmp_path / "webroot" if str(value) == "/var/lib/pasarguardbot-acme" else real_path(value),
    )
    return root, nginx_root


def test_existing_https_full_setup_only_recreates_bot_and_keeps_cert(host):
    setup = FakeSetup(*host)
    original = setup.original_site.read_text()
    setup.execute()
    assert not setup.cert_requested
    assert original.replace("\n}", "") in setup.original_site.read_text().replace("\n}", "")
    assert "ADMIN_MINI_APP_URL=https://admin.example.com/admin" in setup.env.read_text()
    up = [args for args in setup.commands if "up" in args]
    assert len(up) == 1 and up[0][-1] == "bot"
    assert "--no-deps" in up[0] and up[0][-3:] == ["--pull", "never", "bot"]
    assert all("mariadb" not in args and "redis" not in args and "down" not in args for args in setup.commands)
    assert not any("__setup_" in p.read_text() for p in host[1].rglob("*.conf"))


@pytest.mark.parametrize("failure", ["dns", "backend"])
def test_failure_restores_env_routes_and_never_restores_database(host, failure):
    setup = FakeSetup(*host, fail=failure)
    old_env, old_site = setup.env.read_bytes(), setup.original_site.read_bytes()
    with pytest.raises(m.SetupError):
        setup.execute()
    assert setup.env.read_bytes() == old_env
    assert setup.original_site.read_bytes() == old_site
    assert not list((host[1] / "snippets").glob("*.conf"))
    up = [args for args in setup.commands if "up" in args]
    assert len(up) == (2 if failure == "backend" else 0)
    assert all(args[-1] == "bot" for args in up)


def test_new_host_issues_certificate_and_enables_timer(host, monkeypatch):
    monkeypatch.setattr(m.shutil, "which", lambda name: None)
    setup = FakeSetup(*host, existing=False)
    setup.execute()
    cert = next(args for args in setup.commands if args[0] == "certbot")
    assert "--webroot" in cert and "--non-interactive" in cert and "--keep-until-expiring" in cert
    assert cert[cert.index("-d") + 1] == "admin.example.com"
    assert ["systemctl", "enable", "--now", "certbot.timer"] in setup.commands
    assert len([args for args in setup.commands if "up" in args]) == 1


def test_certificate_failure_never_changes_env_or_recreates_bot(host, monkeypatch):
    monkeypatch.setattr(m.shutil, "which", lambda name: None)
    setup = FakeSetup(*host, existing=False, fail="cert")
    before = setup.env.read_bytes()
    with pytest.raises(m.SetupError):
        setup.execute()
    assert setup.env.read_bytes() == before
    assert not any("up" in args for args in setup.commands)
    assert not list((host[1] / "conf.d").glob("*.conf"))


def test_image_change_prevents_recreate(tmp_path):
    setup = m.Setup("admin.example.com", tmp_path)
    setup.image = "sha256:running"
    commands = []

    def run(args, **kwargs):
        commands.append(args)
        return SimpleNamespace(
            stdout=json.dumps({"services": {"bot": {"image": "registry:dev"}}}) if "config" in args else "sha256:new"
        )

    setup.run = run
    with pytest.raises(m.SetupError):
        setup.recreate_bot()
    assert not any("up" in args for args in commands)


def test_nonroot_preflight_does_not_run_commands(tmp_path, monkeypatch):
    monkeypatch.setattr(m.os, "geteuid", lambda: 123)
    setup = m.Setup("admin.example.com", tmp_path)
    with pytest.raises(m.SetupError):
        setup.preflight()
    assert not list(tmp_path.iterdir())


def test_host_helper_stays_python310_and_shell_syntax_valid():
    root = Path(__file__).resolve().parents[1]
    ast.parse((root / "scripts/setup_miniapp.py").read_text(), feature_version=(3, 10))
    for name in ("setup-miniapp.sh", "pasarguardbot.sh"):
        subprocess.run(["bash", "-n", str(root / "scripts" / name)], check=True)
    launcher = (root / "scripts/setup-miniapp.sh").read_text()
    assert "PASARGUARDBOT_SETUP_BRANCH:-main" in launcher
    script = (root / "scripts/pasarguardbot.sh").read_text()
    assert 'branch="${PASARGUARDBOT_SETUP_BRANCH:-main}"' in script
    assert "11) action_miniapp" in script and 'miniapp)        action_miniapp "${2:-}"' in script


def test_rollback_does_not_overwrite_concurrent_changes(tmp_path):
    file = tmp_path / "config"
    file.write_text("original")
    journal = m.Journal(tmp_path / "private")
    journal.write(file, "setup change")
    file.write_text("another admin's edit")
    assert journal.restore() == [file]
    assert file.read_text() == "another admin's edit"


def test_env_concurrent_edit_before_apply_is_preserved(host):
    setup = FakeSetup(*host)

    def probe(url, *args, **kwargs):
        setup.env.write_text("BOT_TOKEN=another-admin-value\n")

    setup.probe = probe
    with pytest.raises(m.SetupError):
        setup.execute()
    assert setup.env.read_text() == "BOT_TOKEN=another-admin-value\n"
    assert not any("up" in args for args in setup.commands)


def test_shared_https_aliases_are_not_changed(tmp_path):
    with pytest.raises(m.SetupError):
        m.existing_server(
            {tmp_path / "site": site(domain="admin.example.com other.example.com")},
            "admin.example.com",
            tmp_path / "ours",
        )
