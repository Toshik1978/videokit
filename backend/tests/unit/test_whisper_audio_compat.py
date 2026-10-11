"""Regression: faster-whisper's audio decoder must work with the resolved ``av`` version.

faster-whisper 1.2.1 calls ``av.open(..., metadata_errors=...)``, which newer ``av`` majors reject.
This decodes a tiny ffmpeg-generated file (no model, no GPU, no network).
"""

import importlib
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@pytest.mark.parametrize("suffix", ["wav", "mp3"])
def test_decode_audio_with_resolved_av(tmp_path: Path, suffix: str) -> None:
    source = tmp_path / f"tone.{suffix}"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.5", str(source)],
        check=True,
    )

    decode_audio = importlib.import_module("faster_whisper.audio").decode_audio
    samples = decode_audio(str(source), sampling_rate=16000)

    assert samples.dtype.name == "float32"
    assert 7000 < samples.shape[0] < 9000  # ~0.5 s at 16 kHz
