"""Runnable app for the Playwright end-to-end tests: the built SPA served with fake providers.

    uv run python tests/e2e_app.py --static ../frontend/dist --video /tmp/clip.mp4 --port 8765

Not a pytest module. It generates a tiny fixture video with ffmpeg, then serves the app with a fake
transcriber, a scripted LLM and a fake video provider (Video Generation enabled without any key), a
temporary DATA_DIR and no network access.
"""

import argparse
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import uvicorn
from pydantic import SecretStr

from fakes_llm import FakeLLM
from fakes_transcribe import FakeTranscriber
from fakes_videogen import FakeVideoGenerator, write_clip
from videokit.core.config import Settings
from videokit.core.llm import LLMChain
from videokit.core.transcribe import TranscriberChain
from videokit.core.videogen import PollResult, Usage, VideoChain
from videokit.core.worker import Providers
from videokit.main import create_app
from videokit.tools.video_generation import pipeline as video_pipeline

RESULT: dict[str, Any] = {
    "language": "en",
    "cover_titles": ["Hidden Gem Resort", "Worth The Price", "Best Pool Ever"],
    "title": "Hotel tour: lobby, pool and rooms",
    "description": "A tour of a small resort.\n\nWe look at the lobby, the pool and a room.",
    "tags": ["hotel review", "travel", "resort"],
    "hashtags": ["#travel", "#hotel", "#resort"],
    "chapters": [
        {"start_seconds": 0, "title": "Arrival"},
        {"start_seconds": 12, "title": "Lobby and pool"},
        {"start_seconds": 24, "title": "Rooms"},
    ],
}
SCRIPT_LENGTH = 200  # enough answers for every upload and regeneration of a whole test run


VIDEO_POLL_INTERVAL_S = 0.05  # the real 10 s wait would make every Video Generation test slow
SLOW_PROMPT_MARKER = "slow"  # a prompt containing it never finishes, so a test can reach Cancel


class E2EVideoGenerator(FakeVideoGenerator):
    """Accepts every request and finishes it on the first poll, unless its prompt contains ``slow``.

    Unscripted on purpose: the Playwright projects run in parallel against one app, so the number of
    jobs is not known in advance.
    """

    def __init__(self) -> None:
        super().__init__(model="e2e-video")
        self._slow: set[str] = set()

    def submit(self, req: Any) -> str:
        ref = super().submit(req)
        if SLOW_PROMPT_MARKER in req.prompt.lower():
            self._slow.add(ref)
        return ref

    def poll(self, ref: str, out_dir: Path) -> PollResult:
        self.poll_refs.append(ref)
        if ref in self._slow:
            return PollResult(done=False, video=None, usage=Usage(), error=None)
        video = write_clip(out_dir / "raw.mp4")
        return PollResult(done=True, video=video, usage=self._usage(), error=None)


def make_video(out: Path) -> None:
    """Write a 48 s, 160x120 clip (three solid-color segments with a tone): long enough for chapters."""
    cmd = ["ffmpeg", "-v", "error", "-y"]
    for color in ("red", "blue", "green"):
        cmd += ["-f", "lavfi", "-i", f"color=c={color}:s=160x120:r=10:d=16"]
    cmd += ["-f", "lavfi", "-i", "sine=frequency=440:duration=48"]
    cmd += ["-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]", "-map", "[v]", "-map", "3:a", "-c:a", "aac"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)]
    subprocess.run(cmd, check=True, capture_output=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--static", type=Path, required=True, help="built SPA directory (frontend/dist)")
    parser.add_argument("--video", type=Path, required=True, help="where to write the fixture video")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    args.video.parent.mkdir(parents=True, exist_ok=True)
    make_video(args.video)

    with tempfile.TemporaryDirectory(prefix="videokit-e2e-") as data_dir:
        video_pipeline.POLL_INTERVAL_S = VIDEO_POLL_INTERVAL_S
        settings = Settings(
            data_dir=Path(data_dir),
            transcribe_providers=["openai"],
            openai_transcribe_api_key=SecretStr("e2e-placeholder"),
            anthropic_api_key=SecretStr("e2e-placeholder"),
            gemini_api_key=SecretStr("e2e-placeholder"),
            video_providers=["omni"],
        )
        providers = Providers(
            transcriber=TranscriberChain([FakeTranscriber()]),
            llm=LLMChain([FakeLLM([RESULT] * SCRIPT_LENGTH)]),
            video=VideoChain([E2EVideoGenerator()]),
        )
        app = create_app(settings, providers, static_dir=args.static)
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
