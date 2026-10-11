#!/usr/bin/env python3
"""Check that the built image can run Whisper on an NVIDIA GPU.

Runs inside the image (`task docker:gpu-check` bind-mounts it), so no check code ships in the
product. Prints one line: success on stdout, the first failure on stderr (exit 1). Only exception
types are printed, never messages or tracebacks.

Failure classification for `WhisperModel(...)`: an error is a GPU-library failure when it is an
ImportError, OSError or RuntimeError whose message mentions CUDA, cuBLAS or cuDNN (ctranslate2
reports a missing or mismatched `libcublas.so.12` / `libcudnn` that way). The message is only
inspected to decide, never printed. Any other error, such as a failed first-run model download from
Hugging Face, is reported as "model could not be loaded".
"""

from __future__ import annotations

import importlib
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def make_audio(path: Path) -> None:
    """Write 5 s of a 440 Hz tone (16 kHz mono WAV) to `path`."""
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=5",
            "-ar",
            "16000",
            "-ac",
            "1",
            str(path),
        ],
        check=True,
    )


_GPU_LIBRARY_ERRORS = (ImportError, OSError, RuntimeError)
_GPU_LIBRARY_HINTS = ("cuda", "cublas", "cudnn")


def _is_gpu_library_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return isinstance(exc, _GPU_LIBRARY_ERRORS) and any(hint in text for hint in _GPU_LIBRARY_HINTS)


def _fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 1


def main() -> int:
    try:
        ctranslate2 = importlib.import_module("ctranslate2")
        whisper_model = importlib.import_module("faster_whisper").WhisperModel
        gpus = int(ctranslate2.get_cuda_device_count())
    except Exception as exc:
        return _fail(f"GPU libraries failed to load: {type(exc).__name__}")
    if gpus == 0:
        return _fail("no GPU visible to the container")

    with tempfile.TemporaryDirectory() as tmp:
        audio = Path(tmp) / "tone.wav"
        try:
            make_audio(audio)
        except Exception as exc:
            return _fail(f"could not generate test audio: {type(exc).__name__}")

        started = time.monotonic()
        try:
            model = whisper_model("tiny", device="cuda", compute_type="float16")
        except Exception as exc:
            reason = "GPU libraries failed to load" if _is_gpu_library_error(exc) else "model could not be loaded"
            return _fail(f"{reason}: {type(exc).__name__}")
        load_seconds = time.monotonic() - started

        device = model.model.device
        if device != "cuda":
            return _fail(f"model is not on the GPU (device: {device})")

        started = time.monotonic()
        try:
            segments, _info = model.transcribe(str(audio))
            for _segment in segments:
                pass
        except Exception as exc:
            return _fail(f"transcription on the GPU failed: {type(exc).__name__}")
        transcribe_seconds = time.monotonic() - started

    print(f"GPU check passed: {gpus} GPU(s), model load {load_seconds:.1f}s, transcription {transcribe_seconds:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
