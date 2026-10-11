"""Transcription seam (contracts/internal-interfaces.md) and the provider types shared with `core.llm`."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from videokit.core.errors import AppError


@dataclass(frozen=True)
class ProviderStatus:
    """Validation result of one provider; ``detail`` is non-secret (e.g. ``model=whisper-1`` or why not ready)."""

    ready: bool
    detail: str = ""


class ProviderError(Exception):
    """A provider could not produce a result; the chain moves on to the next provider."""


class AllProvidersFailed(AppError):
    """Every provider of a chain failed for ``stage`` ("transcription" or "generation")."""

    def __init__(self, stage: str) -> None:
        super().__init__("providers_failed", f"All {stage} providers failed. Try again later.", 502)
        self.stage = stage


@dataclass(frozen=True)
class AudioFiles:
    """Extracted audio of one job: 16 kHz mono WAV (local Whisper) and 32 kbps mono MP3 (cloud upload)."""

    wav: Path
    mp3: Path


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class Transcript:
    """Timestamped transcript; ``no_speech`` marks an empty or near-empty transcript (FR-033)."""

    language: str | None
    language_probability: float
    segments: list[Segment]
    audio_minutes: float
    no_speech: bool
    provider: str  # name of the provider that produced it, e.g. "local"
    model: str = ""  # the model that produced it, e.g. "whisper-1" (prices cloud transcription)


@runtime_checkable
class Transcriber(Protocol):
    name: str

    def check(self) -> ProviderStatus:
        """Startup validation; never makes calls that cost money."""
        ...

    def transcribe(self, audio: AudioFiles) -> Transcript: ...
