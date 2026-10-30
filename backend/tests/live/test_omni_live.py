"""Opt-in smoke tests against the real Gemini Omni API (`pytest -m live -k omni`, quickstart section 3).

Skipped unless ``GEMINI_API_KEY`` is set. Each run costs about $2.10 (a 5 s 720p clip delivered inline and a 10 s
1080p clip delivered as a URI). These tests confirm the **live-verify** items of research.md: the accepted
``duration`` format (V9), both delivery modes (V2), reported video usage (V8), how a safety refusal surfaces (V10)
and that a stored interaction can be deleted (V6). Nothing here prints keys, prompts or provider messages.
"""

import os
import subprocess
import time
from pathlib import Path

import pytest

from videokit.core.config import Settings
from videokit.core.media import MediaService
from videokit.core.videogen import Picture, PollResult, VideoError, VideoRequest
from videokit.core.videogen.base import Resolution
from videokit.core.videogen.omni import OmniProvider

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.environ.get("GEMINI_API_KEY"), reason="GEMINI_API_KEY not set"),
]

POLL_INTERVAL_S = 10
WAIT_CAP_S = 20 * 60


def _picture(tmp_path: Path) -> Picture:
    png, jpeg = tmp_path / "ref.png", tmp_path / "ref.jpg"
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=512x512:d=1", "-frames:v", "1", str(png)]
    subprocess.run(cmd, check=True, capture_output=True)
    MediaService(Settings()).normalize_picture(png, jpeg)
    return Picture(position=1, jpeg=jpeg.read_bytes())


def _wait(provider: OmniProvider, ref: str, out_dir: Path) -> PollResult:
    deadline = time.monotonic() + WAIT_CAP_S
    while time.monotonic() < deadline:
        result = provider.poll(ref, out_dir)
        if result.done:
            return result
        time.sleep(POLL_INTERVAL_S)
    provider.cancel(ref)
    pytest.fail("the interaction did not finish within the wait cap")


@pytest.mark.parametrize(
    ("duration_s", "resolution"),
    [(5, "720p"), (10, "1080p")],  # inline delivery; URI delivery (larger than 4 MB), the form's default resolution
)
def test_omni_generates_a_clip_from_a_prompt_and_a_picture_and_forgets_it(
    tmp_path: Path, duration_s: int, resolution: Resolution
) -> None:
    provider = OmniProvider(Settings())
    assert provider.check().ready
    req = VideoRequest(
        prompt="A slow camera pan over the colorful test pattern in the picture.",
        pictures=[_picture(tmp_path)],
        duration_s=duration_s,
        aspect_ratio="16:9",
        resolution=resolution,
    )

    ref = provider.submit(req)  # raising here means the `duration` format ("5s") was rejected: see research.md V9
    try:
        result = _wait(provider, ref, tmp_path)
        assert result.error is None
        assert result.video is not None
        assert result.video.stat().st_size > 0  # inline bytes, or the download of the delivery URI
        assert b"ftyp" in result.video.read_bytes()[:64]  # an MP4 container, not a URI or an error body
        assert result.usage.video_output_tokens > 0  # reported, or derived at the documented rate
        assert result.usage.input_tokens > 0
    finally:
        provider.forget(ref)

    with pytest.raises(VideoError):  # deleted: the provider no longer knows the interaction
        provider.poll(ref, tmp_path)


def test_omni_refused_prompt_surfaces_as_content_blocked(tmp_path: Path) -> None:
    provider = OmniProvider(Settings())
    req = VideoRequest(
        prompt="Photorealistic footage of a real, named head of state committing a violent crime.",
        pictures=[],
        duration_s=5,
        aspect_ratio="16:9",
        resolution="720p",
    )

    try:
        ref = provider.submit(req)
    except VideoError as exc:  # refused up front
        assert exc.code == "content_blocked"
        return

    try:
        result = _wait(provider, ref, tmp_path)
        assert result.error is not None, "the prompt was not refused; pick a stronger one"
        assert result.error.code == "content_blocked"
    finally:
        provider.forget(ref)
