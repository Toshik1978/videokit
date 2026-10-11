"""Tests for .github/dependabot.yml: majors that need manual work are never proposed."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

MAJOR = "version-update:semver-major"
CONFIG = Path(__file__).resolve().parents[3] / ".github" / "dependabot.yml"


def _entries(ecosystem: str) -> list[dict[str, Any]]:
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    return [update for update in config["updates"] if update["package-ecosystem"] == ecosystem]


def test_docker_entry_ignores_python_minor_and_major_and_node_major_upgrades() -> None:
    (docker,) = _entries("docker")

    assert docker["ignore"] == [
        {
            "dependency-name": "python",
            "update-types": ["version-update:semver-major", "version-update:semver-minor"],
        },
        {"dependency-name": "node", "update-types": [MAJOR]},
    ]


def test_npm_entry_ignores_typescript_majors() -> None:
    (npm,) = _entries("npm")

    assert npm["ignore"] == [{"dependency-name": "typescript", "update-types": [MAJOR]}]


def test_uv_entry_ignores_av_majors() -> None:
    (uv,) = _entries("uv")

    assert uv["ignore"] == [{"dependency-name": "av", "update-types": [MAJOR]}]


def test_github_actions_ignore_block_is_unchanged() -> None:
    (actions,) = _entries("github-actions")

    assert actions["ignore"] == [
        {
            "dependency-name": "*",
            "update-types": ["version-update:semver-minor", "version-update:semver-patch"],
        }
    ]
