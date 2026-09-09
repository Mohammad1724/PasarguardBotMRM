"""Explicit local Docker smoke: mutable tags must not change Restart's image IDs.

Uses tiny synthetic containers (NOT a production bot/database) and the real
manager Restart functions. Requires MANAGER_DOCKER_SMOKE=1 and a disposable local
Docker daemon selected via a unix:// DOCKER_HOST. No production project is used.
"""

import json
import os
import re
import secrets
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if os.getenv("MANAGER_DOCKER_SMOKE") != "1" or not os.getenv("DOCKER_HOST", "").startswith("unix://"):
    raise SystemExit("Use MANAGER_DOCKER_SMOKE=1 with a disposable local unix:// DOCKER_HOST.")


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True).strip()


name = "pbg-manager-smoke-" + secrets.token_hex(5)
old, current = name + ":old", name + ":current"
source = (ROOT / "scripts/pasarguardbot.sh").read_text()
with tempfile.TemporaryDirectory(prefix="pbg-restart-") as folder:
    folder = Path(folder)
    (folder / "data").mkdir()
    (folder / "data/balances.txt").write_text("SYNTHETIC: user=2 balance=123456 services=100,101\n")
    (folder / ".env").write_text("")
    (folder / "probe").write_text(
        "#!/bin/sh\n# Synthetic readiness only, to isolate Restart semantics.\necho ready\nexit 0\n"
    )
    (folder / "probe").chmod(0o755)
    (folder / "restore").write_text(
        '#!/bin/sh\ncat /restore/input.zip >/data/restore-input.txt\necho "$IMAGE_MARKER" >/data/restore-image.txt\n'
    )
    (folder / "restore").chmod(0o755)
    (folder / "input.zip").write_text("synthetic restore input")
    services = ["bot", "redis", "mariadb", "phpmyadmin"]
    compose = "services:\n" + "".join(
        f"  {service}:\n    image: {current}\n    pull_policy: always\n    network_mode: none\n"
        f"    volumes:\n      - {folder}/data:/data\n"
        for service in services
    )
    (folder / "compose.yml").write_text(compose)
    base = ["compose", "-p", name, "-f", str(folder / "compose.yml")]
    try:
        for marker in ["OLD", "NEW"]:
            (folder / "Dockerfile").write_text(
                f'FROM busybox:1.37\nCOPY probe /app/.venv/bin/python\nCOPY restore /bin/uv\nENV IMAGE_MARKER={marker}\nCMD ["sleep","600"]\n'
            )
            docker("build", "-q", "-t", old if marker == "OLD" else current, str(folder))
            if marker == "OLD":
                docker("tag", old, current)
                docker(*base, "up", "-d", "--pull", "never")
                before = {
                    s: docker("inspect", "--format", "{{.Image}}", docker(*base, "ps", "-q", s)) for s in services
                }
        assert docker("image", "inspect", "--format", "{{.Id}}", current) != before["bot"]
        functions = "\n".join(
            re.search(r"^" + n + r"\(\) \{.*?^\}", source, re.M | re.S)[0]
            for n in [
                "docker_compose",
                "write_current_docker_images",
                "restart_docker_same_images",
                "restore_docker_same_images",
                "wait_for_containers_healthy",
                "action_restart_quiet",
            ]
        )
        script = folder / "restart.sh"
        script.write_text(
            "set -euo pipefail\n"
            f"CONFIG_DIR='{folder}'\nCOMPOSE_FILE='{folder}/compose.yml'\nENV_FILE='{folder}/.env'\n"
            'info() { echo "$*"; }; ok() { echo "$*"; }; warn() { echo "$*"; }; err() { echo "$*"; }\n'
            'die() { echo "$*"; exit 1; }; is_native_mode() { return 1; }\n' + functions + "\naction_restart_quiet\n"
        )
        env = {**os.environ, "COMPOSE_PROJECT_NAME": name}
        subprocess.run(["bash", str(script)], env=env, check=True, timeout=150)
        after = {s: docker("inspect", "--format", "{{.Image}}", docker(*base, "ps", "-q", s)) for s in services}
        assert before == after
        for service in services:
            cid = docker(*base, "ps", "-q", service)
            assert (
                docker("exec", cid, "cat", "/data/balances.txt") == "SYNTHETIC: user=2 balance=123456 services=100,101"
            )
            assert "IMAGE_MARKER=OLD" in json.loads(docker("inspect", cid))[0]["Config"]["Env"]
        restore_script = folder / "restore.sh"
        restore_script.write_text(
            script.read_text().replace(
                "\naction_restart_quiet\n", f"\nrestore_docker_same_images '{folder}/input.zip'\n"
            )
        )
        subprocess.run(["bash", str(restore_script)], env=env, check=True, timeout=90)
        assert (folder / "data/restore-image.txt").read_text().strip() == "OLD"
        assert (folder / "data/restore-input.txt").read_text() == "synthetic restore input"
        print(
            "PASS: Restore one-off also used the pinned OLD image and read-only input mount (synthetic restore command, no database imported)."
        )
        print(
            "PASS: real Docker/Compose Restart retained all four image IDs and persisted synthetic data despite moved tags; no upgrade."
        )
    finally:
        docker(*base, "down", "--remove-orphans")
        for image in [old, current]:
            subprocess.run(["docker", "image", "rm", image], check=False, capture_output=True)
