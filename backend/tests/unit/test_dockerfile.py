"""Static checks on the Dockerfile (no Docker needed)."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DOCKERFILE = (ROOT / "Dockerfile").read_text()
PYTHON_VERSION = (ROOT / "backend" / ".python-version").read_text().strip()


def _logical_lines() -> list[str]:
    return re.sub(r"\\\n\s*", " ", DOCKERFILE).splitlines()


def test_runtime_stage_is_python_slim_matching_python_version() -> None:
    froms = [line for line in _logical_lines() if line.startswith("FROM ")]
    assert froms[-1] == f"FROM python:{PYTHON_VERSION}-slim"


def test_no_cuda_base_image() -> None:
    assert "nvidia/cuda" not in DOCKERFILE


def test_no_uv_python_install() -> None:
    assert "uv python install" not in DOCKERFILE
    assert "/opt/python" not in DOCKERFILE


def test_both_uv_sync_steps_use_cache_mount() -> None:
    syncs = [line for line in _logical_lines() if "uv sync" in line]
    assert len(syncs) == 2
    for line in syncs:
        assert line.startswith("RUN --mount=type=cache,target=/root/.cache/uv ")


def test_uv_is_bind_mounted_not_copied_into_the_image() -> None:
    copies = [line for line in _logical_lines() if line.startswith("COPY")]
    assert not [line for line in copies if "--from=uv" in line or "astral-sh/uv" in line]
    # A named FROM stage is what Dependabot's docker ecosystem tracks; RUN --mount=from=<image> is not.
    # Any tag matches, so a Dependabot bump of the pin does not break this test.
    assert any(re.fullmatch(r"FROM ghcr\.io/astral-sh/uv:\S+ AS uv", line) for line in _logical_lines())
    syncs = [line for line in _logical_lines() if "uv sync" in line]
    assert len(syncs) == 2
    for line in syncs:
        assert "--mount=from=uv,source=/uv,target=/usr/local/bin/uv" in line


def test_uv_sync_installs_everything_locked() -> None:
    for line in [line for line in _logical_lines() if "uv sync" in line]:
        assert "--no-install-package" not in line.replace("--no-install-project", "")


def test_uv_uses_system_python() -> None:
    assert "UV_PYTHON_DOWNLOADS=never" in DOCKERFILE
    assert "UV_PYTHON=/usr/local/bin/python3" in DOCKERFILE


def test_image_is_cpu_only() -> None:
    assert "ldconfig" not in DOCKERFILE
    assert "nvidia" not in DOCKERFILE.lower()
    assert "NVIDIA_" not in DOCKERFILE
    assert "HF_HOME" not in DOCKERFILE


def test_xdg_cache_home_stays_under_data() -> None:
    assert "XDG_CACHE_HOME=/data/cache" in DOCKERFILE


def test_oci_labels() -> None:
    for label in (
        'org.opencontainers.image.title="VideoKit"',
        "org.opencontainers.image.description=",
        'org.opencontainers.image.authors="Anton Krivenko"',
        'org.opencontainers.image.licenses="MIT"',
    ):
        assert label in DOCKERFILE
