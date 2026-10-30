"""The constitution names video generation providers and keeps its version stamps consistent."""

from __future__ import annotations

import re
from pathlib import Path

CONSTITUTION = Path(__file__).resolve().parents[3] / ".specify" / "memory" / "constitution.md"


def _text() -> str:
    return CONSTITUTION.read_text(encoding="utf-8")


def test_principle_iv_lists_video_generation_providers() -> None:
    principle = _text().split("### IV.", 1)[1].split("### V.", 1)[0]

    assert "video generation providers" in " ".join(principle.split())


def test_technology_section_names_the_video_generation_sdk() -> None:
    technology = _text().split("## Technology & Operational Constraints", 1)[1].split("## Development Workflow", 1)[0]

    assert "Video generation: Google Gemini Omni via the official `google-genai` SDK" in technology


def test_principle_iv_has_no_local_whisper_and_no_optional_gpu() -> None:
    principle = " ".join(_text().split("### IV.", 1)[1].split("### V.", 1)[0].split())

    assert "local Whisper" not in principle
    assert "A GPU MUST be optional" not in principle
    assert "VideoKit itself MUST NOT require a GPU" in principle


def test_technology_section_has_no_faster_whisper_and_a_cpu_only_image() -> None:
    technology = " ".join(
        _text().split("## Technology & Operational Constraints", 1)[1].split("## Development Workflow", 1)[0].split()
    )

    assert "faster-whisper" not in technology
    assert "single CPU-only image" in technology
    assert "without GPU access" in technology


def test_sync_impact_report_matches_the_version_line() -> None:
    text = _text()
    reported = re.search(r"Version change: \S+ -> (\S+)", text)
    stamped = re.search(r"\*\*Version\*\*: (\S+) \|", text)

    assert reported and stamped
    assert reported.group(1) == stamped.group(1) == "1.6.0"
