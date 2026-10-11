"""Shared fixtures: tiny ffmpeg-generated media (no network, no binary files in git)."""

import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from videokit.core.config import Settings

MakeVideo = Callable[..., Path]


@pytest.fixture(autouse=True)
def _isolate_settings_env(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every Settings variable from the process environment so tests are hermetic.

    Tests marked ``live`` need real provider keys from the environment and are left untouched.
    """
    if request.node.get_closest_marker("live"):
        return
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


@pytest.fixture
def make_video(tmp_path: Path) -> MakeVideo:
    """Return a factory ``make_video(name="clip.mp4", *, colors=("red", "blue"), segment_s=3.0, audio=False)``.

    The video is ``len(colors)`` solid-color segments of ``segment_s`` seconds each, so every segment
    boundary is a known scene change. ``audio=True`` adds a mono sine tone for the whole duration.
    """

    def factory(
        name: str = "clip.mp4",
        *,
        colors: Sequence[str] = ("red", "blue"),
        segment_s: float = 3.0,
        audio: bool = False,
    ) -> Path:
        out = tmp_path / name
        cmd = ["ffmpeg", "-v", "error", "-y"]
        for color in colors:
            cmd += ["-f", "lavfi", "-i", f"color=c={color}:s=160x120:r=10:d={segment_s}"]
        if audio:
            cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={segment_s * len(colors)}"]
        inputs = "".join(f"[{i}:v]" for i in range(len(colors)))
        cmd += ["-filter_complex", f"{inputs}concat=n={len(colors)}:v=1:a=0[v]", "-map", "[v]"]
        if audio:
            cmd += ["-map", f"{len(colors)}:a", "-c:a", "aac"]
        cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)]
        subprocess.run(cmd, check=True, capture_output=True)
        return out

    return factory


@pytest.fixture
def make_non_video(tmp_path: Path) -> Callable[[], Path]:
    """Return a factory for a file that is not media at all."""

    def factory() -> Path:
        out = tmp_path / "notes.bin"
        out.write_bytes(b"this is definitely not a video\n" * 20)
        return out

    return factory
