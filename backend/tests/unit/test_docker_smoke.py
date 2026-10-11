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
