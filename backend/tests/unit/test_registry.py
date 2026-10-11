import textwrap
from pathlib import Path
from typing import Any

import pytest

from videokit.core.registry import JobCancelled, RegistryError, Tool, discover_tools

TESTS_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _tests_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.syspath_prepend(str(TESTS_DIR))


def _make_package(root: Path, pkg: str, tools: dict[str, str]) -> None:
    """Write package `pkg` with one tool subpackage per entry (sub name -> tool id)."""
    (root / pkg).mkdir()
    (root / pkg / "__init__.py").write_text("")
    for sub, tool_id in tools.items():
        (root / pkg / sub).mkdir()
        body = textwrap.dedent(
            f"""
            from fastapi import APIRouter

            class T:
                id = {tool_id!r}
                name = "n"
                description = "d"
                router = APIRouter()

                async def run(self, job, ctx):
                    return None

            TOOL = T()
            """
        )
        (root / pkg / sub / "__init__.py").write_text(body)


def test_discovers_dummy_tools_without_core_changes() -> None:
    tools = discover_tools("dummy_tools")
    assert sorted(tools) == ["alpha", "beta_2"]
    assert all(isinstance(t, Tool) for t in tools.values())


def test_default_package_imports_cleanly() -> None:
    assert isinstance(discover_tools(), dict)


def test_invalid_id_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.syspath_prepend(str(tmp_path))
    _make_package(tmp_path, "bad_id_pkg", {"x": "Bad-Id"})
    with pytest.raises(RegistryError, match="Bad-Id"):
        discover_tools("bad_id_pkg")


def test_duplicate_id_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.syspath_prepend(str(tmp_path))
    _make_package(tmp_path, "dup_pkg", {"a": "same", "b": "same"})
    with pytest.raises(RegistryError, match="Duplicate"):
        discover_tools("dup_pkg")


def test_object_that_is_not_a_tool_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.syspath_prepend(str(tmp_path))
    (tmp_path / "broken_pkg" / "x").mkdir(parents=True)
    (tmp_path / "broken_pkg" / "__init__.py").write_text("")
    (tmp_path / "broken_pkg" / "x" / "__init__.py").write_text("TOOL = object()\n")
    with pytest.raises(RegistryError, match="Tool"):
        discover_tools("broken_pkg")


def test_job_cancelled_is_exception() -> None:
    assert issubclass(JobCancelled, Exception)


async def test_dummy_tool_run_honours_check_cancelled() -> None:
    class Handle:
        def set_state(self, state: str, **fields: Any) -> None:
            pass

        def check_cancelled(self) -> None:
            raise JobCancelled

    tool = discover_tools("dummy_tools")["alpha"]
    with pytest.raises(JobCancelled):
        await tool.run(Handle(), None)  # type: ignore[arg-type]
