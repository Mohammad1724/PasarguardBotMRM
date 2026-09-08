#!/usr/bin/env bash
# Standalone launcher; changes are performed only after Python preflight checks.
set -euo pipefail
if [[ "${EUID:-$(id -u)}" != 0 ]]; then
    echo 'Run this downloaded file with sudo: sudo bash setup-miniapp.sh [domain]' >&2
    exit 1
fi
command -v apt-get >/dev/null || { echo 'Automatic setup supports Debian/Ubuntu hosts.' >&2; exit 1; }
[[ -d /run/systemd/system ]] || { echo 'A systemd host is required; do not run inside a container.' >&2; exit 1; }
if ! command -v python3 >/dev/null || ! command -v curl >/dev/null || ! command -v ss >/dev/null; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3 curl ca-certificates iproute2
fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' || {
    echo 'System Python 3.10+ is required (Ubuntu 22.04+ / Debian 12+).' >&2
    exit 1
}
branch="${PASARGUARDBOT_SETUP_BRANCH:-main}"
[[ "$branch" =~ ^[a-zA-Z0-9_./-]+$ ]] || { echo 'Invalid branch.' >&2; exit 1; }
tmp="$(mktemp -d)"
trap 'rm -rf -- "$tmp"' EXIT
curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
    --connect-timeout 15 --max-time 90 --retry 2 \
    "https://raw.githubusercontent.com/Mohammad1724/PasarguardBotMRM/${branch}/scripts/setup_miniapp.py" \
    -o "$tmp/setup_miniapp.py"
python3 "$tmp/setup_miniapp.py" "$@"
