"""Taskfile has no GPU tooling: `docker:run` passes no --gpus and `docker:gpu-check` is gone."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]

pytestmark = pytest.mark.skipif(shutil.which("task") is None, reason="task is not installed")


def test_docker_run_has_no_gpu_flags() -> None:
    result = subprocess.run(["task", "docker:run", "--dry"], cwd=ROOT, capture_output=True, text=True, check=True)
    output = result.stdout + result.stderr
    assert "--gpus" not in output
    assert '-v "$PWD/.data:/data"' in output
    assert "--env-file .env" in output


def test_gpu_check_task_and_script_are_gone() -> None:
    assert "gpu" not in (ROOT / "Taskfile.yml").read_text(encoding="utf-8").lower()
    assert not (ROOT / "scripts" / "gpu_check.py").exists()
