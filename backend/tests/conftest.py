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
    boundary is a known scene change. ``audio=True`` adds a mono sine tone for the whole duration, or
    for ``audio_s`` seconds when given (e.g. audio that outlasts the video stream).

    Audio options (all imply ``audio``): ``two_audio_tracks`` adds a second, stereo track flagged default
    after the mono first one; ``audio_channels`` and ``audio_rate`` set the (first) track's layout, e.g.
    6 or 96000; ``location`` writes a neutral ``location`` metadata tag.
    """

    def factory(
        name: str = "clip.mp4",
        *,
        colors: Sequence[str] = ("red", "blue"),
        segment_s: float = 3.0,
        audio: bool = False,
        audio_s: float | None = None,
        two_audio_tracks: bool = False,
        audio_channels: int = 1,
        audio_rate: int | None = None,
        location: bool = False,
    ) -> Path:
        out = tmp_path / name
        cmd = ["ffmpeg", "-v", "error", "-y"]
        for color in colors:
            cmd += ["-f", "lavfi", "-i", f"color=c={color}:s=160x120:r=10:d={segment_s}"]
        audio = audio or two_audio_tracks or audio_channels != 1 or audio_rate is not None
        tracks = 2 if two_audio_tracks else 1
        if audio:
            for _ in range(tracks):
                cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={audio_s or segment_s * len(colors)}"]
        inputs = "".join(f"[{i}:v]" for i in range(len(colors)))
        cmd += ["-filter_complex", f"{inputs}concat=n={len(colors)}:v=1:a=0[v]", "-map", "[v]"]
        if audio:
            for i in range(tracks):
                cmd += ["-map", f"{len(colors) + i}:a"]
            cmd += ["-c:a", "aac", "-ac:a:0", str(audio_channels)]
            if audio_rate is not None:
                cmd += ["-ar", str(audio_rate)]
            if two_audio_tracks:
                cmd += ["-ac:a:1", "2", "-disposition:a:0", "0", "-disposition:a:1", "default"]
        if location:
            cmd += ["-metadata", "location=+00.0000+000.0000/"]
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
