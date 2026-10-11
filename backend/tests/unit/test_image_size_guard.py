"""Tests for scripts/image_size_guard.sh with fake `docker` and `gzip` programs on PATH.

The fake `docker save` writes FAKE_SAVE_BYTES bytes; the fake `gzip` is a pass-through, so the
measured size equals that number exactly (real gzip would shrink the filler to almost nothing).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "image_size_guard.sh"
LIMIT = 1_250_000_000

FAKE_DOCKER = """\
#!/bin/sh
[ "$1" = save ] || exit 64
if [ "${FAKE_SAVE_FAIL:-}" = 1 ]; then
  echo "boom" >&2
  exit 1
fi
head -c "$FAKE_SAVE_BYTES" /dev/zero
"""

FAKE_GZIP = """\
#!/bin/sh
cat
"""


@pytest.fixture
def fake_bin(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("docker", FAKE_DOCKER), ("gzip", FAKE_GZIP)):
        path = bin_dir / name
        path.write_text(body)
        path.chmod(0o755)
    return bin_dir


def run(fake_bin: Path, *args: str, size: int = 0, fail: bool = False) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "FAKE_SAVE_BYTES": str(size),
        "FAKE_SAVE_FAIL": "1" if fail else "0",
    }
    return subprocess.run([str(SCRIPT), *args], env=env, capture_output=True, text=True, check=False)


def test_under_limit_prints_size_and_passes(fake_bin: Path) -> None:
    result = run(fake_bin, "videokit:ci", size=1_060_000_000)
    assert result.returncode == 0
    assert result.stdout.strip() == "image size: 1.06 GB (limit 1.25 GB)"


def test_exactly_at_limit_passes(fake_bin: Path) -> None:
    result = run(fake_bin, "videokit:ci", size=LIMIT)
    assert result.returncode == 0
    assert result.stdout.strip() == "image size: 1.25 GB (limit 1.25 GB)"


def test_over_limit_fails_with_size_and_limit_on_stderr(fake_bin: Path) -> None:
    result = run(fake_bin, "videokit:ci", size=LIMIT + 1)
    assert result.returncode == 1
    assert result.stdout == ""
    assert "1250000001" in result.stderr
    assert "1250000000" in result.stderr


def test_missing_argument_is_a_usage_error(fake_bin: Path) -> None:
    result = run(fake_bin)
    assert result.returncode == 2
    assert "usage" in result.stderr.lower()


def test_docker_save_failure_is_an_error(fake_bin: Path) -> None:
    result = run(fake_bin, "videokit:ci", size=10, fail=True)
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.strip()
