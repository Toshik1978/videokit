"""Tests for .github/scripts/check-commit-messages.sh against a throwaway git repository.

The temp repo defines an offline local hook with the id the script runs, `conventional-pre-commit`,
so no hook repository is fetched.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / ".github" / "scripts" / "check-commit-messages.sh"
ZEROS = "0" * 40

PRE_COMMIT_CONFIG = """\
repos:
  - repo: local
    hooks:
      - id: conventional-pre-commit
        name: conventional commit
        language: pygrep
        entry: '^(feat|fix|build|ci|chore|docs|test|refactor)(\\([a-z-]+\\))?!?: .+'
        args: [--negate]
        stages: [commit-msg]
"""

pytestmark = pytest.mark.skipif(shutil.which("pre-commit") is None, reason="pre-commit is not on PATH")


class Repo:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.env = {
            **os.environ,
            "PRE_COMMIT_HOME": str(path.parent / "pre-commit-home"),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
        }
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Test User")
        self.git("config", "user.email", "test@example.com")
        (path / ".pre-commit-config.yaml").write_text(PRE_COMMIT_CONFIG)
        self.git("add", ".pre-commit-config.yaml")
        self.git("commit", "-q", "-m", "chore: add config")

    def git(self, *args: str) -> str:
        result = subprocess.run(["git", *args], cwd=self.path, env=self.env, check=True, capture_output=True, text=True)
        return result.stdout.strip()

    def commit(self, message: str) -> str:
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD")

    def check(self, base: str, head: str = "HEAD") -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(SCRIPT), base, head], cwd=self.path, env=self.env, capture_output=True, text=True
        )


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    path = tmp_path / "repo"
    path.mkdir()
    return Repo(path)


def test_accepts_conventional_commits(repo: Repo) -> None:
    base = repo.git("rev-parse", "HEAD")
    repo.commit("feat: add thing")
    repo.commit("fix(api): handle edge case")

    result = repo.check(base)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "checked 2 commit message(s)" in result.stdout


def test_rejects_bad_commit_and_names_only_its_short_sha(repo: Repo) -> None:
    base = repo.git("rev-parse", "HEAD")
    good = repo.commit("feat: good one")
    bad = repo.commit("Updated some stuff")

    result = repo.check(base)

    output = result.stdout + result.stderr
    assert result.returncode == 1
    assert bad[:7] in output
    assert good[:7] not in output
    assert "pre-commit install" in output


def test_skips_merge_commits(repo: Repo) -> None:
    base = repo.git("rev-parse", "HEAD")
    repo.git("switch", "-q", "-c", "side")
    repo.commit("feat: on side")
    repo.git("switch", "-q", "main")
    repo.commit("fix: on main")
    repo.git("merge", "-q", "--no-ff", "-m", "Merge branch 'side'", "side")

    result = repo.check(base)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "checked 2 commit message(s)" in result.stdout


@pytest.mark.parametrize("base", [ZEROS, "", "deadbeef" * 5])
def test_checks_only_the_tip_when_base_is_missing(repo: Repo, base: str) -> None:
    repo.commit("bad old message")
    repo.commit("feat: good tip")

    result = repo.check(base)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "checked 1 commit message(s)" in result.stdout


def test_rejects_a_bad_tip_when_base_is_missing(repo: Repo) -> None:
    bad = repo.commit("not conventional")

    result = repo.check(ZEROS)

    assert result.returncode == 1
    assert bad[:7] in result.stdout + result.stderr
