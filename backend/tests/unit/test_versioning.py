"""The git tag is the only version: nothing hardcodes one."""

import json
import tomllib
from pathlib import Path

import videokit

REPO = Path(__file__).resolve().parents[3]
PYPROJECT = REPO / "backend" / "pyproject.toml"
COMMENT = "# The git tag is the version (docs/RELEASING.md); the build backend requires this field."


def test_package_has_no_version_attribute() -> None:
    assert not hasattr(videokit, "__version__")
    assert "__version__" not in getattr(videokit, "__all__", [])


def test_pyproject_version_is_placeholder_with_comment() -> None:
    text = PYPROJECT.read_text(encoding="utf-8")
    assert tomllib.loads(text)["project"]["version"] == "0.0.0"
    lines = text.splitlines()
    index = lines.index('version = "0.0.0"')
    assert lines[index - 1] == COMMENT


def test_frontend_package_json_has_no_version() -> None:
    data = json.loads((REPO / "frontend" / "package.json").read_text(encoding="utf-8"))
    assert "version" not in data
    assert data["private"] is True


def test_frontend_lockfile_has_no_root_version() -> None:
    lock = json.loads((REPO / "frontend" / "package-lock.json").read_text(encoding="utf-8"))
    assert "version" not in lock
    assert "version" not in lock["packages"][""]
