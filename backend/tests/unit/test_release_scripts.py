"""Tests for the release guard scripts in .github/scripts (changelog sections, version, tag on main)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / ".github" / "scripts"
EXTRACT = SCRIPTS / "extract-changelog.sh"
CHECK_VERSION = SCRIPTS / "check-release-version.sh"
ON_MAIN = SCRIPTS / "check-tag-on-main.sh"

CHANGELOG = """\
# Changelog

Intro text.

---

## v1.50.0 — 2026-03-01

Fifty notes.

## v1.5.0 — 2026-02-01

### Features

- middle feature

## v1.4.0

Bare heading notes.

## v1.3.0 — 2026-01-01

## v1.2.0 — 2025-12-01

Last notes.
"""


def run(script: Path, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(script), *args], capture_output=True, text=True, cwd=cwd, check=False)


@pytest.fixture
def changelog(tmp_path: Path) -> Path:
    path = tmp_path / "CHANGELOG.md"
    path.write_text(CHANGELOG, encoding="utf-8")
    return path


def test_extract_first_section(changelog: Path) -> None:
    result = run(EXTRACT, "v1.50.0", str(changelog))
    assert result.returncode == 0
    assert result.stdout.startswith("## v1.50.0 — 2026-03-01")
    assert "Fifty notes." in result.stdout
    assert "v1.5.0" not in result.stdout


def test_extract_middle_section_is_not_confused_with_longer_version(changelog: Path) -> None:
    result = run(EXTRACT, "v1.5.0", str(changelog))
    assert result.returncode == 0
    assert result.stdout.startswith("## v1.5.0 — 2026-02-01")
    assert "middle feature" in result.stdout
    assert "Fifty notes." not in result.stdout
    assert "Bare heading" not in result.stdout


def test_extract_bare_heading(changelog: Path) -> None:
    result = run(EXTRACT, "v1.4.0", str(changelog))
    assert result.returncode == 0
    assert "Bare heading notes." in result.stdout


def test_extract_last_section(changelog: Path) -> None:
    result = run(EXTRACT, "v1.2.0", str(changelog))
    assert result.returncode == 0
    assert "Last notes." in result.stdout


def test_extract_dots_are_literal(changelog: Path) -> None:
    result = run(EXTRACT, "v1x5x0", str(changelog))
    assert result.returncode == 1


def test_extract_empty_section_fails(changelog: Path) -> None:
    result = run(EXTRACT, "v1.3.0", str(changelog))
    assert result.returncode == 1
    assert f"error: no '## v1.3.0' section found in {changelog}" in result.stderr
    assert "hint:" in result.stderr


def test_extract_missing_section_fails(changelog: Path) -> None:
    result = run(EXTRACT, "v9.9.9", str(changelog))
    assert result.returncode == 1
    assert "error: no '## v9.9.9' section found" in result.stderr


def test_extract_usage_and_missing_file(tmp_path: Path) -> None:
    assert run(EXTRACT).returncode == 2
    assert run(EXTRACT, "v1.0.0", str(tmp_path / "nope.md")).returncode == 2


def test_version_matches_newest_heading(changelog: Path) -> None:
    result = run(CHECK_VERSION, "v1.50.0", str(changelog))
    assert result.returncode == 0
    assert result.stdout == "release version: v1.50.0 (tag v1.50.0)\n"


def test_version_mismatch_names_both(changelog: Path) -> None:
    result = run(CHECK_VERSION, "v1.5.0", str(changelog))
    assert result.returncode == 1
    assert "v1.5.0" in result.stderr
    assert "v1.50.0" in result.stderr


def test_version_no_heading(tmp_path: Path) -> None:
    path = tmp_path / "CHANGELOG.md"
    path.write_text("# Changelog\n\nNothing yet.\n", encoding="utf-8")
    assert run(CHECK_VERSION, "v1.0.0", str(path)).returncode == 2


def test_version_usage_and_missing_file(tmp_path: Path) -> None:
    assert run(CHECK_VERSION).returncode == 2
    assert run(CHECK_VERSION, "v1.0.0", str(tmp_path / "nope.md")).returncode == 2


def git(repo: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    done = subprocess.run(
        ["git", "-c", f"core.hooksPath={os.devnull}", "-c", "commit.gpgsign=false", *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    git(path, "commit", "-q", "--allow-empty", "-m", "first")
    return path


def test_tag_on_main(repo: Path) -> None:
    sha = git(repo, "rev-parse", "HEAD")
    result = run(ON_MAIN, sha, "main", cwd=repo)
    assert result.returncode == 0
    assert result.stdout == f"{sha} is on main\n"


def test_tag_on_side_branch_is_rejected(repo: Path) -> None:
    git(repo, "checkout", "-q", "-b", "side")
    git(repo, "commit", "-q", "--allow-empty", "-m", "side work")
    sha = git(repo, "rev-parse", "HEAD")
    result = run(ON_MAIN, sha, "main", cwd=repo)
    assert result.returncode == 1
    assert result.stderr == f"{sha} is not on main\n"


def test_tag_on_main_defaults_to_origin_main(repo: Path) -> None:
    sha = git(repo, "rev-parse", "HEAD")
    git(repo, "update-ref", "refs/remotes/origin/main", sha)
    assert run(ON_MAIN, sha, cwd=repo).returncode == 0


def test_tag_on_main_usage(repo: Path) -> None:
    assert run(ON_MAIN, cwd=repo).returncode == 2
