"""Tests for scripts/gpu_check.py with fake `ctranslate2` / `faster_whisper` modules (no GPU)."""

from __future__ import annotations

import importlib.util
import re
import sys
import types
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "gpu_check.py"
SECRET = "secret-detail-must-not-leak"

Run = Callable[..., tuple[int, str, str]]


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gpu_check", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Inner:
    def __init__(self, device: str) -> None:
        self.device = device


class _Model:
    device = "cuda"
    fail_load = False
    fail_transcribe = False

    def __init__(self, name: str, device: str, compute_type: str, **_: Any) -> None:
        if type(self).fail_load:
            raise OSError(SECRET)
        self.calls = (name, device, compute_type)
        self.model = _Inner(type(self).device)

    def transcribe(self, audio: str, **_: Any) -> tuple[Iterator[object], object]:
        if type(self).fail_transcribe:
            raise RuntimeError(SECRET)
        return iter([object(), object()]), object()


@pytest.fixture
def run(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> Run:
    def _run(
        *,
        gpus: int = 1,
        device: str = "cuda",
        fail_load: bool = False,
        fail_transcribe: bool = False,
    ) -> tuple[int, str, str]:
        ct2 = types.SimpleNamespace(get_cuda_device_count=lambda: gpus)
        model = type(
            "Model",
            (_Model,),
            {"device": device, "fail_load": fail_load, "fail_transcribe": fail_transcribe},
        )
        fw = types.SimpleNamespace(WhisperModel=model)
        monkeypatch.setitem(sys.modules, "ctranslate2", ct2)
        monkeypatch.setitem(sys.modules, "faster_whisper", fw)
        module = _load_script()
        monkeypatch.setattr(module, "make_audio", lambda path: path.write_bytes(b"x"))
        code = int(module.main())
        out = capsys.readouterr()
        assert "Traceback" not in out.out + out.err
        assert SECRET not in out.out + out.err
        return code, out.out, out.err

    return _run


def test_no_gpu(run: Run) -> None:
    assert run(gpus=0) == (1, "", "no GPU visible to the container\n")


def test_model_load_error(run: Run) -> None:
    assert run(fail_load=True) == (1, "", "GPU libraries failed to load: OSError\n")


def test_model_on_cpu(run: Run) -> None:
    assert run(device="cpu") == (1, "", "model is not on the GPU (device: cpu)\n")


def test_transcription_error(run: Run) -> None:
    assert run(fail_transcribe=True) == (
        1,
        "",
        "transcription on the GPU failed: RuntimeError\n",
    )


def test_success(run: Run) -> None:
    code, out, err = run(gpus=2)
    assert code == 0
    assert err == ""
    assert re.fullmatch(r"GPU check passed: 2 GPU\(s\), model load \d+\.\d+s, transcription \d+\.\d+s\n", out)
