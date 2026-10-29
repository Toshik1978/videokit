"""Shared fixtures: tiny ffmpeg-generated media (no network, no binary files in git)."""

import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from fakes_videogen import write_clip
from heic_sample import write_heic
from videokit.core.config import Settings

MakeVideo = Callable[..., Path]
MakePicture = Callable[..., Path]
MakeClip = Callable[..., Path]

_CLIP_ENCODERS = {"h264": "libx264", "hevc": "libx265"}
_PICTURE_ENCODERS = {"png": ("png", "rgb24"), "jpeg": ("mjpeg", "yuvj420p"), "webp": ("libwebp", "yuv420p")}
# An EXIF APP1 segment with a GPS latitude (12 deg 34 min 56 sec N), big-endian TIFF: IFD0 -> GPS IFD.
_EXIF_GPS = (
    b"Exif\0\0MM\0*\0\0\0\x08"  # TIFF header, IFD0 at 8
    + b"\0\x01\x88\x25\0\x04\0\0\0\x01\0\0\0\x1a\0\0\0\0"  # IFD0: GPSInfo -> 26
    + b"\0\x02\0\x01\0\x02\0\0\0\x02N\0\0\0"  # GPS IFD: LatitudeRef "N"
    + b"\0\x02\0\x05\0\0\0\x03\0\0\0\x38\0\0\0\0"  # Latitude -> 56
    + b"\0\0\0\x0c\0\0\0\x01\0\0\0\x22\0\0\0\x01\0\0\0\x38\0\0\0\x01"
)


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


@pytest.fixture
def make_picture(tmp_path: Path) -> MakePicture:
    """Return a factory ``make_picture(format, w=64, h=48, *, exif_gps=False)`` for a solid test picture.

    ``format`` is ``png``, ``jpeg``, ``webp`` or ``heic``; ``exif_gps`` (JPEG only) embeds an EXIF GPS latitude;
    ``grid=(cols, rows)`` (HEIC only) stores it as a tile grid of solid-colour tiles, like a phone photo.
    The HEIC picture is assembled by ``heic_sample`` and needs the local ffmpeg to encode HEVC (skips otherwise).
    """

    def factory(fmt: str, w: int = 64, h: int = 48, *, exif_gps: bool = False, grid: tuple[int, int] = (1, 1)) -> Path:
        out = tmp_path / f"picture-{w}x{h}.{'jpg' if fmt == 'jpeg' else fmt}"
        if fmt == "heic":
            encoders = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
            if " libx265 " not in encoders:
                pytest.skip("the local ffmpeg cannot encode HEVC, so no HEIC fixture")
            return write_heic(out, w, h, grid=grid)
        cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=s={w}x{h}:d=1", "-frames:v", "1"]
        encoder, pix_fmt = _PICTURE_ENCODERS[fmt]
        cmd += ["-c:v", encoder, "-pix_fmt", pix_fmt, str(out)]
        subprocess.run(cmd, check=True, capture_output=True)
        if exif_gps:
            data = out.read_bytes()
            segment = b"\xff\xe1" + (len(_EXIF_GPS) + 2).to_bytes(2, "big") + _EXIF_GPS
            out.write_bytes(data[:2] + segment + data[2:])
        return out

    return factory


@pytest.fixture
def make_clip(tmp_path: Path) -> MakeClip:
    """Return a factory ``make_clip(codec="h264", with_audio=True, *, duration_s=1.0, size="160x120", title=None)``.

    A tiny ``raw.mp4`` like a provider's download; ``codec`` is ``h264`` or ``hevc``. The ffmpeg command is
    ``fakes_videogen.write_clip``, the same one the fake video provider uses.
    """

    def factory(
        codec: str = "h264",
        with_audio: bool = True,
        *,
        duration_s: float = 1.0,
        size: str = "160x120",
        title: str | None = None,
    ) -> Path:
        out = tmp_path / "raw.mp4"
        return write_clip(
            out, duration_s=duration_s, vcodec=_CLIP_ENCODERS[codec], with_audio=with_audio, size=size, title=title
        )

    return factory
