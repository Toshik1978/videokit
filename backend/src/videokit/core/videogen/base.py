"""Video generation seam (contracts/internal-interfaces.md). Provider status and usage are shared with `core.llm`."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from videokit.core.llm.base import ProviderStatus, Usage

__all__ = [
    "Accepted",
    "AspectRatio",
    "Capabilities",
    "Picture",
    "PollResult",
    "ProviderStatus",
    "Resolution",
    "Usage",
    "VideoError",
    "VideoErrorCode",
    "VideoGenerator",
    "VideoRequest",
]

AspectRatio = Literal["9:16", "16:9"]
Resolution = Literal["720p", "1080p"]
VideoErrorCode = Literal[
    "provider_out_of_credit",
    "content_blocked",
    "invalid_picture",
    "provider_unavailable",
    "video_failed",
]


@dataclass(frozen=True)
class Picture:
    """One reference picture, already normalized to JPEG; ``position`` is 1-based."""

    position: int
    jpeg: bytes


@dataclass(frozen=True)
class VideoRequest:
    """One generation request. Clips are always silent: providers request no audio where the API allows it."""

    prompt: str
    pictures: list[Picture]
    duration_s: int
    aspect_ratio: AspectRatio
    resolution: Resolution


@dataclass(frozen=True)
class Capabilities:
    """What a provider offers; also the fallback usage rate and the basis of the cost estimate (research V8)."""

    durations_s: list[int]
    max_pictures: int
    video_tokens_per_second: dict[str, int]  # per resolution, e.g. {"720p": 5792, "1080p": 8688}
    tokens_per_image: int


@dataclass(frozen=True)
class Accepted:
    """A provider took the request; persisted by the tool so a restart can poll instead of resubmitting."""

    provider: str  # "<provider>:<model>"
    ref: str  # provider reference (Omni: the stored interaction id)


class VideoError(Exception):
    """A generation failure, mapped to a user-safe ``code``; the message is never shown or logged (research V10)."""

    def __init__(self, code: VideoErrorCode, message: str = "") -> None:
        super().__init__(message)
        self.code: VideoErrorCode = code


@dataclass(frozen=True)
class PollResult:
    done: bool
    video: Path | None  # the downloaded raw video once done
    usage: Usage  # as reported by the provider, else derived from the documented rate (research V8)
    error: VideoError | None


@runtime_checkable
class VideoGenerator(Protocol):
    name: str
    model: str

    def check(self) -> ProviderStatus:
        """Startup validation; never makes calls that cost money."""
        ...

    def capabilities(self) -> Capabilities: ...

    def submit(self, req: VideoRequest) -> str:
        """Start a generation and return the operation ref; raises `VideoError` when not accepted."""
        ...

    def poll(self, ref: str, out_dir: Path) -> PollResult: ...

    def cancel(self, ref: str) -> None:
        """Best-effort."""
        ...

    def forget(self, ref: str) -> None:
        """Best-effort delete of the stored request at the provider (privacy, research V6)."""
        ...
