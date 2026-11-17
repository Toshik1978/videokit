"""The runtime dependencies the video generation tool needs, and the heavy ones it must not pull in."""

from __future__ import annotations

import ast
import importlib
import importlib.util
import re
import sys
import tomllib
from importlib.metadata import packages_distributions
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("module", ["google.genai", "python_multipart"])
def test_required_modules_import(module: str) -> None:
    importlib.import_module(module)


@pytest.mark.parametrize("module", ["torch", "transformers"])
def test_google_genai_local_tokenizer_extra_is_not_installed(module: str) -> None:
    # The `local-tokenizer` extra of google-genai drags in torch; the image size guard depends on it staying out.
    assert importlib.util.find_spec(module) is None


def _runtime_requirements() -> set[str]:
    pyproject = tomllib.loads((BACKEND / "pyproject.toml").read_text(encoding="utf-8"))
    return {_normalize(re.split(r"[\s\[<>=!~;]", spec, maxsplit=1)[0]) for spec in pyproject["project"]["dependencies"]}


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _imported_top_level_modules() -> set[str]:
    modules: set[str] = set()
    for path in (BACKEND / "src").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                modules.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules.add(node.module.split(".")[0])
    return {m for m in modules if m not in sys.stdlib_module_names and m != "videokit"}


def test_every_third_party_import_is_a_declared_runtime_dependency() -> None:
    # A module that only arrives transitively (or through the dev group) vanishes from the image the
    # day the dependency that brought it stops doing so: httpx left with google-genai 3.0.
    declared = _runtime_requirements()
    providers = packages_distributions()
    undeclared = sorted(
        module
        for module in _imported_top_level_modules()
        if not {_normalize(dist) for dist in providers.get(module, [module])} & declared
    )
    assert undeclared == []
