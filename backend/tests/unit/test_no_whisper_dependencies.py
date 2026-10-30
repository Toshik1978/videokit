"""Remote transcription (feature 005): the in-process Whisper and GPU packages must be gone from the lock."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
REMOVED = ["faster-whisper", "ctranslate2", "onnxruntime", "huggingface-hub", "av", "nvidia-cublas-cu12"]


@pytest.mark.parametrize("package", REMOVED)
def test_package_is_not_locked(package: str) -> None:
    lock = tomllib.loads((BACKEND / "uv.lock").read_text(encoding="utf-8"))

    assert package not in {entry["name"] for entry in lock["package"]}


@pytest.mark.parametrize("package", REMOVED)
def test_package_is_not_a_declared_dependency(package: str) -> None:
    project = tomllib.loads((BACKEND / "pyproject.toml").read_text(encoding="utf-8"))["project"]

    declared = {
        requirement.split(";")[0].strip().split("[")[0].split("<")[0].split(">")[0].split("=")[0].strip()
        for requirement in project["dependencies"]
    }
    assert package not in declared
