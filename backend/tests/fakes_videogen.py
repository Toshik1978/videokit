"""Scripted `VideoGenerator` for tests: no SDK, no network, no cost."""

import subprocess
from collections import deque
from collections.abc import Iterable
from pathlib import Path

from videokit.core.llm.base import Usage
from videokit.core.videogen import Capabilities, PollResult, ProviderStatus, VideoRequest

CAPABILITIES = Capabilities(
    durations_s=[5, 6, 7, 8, 9, 10],
    max_pictures=3,
    video_tokens_per_second={"720p": 5792, "1080p": 8688},
    tokens_per_image=1120,
)

SubmitItem = str | Exception
PollItem = PollResult | Exception | str


def write_clip(
    path: Path,
    *,
    duration_s: float = 1.0,
    vcodec: str = "libx264",
    with_audio: bool = True,
    size: str = "160x120",
    title: str | None = None,
) -> Path:
    """Write a tiny test clip (H.264 + AAC by default) with ffmpeg; the one place the clip command lives.

    ``vcodec`` is an ffmpeg encoder name (``libx265`` for an HEVC clip); ``title`` writes a container tag.
    """
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=green:s={size}:r=10:d={duration_s}"]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={duration_s}", "-c:a", "aac"]
    if title is not None:
        cmd += ["-metadata", f"title={title}"]
    cmd += ["-c:v", vcodec, "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, check=True, capture_output=True)
    return path


class FakeVideoGenerator:
    """Accepts, polls and fails as scripted.

    ``submits`` items, one per ``submit`` call: a ``str`` -> accepted with that ref; an ``Exception`` -> raised.
    Default: every call is accepted as ``"op-<n>"``; a given script that runs out fails the test.

    ``polls`` items, one per ``poll`` call: ``"pending"`` -> not done yet; ``"done"`` -> writes a tiny H.264+AAC
    clip into ``out_dir`` and reports it; a ``PollResult`` -> returned as is; an ``Exception`` -> raised.
    Default: ``["done"]``; a script that runs out fails the test.

    A finished clip reports ``usage`` when given, else the documented rate for the last submitted request:
    ``duration_s`` x ``caps.video_tokens_per_second[resolution]`` video tokens and ``tokens_per_image`` input
    tokens per picture (research V8).

    Calls are recorded: ``submit_count`` / ``requests``, ``poll_refs``, ``cancelled``, ``forgotten``.
    """

    def __init__(
        self,
        *,
        name: str = "fake",
        model: str = "fake-model",
        submits: Iterable[SubmitItem] | None = None,
        polls: Iterable[PollItem] | None = None,
        caps: Capabilities = CAPABILITIES,
        usage: Usage | None = None,
    ) -> None:
        self.name = name
        self.model = model
        self.caps = caps
        self.usage = usage
        self.submit_script: deque[SubmitItem] | None = deque(submits) if submits is not None else None
        self.poll_script: deque[PollItem] = deque(["done"] if polls is None else polls)
        self.requests: list[VideoRequest] = []
        self.poll_refs: list[str] = []
        self.cancelled: list[str] = []
        self.forgotten: list[str] = []

    @property
    def submit_count(self) -> int:
        return len(self.requests)

    def check(self) -> ProviderStatus:
        return ProviderStatus(ready=True, detail=self.model)

    def capabilities(self) -> Capabilities:
        return self.caps

    def submit(self, req: VideoRequest) -> str:
        self.requests.append(req)
        if self.submit_script is None:
            return f"op-{len(self.requests)}"
        if not self.submit_script:
            raise AssertionError("FakeVideoGenerator submit script exhausted")
        item = self.submit_script.popleft()
        if isinstance(item, Exception):
            raise item
        return item

    def poll(self, ref: str, out_dir: Path) -> PollResult:
        self.poll_refs.append(ref)
        if not self.poll_script:
            raise AssertionError("FakeVideoGenerator poll script exhausted")
        item = self.poll_script.popleft()
        if isinstance(item, Exception):
            raise item
        if isinstance(item, PollResult):
            return item
        if item == "pending":
            return PollResult(done=False, video=None, usage=Usage(), error=None)
        if item == "done":
            video = write_clip(out_dir / "raw.mp4")
            return PollResult(done=True, video=video, usage=self._usage(), error=None)
        raise AssertionError(f"unknown poll script item {item!r}")

    def _usage(self) -> Usage:
        if self.usage is not None:
            return self.usage
        if not self.requests:
            return Usage()
        req = self.requests[-1]
        return Usage(
            input_tokens=len(req.pictures) * self.caps.tokens_per_image,
            video_output_tokens=req.duration_s * self.caps.video_tokens_per_second[req.resolution],
        )

    def cancel(self, ref: str) -> None:
        self.cancelled.append(ref)

    def forget(self, ref: str) -> None:
        self.forgotten.append(ref)
