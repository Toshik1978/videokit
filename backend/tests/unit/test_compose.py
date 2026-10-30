"""Tests for compose.yaml: it runs the published image and reserves no GPU."""

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


def test_no_gpu_reservation() -> None:
    service = _service()
    assert "deploy" not in service or "devices" not in (service["deploy"].get("resources", {}).get("reservations", {}))
    text = COMPOSE.read_text(encoding="utf-8").lower()
    assert "nvidia" not in text
    assert "gpu" not in text


def test_user_volume_port_env_and_restart_unchanged() -> None:
    service = _service()
    assert service["user"] == "${PUID:-1000}:${PGID:-1000}"
    assert service["volumes"] == ["./.data:/data"]
    assert service["ports"] == ["8000:8000"]
    assert service["env_file"] == ".env"
    assert service["restart"] == "unless-stopped"
