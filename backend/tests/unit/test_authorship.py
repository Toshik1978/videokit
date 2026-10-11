"""The author is named (name only, never an email) everywhere a package manifest or notice asks."""

import json
import re
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
AUTHOR = "Anton Krivenko"


def test_license_names_the_author() -> None:
    text = (REPO / "LICENSE").read_text(encoding="utf-8")
    assert f"Copyright (c) 2026 {AUTHOR}\n" in text
    assert "@" not in text


def test_pyproject_authors() -> None:
    data = tomllib.loads((REPO / "backend" / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["authors"] == [{"name": AUTHOR}]


def test_package_json_author() -> None:
    data = json.loads((REPO / "frontend" / "package.json").read_text(encoding="utf-8"))
    assert data["author"] == AUTHOR


def test_dockerfile_authors_label() -> None:
    text = (REPO / "Dockerfile").read_text(encoding="utf-8")
    match = re.search(r'org\.opencontainers\.image\.authors="([^"]*)"', text)
    assert match is not None
    assert match.group(1) == AUTHOR
