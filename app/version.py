import tomllib
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from app.logger import LogTag, get_logger

logger = get_logger(__name__)

# Distribution name from pyproject.toml; "app" is only the import package name.
_PROJECT_NAME = "PasarguardBot"
# Resolve pyproject.toml from this file, not the process working directory, so
# `python /path/to/main.py` works from any CWD (systemd, CLI helpers, containers).
_PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _pyproject_version() -> str:
    try:
        data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
        return str(data["project"]["version"])
    except OSError, KeyError, TypeError, tomllib.TOMLDecodeError:
        logger.warning("%s Could not read version from %s", LogTag.VERSION, _PYPROJECT)
        return "unknown"


def get_app_version() -> str:
    try:
        return version(_PROJECT_NAME)
    except PackageNotFoundError:
        return _pyproject_version()


def v(pkg: str) -> str:
    try:
        return version(pkg)
    except PackageNotFoundError:
        return "unknown"


def get_telethon_layer() -> str:
    try:
        from telethon.tl.alltlobjects import LAYER

        return str(LAYER)
    except ImportError:
        return "unknown"


@dataclass(frozen=True)
class Versions:
    app: str
    telethon: str
    telethon_layer: str
    fastapi: str
    pasarguard: str


VERSIONS = Versions(
    app=get_app_version(),
    telethon=v("telethon"),
    telethon_layer=get_telethon_layer(),
    fastapi=v("fastapi"),
    pasarguard=v("pasarguard"),
)


def log_runtime_versions() -> None:
    """Log dependency versions once at startup (not on import)."""
    logger.info(
        "%s app=%s telethon=%s layer=%s fastapi=%s pasarguard=%s",
        LogTag.VERSION,
        VERSIONS.app,
        VERSIONS.telethon,
        VERSIONS.telethon_layer,
        VERSIONS.fastapi,
        VERSIONS.pasarguard,
    )
