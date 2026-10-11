"""Tests for compose.yaml: it runs the published image with GPU access by default."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

COMPOSE = Path(__file__).resolve().parents[3] / "compose.yaml"


def _service() -> dict[str, Any]:
    data = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    service = data["services"]["videokit"]
    assert isinstance(service, dict)
    return service


def test_runs_the_published_image() -> None:
    service = _service()
    assert service["image"] == "ghcr.io/toshik1978/videokit:latest"
    assert "build" not in service


def test_gpu_reservation_is_enabled() -> None:
    devices = _service()["deploy"]["resources"]["reservations"]["devices"]
    assert devices == [{"driver": "nvidia", "count": "all", "capabilities": ["gpu"]}]


def test_gpu_block_documents_how_to_remove_it() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    assert "NVIDIA container toolkit" in text
    assert "delete this block" in text.lower()


def test_user_volume_port_env_and_restart_unchanged() -> None:
    service = _service()
    assert service["user"] == "${PUID:-1000}:${PGID:-1000}"
    assert service["volumes"] == ["./.data:/data"]
    assert service["ports"] == ["8000:8000"]
    assert service["env_file"] == ".env"
    assert service["restart"] == "unless-stopped"
