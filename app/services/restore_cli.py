"""Offline restore entry point. Stop all writers before running; never starts Telegram."""

import argparse
import asyncio
from pathlib import Path

from app.services.restore import restore_from_zip


def main():
    parser = argparse.ArgumentParser(description="Offline trusted-backup restore; stop the bot first")
    parser.add_argument("backup", type=Path)
    parser.add_argument("--offline", action="store_true", required=True, help="Confirm all writers are stopped")
    parser.add_argument("--safety-dir", type=Path, default=Path("logs/restore-safety"))
    args = parser.parse_args()
    result = asyncio.run(restore_from_zip(args.backup, offline=args.offline, safety_dir=args.safety_dir))
    print(result.message)
    raise SystemExit(0 if result.ok else 1)


if __name__ == "__main__":
    main()
