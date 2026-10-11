import importlib.util
import subprocess
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_denylist.py"
SECRET = "Sup3rS3cretTerm"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_denylist", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    return tmp_path


@pytest.fixture
def run(repo: Path) -> Callable[[], int]:
    module = _load()
    return lambda: int(module.main(cwd=repo))


def _stage(repo: Path, name: str, text: str) -> None:
    (repo / name).write_text(text)
    _git(repo, "add", name)


def test_no_denylist_file_exits_zero(repo: Path, run: Callable[[], int]) -> None:
    _stage(repo, "a.txt", f"{SECRET}\n")
    assert run() == 0


def test_hit_exits_one_naming_location_without_leaking_match(
    repo: Path, run: Callable[[], int], capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / ".denylist").write_text(f"{SECRET}\n")
    _stage(repo, "a.txt", f"clean\nclean\nleaks {SECRET} here\n")
    assert run() == 1
    output = capsys.readouterr()
    assert "a.txt:3" in output.out + output.err
    assert SECRET not in output.out + output.err
    assert SECRET.lower() not in (output.out + output.err).lower()


def test_match_is_case_insensitive(repo: Path, run: Callable[[], int]) -> None:
    (repo / ".denylist").write_text(f"{SECRET.upper()}\n")
    _stage(repo, "a.txt", f"x {SECRET.lower()} y\n")
    assert run() == 1


def test_comments_and_blank_lines_are_ignored(repo: Path, run: Callable[[], int]) -> None:
    (repo / ".denylist").write_text(f"\n# {SECRET}\n   \n")
    _stage(repo, "a.txt", f"{SECRET}\n")
    assert run() == 0


def test_only_added_lines_of_staged_diff_count(repo: Path, run: Callable[[], int]) -> None:
    (repo / ".denylist").write_text(f"{SECRET}\n")
    (repo / "old.txt").write_text(f"{SECRET}\n")
    _git(repo, "add", "old.txt")
    # Pre-existing (already committed) content is not re-flagged when untouched.
    _git(repo, "commit", "-q", "-m", "chore: seed")
    _stage(repo, "new.txt", "nothing to see\n")
    assert run() == 0
    # Unstaged working-tree edits are not scanned either.
    (repo / "new.txt").write_text(f"{SECRET}\n")
    assert run() == 0
    # Removed lines are not scanned.
    (repo / "old.txt").write_text("gone\n")
    _git(repo, "add", "old.txt")
    assert run() == 0


def test_content_lines_resembling_diff_headers_are_still_scanned(
    repo: Path, run: Callable[[], int], capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / ".denylist").write_text(f"{SECRET}\n")
    _stage(repo, "a.txt", f"++ {SECRET}\n")
    assert run() == 1
    assert "a.txt:1" in capsys.readouterr().err
    # A removed line that looks like a header must not derail later added lines.
    (repo / "b.txt").write_text("-- old\nkeep\n")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-q", "-m", "chore: seed b")
    _stage(repo, "b.txt", f"keep\n{SECRET}\n")
    assert run() == 1
    assert "b.txt:2" in capsys.readouterr().err
