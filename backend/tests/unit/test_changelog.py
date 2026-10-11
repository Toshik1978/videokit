"""`scripts/changelog.sh` against throwaway git repositories with the real git-cliff config."""

import os
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(shutil.which("git-cliff") is None, reason="git-cliff not installed")

ROOT = Path(__file__).resolve().parents[3]
HEADER = tomllib.loads((ROOT / ".cliff.toml").read_text())["changelog"]["header"]


class Repo:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.env = {
            **os.environ,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        }
        self.env.pop("TAG", None)
        self.git("init", "-q", "-b", "main")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "core.hooksPath", os.devnull)
        (path / "scripts").mkdir()
        shutil.copy(ROOT / ".cliff.toml", path / ".cliff.toml")
        shutil.copy(ROOT / "CHANGELOG.md", path / "CHANGELOG.md")
        shutil.copy(ROOT / "scripts" / "changelog.sh", path / "scripts" / "changelog.sh")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "chore: scaffold")

    def git(self, *args: str) -> str:
        done = subprocess.run(["git", *args], cwd=self.path, env=self.env, check=True, capture_output=True, text=True)
        return done.stdout

    def commit(self, message: str) -> None:
        self.git("commit", "-q", "--allow-empty", "-m", message)

    @property
    def changelog(self) -> str:
        return (self.path / "CHANGELOG.md").read_text()

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", "scripts/changelog.sh", *args],
            cwd=self.path,
            env=self.env,
            capture_output=True,
            text=True,
        )


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    return Repo(tmp_path)


def test_changelog_file_is_exactly_the_header() -> None:
    assert (ROOT / "CHANGELOG.md").read_text() == HEADER


def test_first_release_is_v0_1_0(repo: Repo) -> None:
    repo.commit("feat: first thing")
    done = repo.run()
    assert done.returncode == 0, done.stderr
    assert "v0.1.0" in done.stdout
    assert "\n## v0.1.0 — " in repo.changelog
    assert "### Features" in repo.changelog
    assert "- feat: first thing" in repo.changelog


def test_feat_after_tag_bumps_minor(repo: Repo) -> None:
    repo.commit("feat: first")
    repo.git("tag", "v0.1.0")
    repo.commit("feat: second")
    assert repo.run().returncode == 0
    assert "## v0.2.0 — " in repo.changelog
    assert "first" not in repo.changelog


def test_fix_after_tag_bumps_patch(repo: Repo) -> None:
    repo.git("tag", "v0.1.0")
    repo.commit("fix: a bug")
    assert repo.run().returncode == 0
    assert "## v0.1.1 — " in repo.changelog
    assert "### Bug Fixes" in repo.changelog


def test_breaking_change_stays_below_1_0(repo: Repo) -> None:
    repo.git("tag", "v0.1.0")
    repo.commit("feat!: drop the old thing")
    assert repo.run().returncode == 0
    assert "## v0.2.0 — " in repo.changelog


def test_tag_argument_overrides_the_version(repo: Repo) -> None:
    repo.git("tag", "v0.1.0")
    repo.commit("feat: big")
    done = repo.run("v1.0.0")
    assert done.returncode == 0, done.stderr
    assert "v1.0.0" in done.stdout
    assert "## v1.0.0 — " in repo.changelog


def test_excluded_commits_are_never_listed(repo: Repo) -> None:
    repo.git("tag", "v0.1.0")
    repo.commit("chore(release): v0.1.0")
    repo.commit("test: more tests")
    repo.commit("style: tidy")
    repo.commit("build(deps): bump x")
    repo.commit("ci(deps): bump y")
    repo.commit("Merge branch 'other'")
    repo.commit("docs: explain it")
    assert repo.run().returncode == 0
    for text in ("chore", "test:", "style", "deps", "Merge"):
        assert text not in repo.changelog
    assert "### Others" in repo.changelog
    assert "- docs: explain it" in repo.changelog


def test_header_is_preserved_once_at_the_top(repo: Repo) -> None:
    repo.commit("feat: a")
    assert repo.run().returncode == 0
    repo.git("tag", "v0.1.0")
    repo.commit("fix: b")
    assert repo.run().returncode == 0
    text = repo.changelog
    assert text.startswith(HEADER)
    assert text.count("# Changelog") == 1
    assert text.index("## v0.1.1") < text.index("## v0.1.0")


@pytest.mark.parametrize("tag", [None, "v0.1.0"])
def test_nothing_releasable_refuses(repo: Repo, tag: str | None) -> None:
    if tag:
        repo.git("tag", tag)
    repo.commit("chore: housekeeping")
    repo.commit("test: only tests")
    before = repo.changelog
    done = repo.run()
    assert done.returncode == 1
    since = tag or "the first commit"
    assert f"nothing to release since {since}" in done.stderr
    assert repo.changelog == before


def test_refuses_even_with_tag_override(repo: Repo) -> None:
    repo.git("tag", "v0.1.0")
    repo.commit("chore: housekeeping")
    done = repo.run("v0.2.0")
    assert done.returncode == 1
    assert repo.changelog == HEADER
