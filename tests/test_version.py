"""Version reporting must resolve pyproject.toml independently of the working directory."""

import tomllib
from pathlib import Path

import app.version as version_module

ROOT = Path(__file__).resolve().parents[1]


def test_version_matches_pyproject_from_any_cwd(monkeypatch, tmp_path):
    expected = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    monkeypatch.chdir(tmp_path)
    assert version_module.get_app_version() == expected


def test_version_falls_back_to_unknown_without_pyproject(monkeypatch, tmp_path):
    monkeypatch.setattr(version_module, "_PYPROJECT", tmp_path / "missing.toml")
    assert version_module._pyproject_version() == "unknown"
