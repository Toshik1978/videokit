"""Tests for .github/dependabot.yml: the Python base image only gets patch-level proposals."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

CONFIG = Path(__file__).resolve().parents[3] / ".github" / "dependabot.yml"


def _entries(ecosystem: str) -> list[dict[str, Any]]:
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    return [update for update in config["updates"] if update["package-ecosystem"] == ecosystem]


def test_docker_entry_ignores_python_minor_and_major_upgrades() -> None:
    (docker,) = _entries("docker")

    assert docker["ignore"] == [
        {
            "dependency-name": "python",
            "update-types": ["version-update:semver-major", "version-update:semver-minor"],
        }
    ]


def test_uv_and_npm_entries_have_no_ignore_block() -> None:
    for ecosystem in ("uv", "npm"):
        (entry,) = _entries(ecosystem)
        assert "ignore" not in entry, ecosystem


def test_github_actions_ignore_block_is_unchanged() -> None:
    (actions,) = _entries("github-actions")

    assert actions["ignore"] == [
        {
            "dependency-name": "*",
            "update-types": ["version-update:semver-minor", "version-update:semver-patch"],
        }
    ]
