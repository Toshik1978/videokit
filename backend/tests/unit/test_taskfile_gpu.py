"""`task docker:run` adds --gpus all for GPU=true, given as an env var or as a CLI variable."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]

pytestmark = pytest.mark.skipif(shutil.which("task") is None, reason="task is not installed")


def _dry_run(*args: str, env_gpu: str | None = None) -> str:
    env = {k: v for k, v in os.environ.items() if k != "GPU"}
    if env_gpu is not None:
        env["GPU"] = env_gpu
    result = subprocess.run(
        ["task", "docker:run", "--dry", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout + result.stderr


def test_no_gpu_by_default() -> None:
    assert "--gpus" not in _dry_run()


def test_gpu_env_var_adds_gpus_all() -> None:
    assert "--gpus all" in _dry_run(env_gpu="true")


def test_gpu_cli_variable_adds_gpus_all() -> None:
    assert "--gpus all" in _dry_run("GPU=true")


def test_gpu_false_adds_nothing() -> None:
    assert "--gpus" not in _dry_run(env_gpu="false")
