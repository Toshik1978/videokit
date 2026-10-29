import textwrap
from pathlib import Path
from typing import Any

import pytest

from videokit.core.registry import JobCancelled, RegistryError, Tool, discover_tools

TESTS_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _tests_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.syspath_prepend(str(TESTS_DIR))


_MEMBERS = {
    "concurrency": "    concurrency = 1",
    "resume_after_restart": "    resume_after_restart = False",
    "enabled": "    def enabled(self, settings):\n        return True",
    "accept": "    def accept(self, job, ctx):\n        return None",
}


def _make_package(
    root: Path, pkg: str, tools: dict[str, str], *, omit: str | None = None, override: dict[str, str] | None = None
) -> None:
    """Write package `pkg` with one tool subpackage per entry (sub name -> tool id).

    `omit` leaves one of the new members out; `override` replaces members' source (class-body lines).
    """
    members = {**_MEMBERS, **(override or {})}
    extra = "\n".join(src for name, src in members.items() if name != omit)
    (root / pkg).mkdir()
    (root / pkg / "__init__.py").write_text("")
    for sub, tool_id in tools.items():
        (root / pkg / sub).mkdir()
        body = (
            textwrap.dedent(
                f"""
                from fastapi import APIRouter

                class T:
                    id = {tool_id!r}
                    name = "n"
                    description = "d"
                    router = APIRouter()

                    async def run(self, job, ctx):
                        return None
                """
            )
            + extra
            + "\n\nTOOL = T()\n"
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


@pytest.mark.parametrize("member", ["concurrency", "resume_after_restart", "enabled", "accept"])
def test_tool_missing_a_member_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, member: str) -> None:
    monkeypatch.syspath_prepend(str(tmp_path))
    _make_package(tmp_path, "missing_pkg", {"x": "tool_x"}, omit=member)
    with pytest.raises(RegistryError, match="Tool"):
        discover_tools("missing_pkg")


@pytest.mark.parametrize("value", ["0", "-1", "True", "1.5", "'2'"])
def test_invalid_concurrency_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.syspath_prepend(str(tmp_path))
    _make_package(tmp_path, "conc_pkg", {"x": "tool_x"}, override={"concurrency": f"    concurrency = {value}"})
    with pytest.raises(RegistryError, match="concurrency"):
        discover_tools("conc_pkg")


@pytest.mark.parametrize("value", ["1", "'yes'", "None"])
def test_non_bool_resume_after_restart_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.syspath_prepend(str(tmp_path))
    _make_package(
        tmp_path,
        "resume_pkg",
        {"x": "tool_x"},
        override={"resume_after_restart": f"    resume_after_restart = {value}"},
    )
    with pytest.raises(RegistryError, match="resume_after_restart"):
        discover_tools("resume_pkg")


def test_tool_with_valid_members_accepted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.syspath_prepend(str(tmp_path))
    _make_package(
        tmp_path,
        "ok_pkg",
        {"x": "tool_x"},
        override={"concurrency": "    concurrency = 3", "resume_after_restart": "    resume_after_restart = True"},
    )
    tool = discover_tools("ok_pkg")["tool_x"]
    assert (tool.concurrency, tool.resume_after_restart) == (3, True)


def test_shipped_tools_declare_the_lane_members() -> None:
    for tool in discover_tools().values():
        assert tool.concurrency == 1
        assert tool.resume_after_restart is False
        assert tool.enabled(None) is True  # type: ignore[arg-type]


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
