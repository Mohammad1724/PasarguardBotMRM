"""Execute real manager functions with host actions stubbed and disposable paths."""

import re
import shlex
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "scripts/pasarguardbot.sh").read_text()


def function(name):
    return re.search(r"^" + name + r"\(\) \{.*?^\}", SOURCE, re.M | re.S)[0] + "\n"


def run(tmp_path, names, body, *, replacements=()):
    code = """set -euo pipefail
info() { printf 'INFO %s\\n' "$*"; }
ok() { printf 'OK %s\\n' "$*"; }
warn() { printf 'WARN %s\\n' "$*"; }
err() { printf 'ERROR %s\\n' "$*"; }
die() { printf 'FAIL %s\\n' "$*"; exit 1; }
get_installed_bot_version() { echo synthetic-version; }
set_install_branch() { :; }
set_install_mode() { :; }
"""
    for key, value in {
        "CONFIG_DIR": tmp_path,
        "RUN_DIR": tmp_path / "run",
        "APP_DIR": tmp_path / "app",
        "ENV_FILE": tmp_path / ".env",
        "CONF_DIR": tmp_path / "conf",
        "PHPMYADMIN_DIR": tmp_path / "pma",
        "PHPMYADMIN_PORT": 6163,
        "BOT_IMAGE": "synthetic-image",
    }.items():
        code += f"{key}={shlex.quote(str(value))}\n"
    code += "\n".join(function(name) for name in names)
    for old, new in replacements:
        code = code.replace(old, new)
    return subprocess.run(["bash", "-c", code + body], capture_output=True, text=True, timeout=10)


INIT_STUBS = """
id() { echo 1000; }
chown() { :; }
configure_native_mariadb_apparmor() { :; }
find_mariadb_install_db() { echo "$CONFIG_DIR/init"; }
"""


@pytest.mark.parametrize("kind", ["file", "directory", "hidden", "broken_symlink"])
def test_unknown_nonempty_datadir_never_deleted(tmp_path, kind):
    data = tmp_path / "data/mariadb"
    data.mkdir(parents=True)
    item = data / (".recoverable" if kind == "hidden" else "customer")
    if kind == "directory":
        item.mkdir()
        (item / "keep.ibd").write_bytes(b"recoverable data")
    elif kind == "broken_symlink":
        item.symlink_to(data / "missing")
    else:
        item.write_bytes(b"recoverable data")
    before = sorted(str(p.relative_to(data)) for p in data.rglob("*"))
    result = run(tmp_path, ["init_native_mariadb_datadir"], INIT_STUBS + "init_native_mariadb_datadir\n")
    assert result.returncode != 0 and "No data was removed" in result.stdout
    assert sorted(str(p.relative_to(data)) for p in data.rglob("*")) == before
    if kind in ("file", "hidden"):
        assert item.read_bytes() == b"recoverable data"


def test_symlink_datadir_refused_without_touching_target(tmp_path):
    target = tmp_path / "recoverable"
    target.mkdir()
    (target / "keep").write_text("data")
    (tmp_path / "data").mkdir()
    (tmp_path / "data/mariadb").symlink_to(target, target_is_directory=True)
    result = run(tmp_path, ["init_native_mariadb_datadir"], INIT_STUBS + "init_native_mariadb_datadir\n")
    assert result.returncode != 0 and (target / "keep").read_text() == "data"


@pytest.mark.parametrize("fail", [False, True])
def test_empty_initialization_and_failed_partial_files_preserved(tmp_path, fail):
    init = tmp_path / "init"
    init.write_text(
        '#!/bin/bash\nprintf "call\\n" >>"' + str(tmp_path / "calls") + '"\n'
        'for arg; do case "$arg" in --datadir=*) data="${arg#--datadir=}";; esac; done\n'
        + ('echo partial >"$data/partial.ibd"; exit 1\n' if fail else 'mkdir "$data/mysql"\n')
    )
    init.chmod(0o700)
    result = run(tmp_path, ["init_native_mariadb_datadir"], INIT_STUBS + "init_native_mariadb_datadir\n")
    assert (result.returncode != 0) == fail
    assert (tmp_path / "calls").read_text() == "call\n"
    if fail:
        assert (tmp_path / "data/mariadb/partial.ibd").read_text() == "partial\n"
        again = run(tmp_path, ["init_native_mariadb_datadir"], INIT_STUBS + "init_native_mariadb_datadir\n")
        assert again.returncode != 0 and (tmp_path / "calls").read_text() == "call\n"


def test_existing_initialized_datadir_not_reinitialized(tmp_path):
    (tmp_path / "data/mariadb/mysql").mkdir(parents=True)
    result = run(tmp_path, ["init_native_mariadb_datadir"], INIT_STUBS + "init_native_mariadb_datadir\n")
    assert result.returncode == 0 and "already initialized" in result.stdout


@pytest.mark.parametrize(
    "address,expected",
    [("", "127.0.0.1"), ("'127.0.0.1' # local", "127.0.0.1"), ("0.0.0.0", "0.0.0.0"), ("[::1]", "[::1]")],
)
def test_native_units_private_default_explicit_bind_and_no_sync(tmp_path, address, expected):
    units = tmp_path / "units"
    units.mkdir()
    (tmp_path / ".env").write_text("PHPMYADMIN_BIND=" + address + "\n")
    result = run(
        tmp_path,
        ["get_env_value", "native_phpmyadmin_bind", "write_native_systemd_units"],
        """
find_mysqld_bin() { echo /usr/sbin/mariadbd; }
command() { echo /usr/bin/tool; }
systemctl() { :; }
write_native_systemd_units
""",
        replacements=[("/etc/systemd/system", str(units))],
    )
    assert result.returncode == 0, result.stderr
    assert f"php -S {expected}:6163" in (units / "pasarguardbot-phpmyadmin.service").read_text()
    runtime = (units / "pasarguardbot.service").read_text()
    assert "Environment=UV_NO_SYNC=1" in runtime
    assert "run --no-sync alembic upgrade head" in runtime and "run --no-sync main.py" in runtime


@pytest.mark.parametrize("address", ["example.com", "127.0.0.1 -t /", "127.0.0.999", "$(touch /tmp/no)", "::1%eth0"])
def test_native_bind_invalid_fails_before_writing_units(tmp_path, address):
    (tmp_path / ".env").write_text("PHPMYADMIN_BIND=" + address + "\n")
    result = run(
        tmp_path,
        ["get_env_value", "native_phpmyadmin_bind", "write_native_systemd_units"],
        "write_native_systemd_units\n",
        replacements=[("/etc/systemd/system", str(tmp_path / "absent"))],
    )
    assert result.returncode != 0 and not (tmp_path / "absent").exists()


DOCKER_STUBS = """
docker() { return 0; }
verify_branch_and_image_available() { :; }
bot_image_tag_for_branch() { echo latest; }
setup_compose() { :; }
pull_images() { :; }
ensure_docker_network_ready() { :; }
docker_compose() { :; }
wait_for_containers_healthy() { echo 'HEALTH FAILURE'; return 1; }
prune_dangling_images_safe() { :; }
get_bot_image_tag() { echo latest; }
curl_download() { return 1; }
"""
NATIVE_STUBS = """
mkdir -p "$APP_DIR"
require_native_os() { :; }
fetch_bot_source() { :; }
link_native_app_paths() { :; }
sync_native_python_deps() { :; }
write_native_systemd_units() { :; }
native_systemctl() { :; }
wait_for_native_bot() { echo 'HEALTH FAILURE'; return 1; }
"""


@pytest.mark.parametrize("mode,stubs", [("docker", DOCKER_STUBS), ("native", NATIVE_STUBS)])
def test_update_health_failure_is_nonzero_and_never_success(tmp_path, mode, stubs):
    name = "action_update_" + mode
    result = run(tmp_path, [name], stubs + name + " main\n")
    assert result.returncode != 0 and "HEALTH FAILURE" in result.stdout
    assert "OK Update complete" not in result.stdout


def test_native_restart_cannot_hide_dependency_failure(tmp_path):
    result = run(
        tmp_path,
        ["action_restart_quiet"],
        """
is_native_mode() { return 0; }
native_systemctl() { return 1; }
action_restart_quiet
""",
    )
    assert result.returncode != 0 and "Full restart completed" not in result.stdout


@pytest.mark.parametrize("missing", [False, True])
def test_restart_pins_all_actual_image_ids_no_pull_or_down(tmp_path, missing):
    body = """
docker_compose() {
 case "$*" in
  'config --services') printf 'bot\\nredis\\n';;
  'ps -a -q bot') printf 'aaaaaaaaaaaa';;
  'ps -a -q redis') printf 'bbbbbbbbbbbb';;
  -f*) cat "$2" >"$CONFIG_DIR/pinned.yml"; printf '%s\\n' "$*" >"$CONFIG_DIR/up-command";;
  *) echo "UNEXPECTED $*"; return 1;;
 esac
}
docker() { printf 'sha256:%064d\\n' 7; }
wait_for_containers_healthy() { return 0; }
restart_docker_same_images
"""
    if missing:
        body = body.replace("printf 'bbbbbbbbbbbb'", "return 1")
    result = run(tmp_path, ["write_current_docker_images", "restart_docker_same_images"], body)
    if missing:
        assert result.returncode != 0 and not (tmp_path / "up-command").exists()
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert (tmp_path / "pinned.yml").read_text().count("pull_policy: never") == 2
        assert (tmp_path / "pinned.yml").read_text().count("sha256:" + "0" * 63 + "7") == 2
        assert "--pull never" in (tmp_path / "up-command").read_text()
    assert "pull bot" not in function("action_restart_quiet") and "docker_compose down" not in function(
        "action_restart_quiet"
    )


@pytest.mark.parametrize("healthy", [False, True])
def test_docker_wait_requires_three_real_readiness_successes(tmp_path, healthy):
    result = run(
        tmp_path,
        ["wait_for_containers_healthy"],
        """
sleep() { SECONDS=$((SECONDS+30)); }
docker_compose() { echo aaaaaaaaaaaa; }
docker() { echo stable-start; }
timeout() { echo probe >>"$CONFIG_DIR/probes"; return """
        + ("0" if healthy else "1")
        + "; }\nwait_for_containers_healthy\n",
    )
    assert (result.returncode == 0) == healthy
    if healthy:
        assert len((tmp_path / "probes").read_text().splitlines()) == 3


def test_compose_healthcheck_artifact_is_not_dockerignored():
    compose = (ROOT / "docker-compose.yml").read_text()
    assert "pull_policy: always" not in compose and '"/app/healthcheck.py"' in compose
    assert "healthcheck.py" not in (ROOT / ".dockerignore").read_text().splitlines()
    assert (ROOT / "healthcheck.py").is_file()
    assert "wait_for_native_bot || true" not in SOURCE and "wait_for_containers_healthy || true" not in SOURCE


def test_datadir_inspection_failure_refuses_initialization(tmp_path):
    (tmp_path / "data/mariadb").mkdir(parents=True)
    result = run(
        tmp_path, ["init_native_mariadb_datadir"], INIT_STUBS + "find() { return 1; }\ninit_native_mariadb_datadir\n"
    )
    assert result.returncode != 0 and "Cannot inspect datadir" in result.stdout


@pytest.mark.parametrize("mode", ["docker", "native"])
def test_install_failure_never_claims_success_and_records_mode(tmp_path, mode):
    stubs = """
is_installed() { return 1; }
for name in require_native_os check_disk_space check_required_ports ensure_config_dirs install_native_packages generate_env_file write_native_redis_conf write_native_mariadb_conf init_native_mariadb_datadir install_phpmyadmin_files install_uv_binary fetch_bot_source link_native_app_paths sync_native_python_deps write_native_systemd_units install_manager_command start_native_services verify_branch_and_image_available install_docker ensure_docker_network_ready setup_compose cleanup_stale_named_containers pull_images; do
 eval "$name() { :; }"
done
docker_compose() { return 0; }
set_install_mode() { echo "$1" >"$CONFIG_DIR/mode"; }
wait_for_native_bot() { return 1; }
wait_for_containers_healthy() { return 1; }
"""
    name = "action_install_" + mode
    result = run(tmp_path, [name], stubs + name + " main\n")
    assert result.returncode != 0 and "Installation completed successfully" not in result.stdout
    assert (tmp_path / "mode").read_text().strip() == mode


@pytest.mark.parametrize("healthy", [False, True])
def test_native_active_alone_is_insufficient(tmp_path, healthy):
    (tmp_path / "app").mkdir()
    result = run(
        tmp_path,
        ["wait_for_native_bot"],
        """
sleep() { SECONDS=$((SECONDS+30)); }
systemctl() {
 case "$1" in
 show) echo 123;;
 is-active) return 0;;
 is-failed) return 1;;
 esac
}
timeout() { return """
        + ("0" if healthy else "1")
        + "; }\nwait_for_native_bot\n",
    )
    assert (result.returncode == 0) == healthy


def test_container_generation_changes_reset_readiness_streak(tmp_path):
    result = run(
        tmp_path,
        ["wait_for_containers_healthy"],
        """
echo 0 >"$CONFIG_DIR/counter"
sleep() { SECONDS=$((SECONDS+30)); }
docker_compose() {
 local n
 n=$(cat "$CONFIG_DIR/counter"); n=$((n+1)); echo "$n" >"$CONFIG_DIR/counter"
 printf '%012d\\n' "$n"
}
docker() { echo stable-start; }
timeout() { return 0; }
wait_for_containers_healthy
""",
    )
    assert result.returncode != 0 and "confirmed" not in result.stdout


@pytest.mark.parametrize("fail", [False, True])
def test_restore_oneoff_and_dependencies_use_pinned_images_without_pull(tmp_path, fail):
    body = """
docker_compose() {
 case "$*" in
  'config --services') printf 'bot\\nredis\\nmariadb\\n';;
  'ps -a -q '*) printf 'aaaaaaaaaaaa';;
  -f*)
   cat "$2" >"$CONFIG_DIR/restore-pins.yml"
   echo "$*" >>"$CONFIG_DIR/commands"
   if [[ "$3" == run ]]; then return RESULT; fi;;
  *) return 1;;
 esac
}
docker() { printf 'sha256:%064d\\n' 8; }
restore_docker_same_images "$CONFIG_DIR/input.zip"
""".replace("RESULT", "9" if fail else "0")
    result = run(tmp_path, ["write_current_docker_images", "restore_docker_same_images"], body)
    assert result.returncode == (9 if fail else 0)
    assert (tmp_path / "restore-pins.yml").read_text().count("pull_policy: never") == 3
    commands = (tmp_path / "commands").read_text().splitlines()
    assert len(commands) == 3 and " stop bot" in commands[0]
    assert "--pull never" in commands[1]
    # Compose 2.26 run has no --pull flag; the shared override enforces never.
    assert " run --rm --no-deps" in commands[2]
    assert "/restore/input.zip:ro" in commands[2] and "--entrypoint uv" in commands[2]


def test_docker_auto_restart_same_container_resets_streak(tmp_path):
    result = run(
        tmp_path,
        ["wait_for_containers_healthy"],
        """
echo 0 >"$CONFIG_DIR/counter"
sleep() { SECONDS=$((SECONDS+30)); }
docker_compose() { echo aaaaaaaaaaaa; }
docker() {
 local n
 n=$(cat "$CONFIG_DIR/counter"); n=$((n+1)); echo "$n" >"$CONFIG_DIR/counter"
 echo "start-time-$n"
}
timeout() { return 0; }
wait_for_containers_healthy
""",
    )
    assert result.returncode != 0 and "confirmed" not in result.stdout
