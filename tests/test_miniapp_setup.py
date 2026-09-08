"""Host-side setup regressions: no real Docker, DNS, packages or ACME requests."""

import ast
import json
import re
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
    def __init__(self, root, nginx_root, *, existing=True, fail=None, https_port=443):
        super().__init__("admin.example.com", root, https_port=https_port)
        self.nginx = str(nginx_root / "nginx")
        Path(self.nginx).touch()
        self.original_site = nginx_root / "site.conf"
        if existing:
            self.original_site.write_text(
                site("location /payment { return 200 'preserved'; }").replace(
                    "listen 443 ssl;", f"listen {https_port} ssl;"
                )
            )
        self.commands, self.probes = [], []
        self.fail = fail
        self.cert_requested = False

    def preflight(self):
        self.content = self.env.read_text()
        self.port = m.api_port(self.content)
        self.host_port = self.port
        self.image = "sha256:synthetic"
        self.check_public_port()

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
    assert "11) action_miniapp" in script and 'miniapp)        action_miniapp "${@:2}"' in script


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


@pytest.mark.parametrize("mode", ["native", "docker"])
def test_install_mode_marker_wins_over_old_files(tmp_path, mode):
    (tmp_path / ".install_mode").write_text(mode + "\n")
    (tmp_path / "docker-compose.yml").touch()
    assert m.install_mode(tmp_path) == mode


def test_install_mode_native_fallback_and_invalid_marker(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app/main.py").touch()
    assert m.install_mode(tmp_path) == "native"
    (tmp_path / ".install_mode").write_text("unexpected")
    with pytest.raises(m.SetupError):
        m.install_mode(tmp_path)


@pytest.mark.parametrize(
    "text,group,expected",
    [
        ("0::/system.slice/pasarguardbot.service", "/system.slice/pasarguardbot.service", True),
        ("1:name=systemd:/system.slice/pasarguardbot.service/child", "/system.slice/pasarguardbot.service", True),
        ("0::/system.slice/other.service", "/system.slice/pasarguardbot.service", False),
        ("0::/system.slice/pasarguardbot.service-other", "/system.slice/pasarguardbot.service", False),
        ("0::/anything", "/", False),
        ("garbage", "", False),
    ],
)
def test_native_port_ownership_cgroup_boundaries(text, group, expected):
    assert m.cgroup_contains(text, group) is expected


class FakeNative(FakeSetup):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mode = "native"
        self.service_state = "active"
        self.restarts = 0
        self.unit_overrides = {}
        self.listener = ""
        asset = self.root / "app/app/assets/admin_app/app.js"
        asset.parent.mkdir(parents=True)
        asset.write_text("synthetic asset")

    def run(self, args, timeout=120, check=True):
        result = super().run(args, timeout, check)
        if args[:2] == ["systemctl", "show"]:
            props = {
                "ActiveState": self.service_state,
                "WorkingDirectory": str(self.root / "app"),
                "EnvironmentFiles": str(self.env) + " (ignore_errors=yes)",
                "ControlGroup": "/system.slice/pasarguardbot.service",
                **self.unit_overrides,
            }
            result.stdout = "\n".join(f"{key}={value}" for key, value in props.items())
        if args[:2] == ["systemctl", "restart"]:
            self.restarts += 1
            if self.fail == "restart_first" and self.restarts == 1:
                self.service_state = "failed"
                raise m.SetupError("synthetic native start failure")
            self.service_state = "active"
        if args[0] == "ss":
            result.stdout = self.listener
        return result


def test_native_setup_uses_only_bot_unit_not_docker_or_stores(host):
    setup = FakeNative(*host)
    setup.execute()
    assert setup.restarts == 1
    assert "ADMIN_MINI_APP_URL=https://admin.example.com/admin" in setup.env.read_text()
    assert not any(args[0] in {"docker", "git", "uv"} for args in setup.commands)
    mutations = [args for args in setup.commands if args[0] == "systemctl" and args[1] in {"restart", "stop"}]
    assert mutations == [["systemctl", "restart", "pasarguardbot.service"]]


@pytest.mark.parametrize("failure", ["backend", "restart_first"])
def test_native_failed_restart_or_health_restores_env_and_restarts_failed_unit(host, failure):
    setup = FakeNative(*host, fail=failure)
    old_env, old_site = setup.env.read_bytes(), setup.original_site.read_bytes()
    with pytest.raises(m.SetupError):
        setup.execute()
    assert setup.env.read_bytes() == old_env and setup.original_site.read_bytes() == old_site
    assert setup.restarts == 2 and setup.service_state == "active"
    assert not any(args[0] == "docker" for args in setup.commands)


@pytest.mark.parametrize(
    "override",
    [
        {"WorkingDirectory": "/another/app"},
        {"EnvironmentFiles": "/another/.env (ignore_errors=yes)"},
        {"EnvironmentFiles": "/another/.env (ignore_errors=no) /opt/pasarguardbot/.env (ignore_errors=yes)"},
        {"ActiveState": "failed"},
    ],
)
def test_native_custom_or_inactive_unit_refused_before_changes(host, override):
    setup = FakeNative(*host)
    setup.unit_overrides = override
    with pytest.raises(m.SetupError):
        setup.native_unit(require_active=True)
    assert setup.restarts == 0 and not setup.state.exists()


def test_native_old_source_refused_before_changes(host):
    setup = FakeNative(*host)
    (setup.root / "app/app/assets/admin_app/app.js").unlink()
    with pytest.raises(m.SetupError):
        setup.native_unit(require_active=True)
    assert setup.restarts == 0


@pytest.mark.parametrize("owned", [True, False])
def test_native_existing_listener_must_belong_to_unit(host, monkeypatch, owned):
    setup = FakeNative(*host)
    setup.preflight()
    setup.listener = 'LISTEN 0 128 0.0.0.0:8123 0.0.0.0:* users:(("python",pid=4242,fd=3))'
    real_path = m.Path
    monkeypatch.setattr(
        m,
        "Path",
        lambda value: (
            SimpleNamespace(
                read_text=lambda: "0::/system.slice/" + ("pasarguardbot.service" if owned else "other.service")
            )
            if str(value) == "/proc/4242/cgroup"
            else real_path(value)
        ),
    )
    if owned:
        setup.preflight_native()
        assert setup.host_port == 8123
    else:
        with pytest.raises(m.SetupError):
            setup.preflight_native()
    assert setup.restarts == 0


def test_native_stops_only_bot_on_rollback_conflict(host):
    setup = FakeNative(*host)
    setup.stop_bot()
    assert setup.commands == [["systemctl", "stop", "pasarguardbot.service"]]


@pytest.mark.parametrize(
    "value", ["0", "65536", "-1", "80", "abc", "443;shutdown", "8443\nallow all", "۴۴۳", "", True, "8443.0"]
)
def test_invalid_https_port_fails_closed(value):
    with pytest.raises(m.SetupError):
        m.public_port(value)


@pytest.mark.parametrize(
    "value,expected", [(443, 443), ("8443", 8443), (" 9443 ", 9443), ("65535", 65535), ("00443", 443)]
)
def test_https_port_validation(value, expected):
    assert m.public_port(value) == expected


def test_custom_port_env_is_idempotent_and_api_unchanged():
    original = "BOT_TOKEN=synthetic\nFASTAPI_PORT=8123\nADMIN_MINI_APP_URL=https://old.example.com/admin\n"
    changed = m.update_env(original, "admin.example.com", 8123, 8443)
    assert "ADMIN_MINI_APP_URL=https://admin.example.com:8443/admin\n" in changed
    assert "BOT_TOKEN=synthetic\n" in changed and m.api_port(changed) == 8123
    assert m.update_env(changed, "admin.example.com", 8123, 8443) == changed
    assert "https://admin.example.com/admin" in m.update_env(changed, "admin.example.com", 8123)


def test_custom_vhost_listeners_redirect_and_acme(tmp_path):
    config = m.vhost("admin.example.com", tmp_path, tmp_path / "snippet", "cert", 8443)
    assert "listen 8443 ssl;" in config and "listen [::]:8443 ssl;" in config
    assert "listen 443" not in config and "listen [::]:443" not in config
    assert "return 301 https://admin.example.com:8443$request_uri;" in config
    assert "listen 80;" in config and "location ^~ /.well-known/acme-challenge/" in config
    assert "ssl_certificate /etc/letsencrypt/live/cert/fullchain.pem;" in config
    assert "proxy_set_header Host $http_host;" in m.route_config(8123, "/admin/proof", "proof")
    m.nginx_nodes(config)


@pytest.mark.parametrize("listen", ["8443", "[::]:8443", "127.0.0.1:8443", "*:8443"])
def test_existing_https_matches_selected_port_only(tmp_path, listen):
    old = site().replace("listen 443 ssl;", f"listen {listen} ssl;")
    path = tmp_path / "site.conf"
    snippet = tmp_path / "snippet"
    assert m.existing_server({path: old}, "admin.example.com", snippet, 8443)[0] == path
    with pytest.raises(m.SetupError):
        m.existing_server({path: old}, "admin.example.com", snippet, 443)


def test_same_domain_other_port_is_not_edited(tmp_path):
    old443 = site("location /admin { return 404; }")
    old8443 = site("location /payment { return 200 'keep'; }").replace("listen 443 ssl;", "listen 8443 ssl;")
    path, snippet = tmp_path / "site", tmp_path / "snippet"
    result = m.existing_server({path: old443 + old8443}, "admin.example.com", snippet, 8443)[1]
    assert result.startswith(old443)
    assert result.replace(f"\n    include {snippet};\n", "") == old443 + old8443


@pytest.mark.parametrize("factory", [FakeSetup, FakeNative])
@pytest.mark.parametrize("existing", [True, False])
def test_custom_https_setup_on_both_install_modes(host, monkeypatch, capsys, factory, existing):
    monkeypatch.setattr(m.shutil, "which", lambda name: "/synthetic/" + name)
    setup = factory(*host, existing=existing, https_port=8443)
    original_run = setup.run

    def run(args, **kwargs):
        result = original_run(args, **kwargs)
        if args == ["ufw", "status"]:
            result.stdout = "Status: active"
        return result

    setup.run = run
    setup.execute()
    assert "ADMIN_MINI_APP_URL=https://admin.example.com:8443/admin" in setup.env.read_text()
    assert m.api_port(setup.env.read_text()) == 8123
    assert all(url.startswith("https://admin.example.com:8443/") for url in setup.probes if url.startswith("https:"))
    assert ["ufw", "allow", "8443/tcp"] in setup.commands
    assert ["ufw", "allow", "443/tcp"] not in setup.commands
    assert (["ufw", "allow", "80/tcp"] in setup.commands) is (not existing)
    assert not any(args[0] == "ss" and args[-1] == "sport = :443" for args in setup.commands)
    if existing:
        assert not setup.cert_requested
        assert not any(args[0] == "ss" and args[-1] == "sport = :80" for args in setup.commands)
    else:
        config = next((host[1] / "conf.d").glob("*.conf")).read_text()
        assert "listen 8443 ssl;" in config and "listen 443 ssl;" not in config
    output = capsys.readouterr()
    assert "Ready: https://admin.example.com:8443/admin" in output.out
    assert not re.search("[\u0600-\u06ff]", output.out + output.err)


@pytest.mark.parametrize("factory", [FakeSetup, FakeNative])
def test_custom_port_rollback_restores_previous_url(host, factory):
    setup = factory(*host, https_port=8443, fail="backend")
    setup.env.write_text(setup.env.read_text() + "ADMIN_MINI_APP_URL=https://admin.example.com:9443/admin\n")
    old_env = setup.env.read_bytes()
    old_site = setup.original_site.read_bytes()
    with pytest.raises(m.SetupError):
        setup.execute()
    assert setup.env.read_bytes() == old_env and setup.original_site.read_bytes() == old_site


@pytest.mark.parametrize("https_port,api_port,host_port", [(8123, 8123, 6160), (6160, 8123, 6160)])
def test_public_port_cannot_collide_with_api(host, https_port, api_port, host_port):
    setup = FakeSetup(*host, https_port=https_port)
    setup.port, setup.host_port = api_port, host_port
    with pytest.raises(m.SetupError, match="must differ"):
        setup.check_public_port()
    assert not setup.commands


def test_occupied_443_is_irrelevant_to_custom_https_port(host):
    setup = FakeSetup(*host, https_port=8443)
    setup.port = setup.host_port = 8123
    commands = []

    def run(args, **kwargs):
        commands.append(args)
        return SimpleNamespace(stdout='LISTEN users:(("other",pid=1,fd=3))' if args[-1] == "sport = :443" else "")

    setup.run = run
    setup.check_public_port()
    assert commands == [["ss", "-H", "-ltnp", "sport = :8443"]]


def test_occupied_selected_port_refused_before_files_change(host):
    setup = FakeSetup(*host, https_port=8443)
    setup.run = lambda *args, **kwargs: SimpleNamespace(stdout='LISTEN users:(("other",pid=1,fd=3))')
    old_env = setup.env.read_bytes()
    with pytest.raises(m.SetupError, match="Port 8443"):
        setup.execute()
    assert setup.env.read_bytes() == old_env and not setup.state.exists()


@pytest.mark.parametrize(
    "argv,interactive,answers,expected",
    [
        (["setup"], True, ["admin.example.com", "8443"], 8443),
        (["setup"], True, ["admin.example.com", ""], 443),
        (["setup", "admin.example.com", "--https-port", "9443"], True, [], 9443),
        (["setup", "admin.example.com"], False, [], 443),
    ],
)
def test_cli_port_prompt_and_explicit_flag(tmp_path, monkeypatch, capsys, argv, interactive, answers, expected):
    monkeypatch.setattr(m.sys, "argv", argv)
    monkeypatch.setattr(m.sys.stdin, "isatty", lambda: interactive)
    monkeypatch.setattr(m.os, "geteuid", lambda: 0)
    monkeypatch.setattr(m, "ROOT", tmp_path / "bot")
    prompts, calls = [], []
    iterator = iter(answers)

    def ask(prompt):
        prompts.append(prompt)
        return next(iterator)

    monkeypatch.setattr("builtins.input", ask)
    monkeypatch.setattr(
        m, "Setup", lambda domain, https_port: SimpleNamespace(execute=lambda: calls.append((domain, https_port)))
    )
    assert m.main() == 0
    assert calls == [("admin.example.com", expected)]
    assert ("HTTPS port [443]: " in prompts) == bool(answers)
    output = capsys.readouterr()
    assert not re.search("[\u0600-\u06ff]", output.out + output.err + "".join(prompts))


def test_invalid_cli_port_reports_english_error_without_changes(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(m.sys, "argv", ["setup", "admin.example.com", "--https-port", "80"])
    monkeypatch.setattr(m.os, "geteuid", lambda: 0)
    monkeypatch.setattr(m, "ROOT", tmp_path / "bot")
    assert m.main() == 1
    assert not (tmp_path / "bot").exists()
    assert "Error: Port 80 is reserved" in capsys.readouterr().err


def test_setup_terminal_scripts_have_no_persian_messages():
    root = Path(__file__).resolve().parents[1]
    for name in ("setup_miniapp.py", "setup-miniapp.sh", "pasarguardbot.sh"):
        assert not re.search("[\u0600-\u06ff]", (root / "scripts" / name).read_text())
    manager = (root / "scripts/pasarguardbot.sh").read_text()
    assert 'bash "$tmp" "$@"' in manager
    assert "--https-port PORT" in manager


def test_managed_site_port_change_reuses_file_and_certificate_name(host):
    first = FakeSetup(*host, existing=False)
    first.execute()
    old_site = next((host[1] / "conf.d").glob("*.conf"))
    original_cert = next(args for args in first.commands if args[0] == "certbot")
    second = FakeSetup(*host, existing=False, https_port=8443)
    second.execute()
    assert list((host[1] / "conf.d").glob("*.conf")) == [old_site]
    assert "listen 8443 ssl;" in old_site.read_text() and "listen 443 ssl;" not in old_site.read_text()
    assert "https://admin.example.com:8443/admin" in second.env.read_text()
    new_cert = next(args for args in second.commands if args[0] == "certbot")
    assert original_cert[original_cert.index("--cert-name") + 1] == new_cert[new_cert.index("--cert-name") + 1]


def test_foreign_http_port_prevents_new_certificate_without_env_changes(host):
    setup = FakeSetup(*host, existing=False, https_port=8443)
    original = setup.env.read_bytes()
    real_run = setup.run

    def run(args, **kwargs):
        result = real_run(args, **kwargs)
        if args[0] == "ss" and args[-1] == "sport = :80":
            result.stdout = 'LISTEN users:(("other",pid=1,fd=3))'
        return result

    setup.run = run
    with pytest.raises(m.SetupError, match="Port 80"):
        setup.execute()
    assert setup.env.read_bytes() == original and not setup.cert_requested
    assert not any(args[0] == "systemctl" and args[1] in {"stop", "restart"} for args in setup.commands)
