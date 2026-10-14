"""The codec check embedded in scripts/docker_smoke.sh, run on the host with a failing fake ffmpeg."""

import os
import subprocess
import sys
from pathlib import Path

SCRIPT = (Path(__file__).resolve().parents[3] / "scripts" / "docker_smoke.sh").read_text()
SECRET = "secret-detail-must-not-leak"


def _codec_check() -> str:
    snippets = SCRIPT.split("""docker run --rm --entrypoint python "$image" -c '""")[1:]
    codec = [s for s in snippets if "codec check failed" in s]
    assert len(codec) == 1
    return codec[0].split("\n' ||")[0]


def test_codec_failure_names_the_clip_and_exception_type_only(tmp_path: Path) -> None:
    ffmpeg = tmp_path / "ffmpeg"
    ffmpeg.write_text(f'#!/bin/sh\n[ "$2" = "-encoders" ] && exit 0\necho "{SECRET}" >&2\nexit 1\n')
    ffmpeg.chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}"}
    done = subprocess.run([sys.executable, "-c", _codec_check()], env=env, capture_output=True, text=True, timeout=60)
    assert done.returncode == 1
    assert done.stderr.strip() == "codec check failed: h264-aac.mp4 (CalledProcessError)"
    assert SECRET not in done.stdout + done.stderr


def _mp3_check() -> str:
    snippets = SCRIPT.split("""docker run --rm --entrypoint sh "$image" -c '""")[1:]
    mp3 = [s for s in snippets if "libmp3lame" in s]
    assert len(mp3) == 1
    return mp3[0].split("\n' ||")[0]


def test_script_encodes_a_lavfi_tone_to_mp3_at_192k_and_probes_it() -> None:
    check = _mp3_check()
    for needle in ("lavfi", "-c:a libmp3lame", "-b:a 192k", "ffprobe", "mp3"):
        assert needle in check
    assert "the image cannot encode MP3" in SCRIPT


def _run_mp3_check(tmp_path: Path, ffmpeg_body: str, probe_output: str) -> subprocess.CompletedProcess[str]:
    ffmpeg = tmp_path / "ffmpeg"
    ffmpeg.write_text(f"#!/bin/sh\n{ffmpeg_body}\n")
    ffprobe = tmp_path / "ffprobe"
    ffprobe.write_text(f"#!/bin/sh\necho {probe_output}\n")
    ffmpeg.chmod(0o755)
    ffprobe.chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}"}
    return subprocess.run(["sh", "-c", _mp3_check()], env=env, capture_output=True, text=True, timeout=60)


def test_mp3_check_fails_when_the_encoder_is_missing(tmp_path: Path) -> None:
    assert _run_mp3_check(tmp_path, "exit 1", "mp3").returncode != 0


def test_mp3_check_fails_when_the_output_is_not_mp3(tmp_path: Path) -> None:
    assert _run_mp3_check(tmp_path, "exit 0", "aac").returncode != 0


def test_mp3_check_passes_when_encoding_and_probe_succeed(tmp_path: Path) -> None:
    assert _run_mp3_check(tmp_path, "exit 0", "mp3").returncode == 0
