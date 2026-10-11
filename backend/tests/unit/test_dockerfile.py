"""Static checks on the Dockerfile and the GPU dependency (no Docker needed)."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DOCKERFILE = (ROOT / "Dockerfile").read_text()
PYPROJECT = (ROOT / "backend" / "pyproject.toml").read_text()
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
    assert not [line for line in _logical_lines() if line.startswith("COPY") and "uv" in line.split()[-2:]]
    assert not [line for line in _logical_lines() if line.startswith("COPY") and "astral-sh/uv" in line]
    # A named FROM stage is what Dependabot's docker ecosystem tracks; RUN --mount=from=<image> is not.
    assert "FROM ghcr.io/astral-sh/uv:0.13 AS uv" in _logical_lines()
    syncs = [line for line in _logical_lines() if "uv sync" in line]
    assert len(syncs) == 2
    for line in syncs:
        assert "--mount=from=uv,source=/uv,target=/usr/local/bin/uv" in line


def test_nvrtc_is_not_installed() -> None:
    syncs = [line for line in _logical_lines() if "uv sync" in line]
    assert len(syncs) == 2
    for line in syncs:
        assert "--no-install-package nvidia-cuda-nvrtc-cu12" in line


def test_uv_uses_system_python() -> None:
    assert "UV_PYTHON_DOWNLOADS=never" in DOCKERFILE
    assert "UV_PYTHON=/usr/local/bin/python3" in DOCKERFILE


def test_ldconfig_registers_pip_nvidia_libs() -> None:
    assert 'find /app/.venv -path "*/nvidia/*/lib" -type d > /etc/ld.so.conf.d/nvidia.conf && ldconfig' in " ".join(
        _logical_lines()
    )


def test_nvidia_env_vars() -> None:
    assert "NVIDIA_VISIBLE_DEVICES=all" in DOCKERFILE
    assert "NVIDIA_DRIVER_CAPABILITIES=compute,utility" in DOCKERFILE


def test_oci_labels() -> None:
    for label in (
        'org.opencontainers.image.title="VideoKit"',
        "org.opencontainers.image.description=",
        'org.opencontainers.image.authors="Anton Krivenko"',
        'org.opencontainers.image.licenses="MIT"',
    ):
        assert label in DOCKERFILE


def test_cublas_from_pip_without_cudnn() -> None:
    assert '"nvidia-cublas-cu12;' in PYPROJECT
    assert "nvidia-cudnn" not in PYPROJECT


def test_cublas_is_limited_to_linux() -> None:
    assert "\"nvidia-cublas-cu12; sys_platform == 'linux'\"" in PYPROJECT
