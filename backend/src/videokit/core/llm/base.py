"""Text-generation seam (contracts/internal-interfaces.md). Provider status/errors are shared with `core.transcribe`."""

from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from videokit.core.transcribe.base import AllProvidersFailed, ProviderError, ProviderStatus

__all__ = [
    "AllProvidersFailed",
    "GenerationRequest",
    "GenerationResponse",
    "LLMProvider",
    "LabeledFrame",
    "ProviderError",
    "ProviderStatus",
    "Turn",
    "Usage",
    "format_timestamp",
]


def format_timestamp(seconds: float) -> str:
    """``m:ss`` below one hour, ``h:mm:ss`` from one hour on (fractions are dropped)."""
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


@dataclass(frozen=True)
class LabeledFrame:
    """One video frame at ``t_seconds``; providers label it ``[frame m:ss]``."""

    t_seconds: float
    jpeg: bytes


@dataclass(frozen=True)
class Turn:
    """A follow-up message after the stable prefix: an instruction, a prior output or repair feedback."""

    role: Literal["user", "assistant"]
    text: str


@dataclass(frozen=True)
class GenerationRequest:
    system: str
    context_text: str  # timestamped transcript lines "[m:ss] text"
    frames: list[LabeledFrame]
    schema: dict[str, Any]  # JSON schema of the expected output
    turns: list[Turn]


@dataclass(frozen=True)
class Usage:
    """Usage of one or more calls (job ``usage`` JSON in data-model.md, minus ``cost_usd``)."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    audio_minutes: float = 0.0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            audio_minutes=self.audio_minutes + other.audio_minutes,
        )


@dataclass(frozen=True)
class GenerationResponse:
    data: dict[str, Any]  # parsed JSON output
    usage: Usage
    provider: str  # "<provider>:<model>", e.g. "anthropic:claude-opus-5-5"

    @property
    def model(self) -> str:
        return self.provider.partition(":")[2]


@runtime_checkable
class LLMProvider(Protocol):
    name: str
    model: str

    def check(self) -> ProviderStatus:
        """Startup validation; never makes calls that cost money."""
        ...

    def generate(self, req: GenerationRequest) -> GenerationResponse: ...
