import json
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from videokit.core import media as media_module
from videokit.core.config import Settings
from videokit.core.errors import AppError
from videokit.core.media import AudioTrack, Clip, MediaArtifacts, MediaService, probe

MakeVideo = Callable[..., Path]
MakePicture = Callable[..., Path]
MakeClip = Callable[..., Path]
pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _job_dir(tmp_path: Path, source: Path) -> Path:
    job = tmp_path / "jobs" / "abc"
    job.mkdir(parents=True)
    shutil.move(source, job / "source.bin")
    return job


def _settings(tmp_path: Path, **kw: int) -> Settings:
    return Settings(data_dir=tmp_path, **kw)  # type: ignore[arg-type]


def _stream_info(path: Path) -> dict[str, Any]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
         "stream=codec_name,sample_rate,channels,bit_rate", "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    )  # fmt: skip
    stream: dict[str, Any] = json.loads(out.stdout)["streams"][0]
    return stream


def _last_video_frame_s(path: Path) -> float:
    """Presentation time of the last video frame, relative to the container start (what ``-ss`` seeks to)."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time:format=start_time",
         "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    )  # fmt: skip
    info = json.loads(out.stdout)
    return max(float(p["pts_time"]) for p in info["packets"]) - float(info["format"]["start_time"])


def _make_h264_mp3_mkv(tmp_path: Path, seconds: float = 2.0, *, ts_offset_s: float = 0.0) -> Path:
    """The container smoke test's H.264/MP3 .mkv clip: the container outlasts the video stream slightly.

    ``ts_offset_s`` shifts every timestamp, so the streams and the container start later than 0.
    """
    out = tmp_path / f"clip-{ts_offset_s}.mkv"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=320x240:rate=15",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:v", "libx264", "-c:a", "libmp3lame",
         "-shortest", "-output_ts_offset", str(ts_offset_s), str(out)],
        check=True, capture_output=True,
    )  # fmt: skip
    return out


def _spy_requested_frame_times(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record every frame time ``MediaService.extract`` asks ffmpeg for."""
    requested: list[float] = []
    extract_frames = media_module._extract_frames

    def spy(src: Path, job_dir: Path, times: list[float], width: int) -> list[media_module.Frame]:
        requested.extend(times)
        return extract_frames(src, job_dir, times, width)

    monkeypatch.setattr(media_module, "_extract_frames", spy)
    return requested


def test_probe_duration_and_audio(make_video: MakeVideo) -> None:
    with_audio = probe(make_video("a.mp4", audio=True))
    assert with_audio.has_audio is True
    assert with_audio.duration_s == pytest.approx(6.0, abs=0.3)
    assert probe(make_video("b.mp4")).has_audio is False


def test_probe_rejects_non_video(make_non_video: Callable[[], Path]) -> None:
    with pytest.raises(AppError) as exc:
        probe(make_non_video())
    assert exc.value.code == "not_a_video"


def test_probe_source_probes_the_job_source(tmp_path: Path, make_video: MakeVideo) -> None:
    job_dir = _job_dir(tmp_path, make_video("a.mp4", audio=True))
    info = MediaService(_settings(tmp_path)).probe_source(job_dir)
    assert info.has_audio is True
    assert info.duration_s == pytest.approx(6.0, abs=0.3)
    assert (job_dir / "source.bin").exists()  # probing leaves the source in place


def test_probe_source_rejects_a_text_file(tmp_path: Path, make_non_video: Callable[[], Path]) -> None:
    job_dir = _job_dir(tmp_path, make_non_video())
    with pytest.raises(AppError) as exc:
        MediaService(_settings(tmp_path)).probe_source(job_dir)
    assert exc.value.code == "not_a_video"


def test_probe_rejects_audio_only_file(tmp_path: Path) -> None:
    wav = tmp_path / "tone.wav"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=duration=1", str(wav)], check=True, capture_output=True
    )
    with pytest.raises(AppError) as exc:
        probe(wav)
    assert exc.value.code == "not_a_video"


def test_extract_audio_files(tmp_path: Path, make_video: MakeVideo) -> None:
    job = _job_dir(tmp_path, make_video(audio=True))
    MediaService(_settings(tmp_path)).extract(job)
    wav = _stream_info(job / "audio.wav")
    assert (int(wav["sample_rate"]), wav["channels"]) == (16000, 1)
    mp3 = _stream_info(job / "audio.mp3")
    assert (mp3["codec_name"], mp3["channels"]) == ("mp3", 1)
    assert int(mp3["bit_rate"]) == pytest.approx(32000, rel=0.1)


def test_extract_without_audio_makes_no_audio_files(tmp_path: Path, make_video: MakeVideo) -> None:
    job = _job_dir(tmp_path, make_video())
    media = MediaService(_settings(tmp_path)).extract(job)
    assert media.has_audio is False
    assert not (job / "audio.wav").exists()
    assert not (job / "audio.mp3").exists()


def test_extract_writes_media_json_and_deletes_source(tmp_path: Path, make_video: MakeVideo) -> None:
    job = _job_dir(tmp_path, make_video(audio=True))
    media = MediaService(_settings(tmp_path)).extract(job)
    assert not (job / "source.bin").exists()
    on_disk = json.loads((job / "media.json").read_text())
    assert on_disk["has_audio"] is True
    assert on_disk["duration_s"] == pytest.approx(6.0, abs=0.3)
    assert on_disk["segments"] == []
    assert on_disk["no_speech"] is False
    assert on_disk["language"] is None
    assert MediaArtifacts.model_validate(on_disk) == media
    assert on_disk["frames"][0]["file"] == f"frames/{round(on_disk['frames'][0]['t'] * 1000)}.jpg"


def test_frames_respect_limit_width_order_and_uniqueness(tmp_path: Path, make_video: MakeVideo) -> None:
    job = _job_dir(tmp_path, make_video(colors=("red", "green", "blue", "white", "black"), segment_s=4.0))
    media = MediaService(_settings(tmp_path, max_frames=6, frame_width=96)).extract(job)
    times = [f.t for f in media.frames]
    assert 1 <= len(times) <= 6
    assert times == sorted(times)
    assert len(set(times)) == len(times)
    for frame in media.frames:
        assert (job / frame.file).is_file()
    assert sorted(p.name for p in (job / "frames").iterdir()) == sorted(Path(f.file).name for f in media.frames)
    width = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=width", "-of", "csv=p=0", str(job / media.frames[0].file)],
        check=True, capture_output=True, text=True,
    ).stdout.strip()  # fmt: skip
    assert width == "96"


def test_scene_changes_are_captured(tmp_path: Path, make_video: MakeVideo) -> None:
    # Boundaries at 5 s and 10 s; baseline spacing is 15/4 = 3.75 s, so only scene detection finds them.
    job = _job_dir(tmp_path, make_video(colors=("red", "blue", "green"), segment_s=5.0))
    media = MediaService(_settings(tmp_path, max_frames=4)).extract(job)
    times = [f.t for f in media.frames]
    for boundary in (5.0, 10.0):
        assert any(abs(t - boundary) < 0.3 for t in times), (boundary, times)


def test_candidates_closer_than_two_seconds_are_dropped(tmp_path: Path, make_video: MakeVideo) -> None:
    # Scene cuts every 1 s: far closer than 2 s.
    job = _job_dir(tmp_path, make_video(colors=("red", "blue") * 4, segment_s=1.0))
    media = MediaService(_settings(tmp_path)).extract(job)
    times = [f.t for f in media.frames]
    assert len(times) >= 2
    assert all(b - a >= 2.0 - 1e-6 for a, b in zip(times, times[1:], strict=False))


def test_extract_non_video_raises_and_keeps_nothing_else(tmp_path: Path, make_non_video: Callable[[], Path]) -> None:
    job = _job_dir(tmp_path, make_non_video())
    with pytest.raises(AppError) as exc:
        MediaService(_settings(tmp_path)).extract(job)
    assert exc.value.code == "not_a_video"


def test_probe_reads_the_video_stream_end(tmp_path: Path, make_video: MakeVideo) -> None:
    # mp4 carries a stream duration (a length); Matroska only a DURATION tag (the stream's end timestamp).
    # video_end_s is relative to the container start, which is what ffmpeg -ss seeks in.
    longer_audio = probe(make_video("a.mp4", colors=("red", "blue"), segment_s=2.0, audio=True, audio_s=8.0))
    assert longer_audio.duration_s == pytest.approx(8.0, abs=0.1)
    assert longer_audio.video_end_s == pytest.approx(4.0, abs=0.1)
    mkv = probe(_make_h264_mp3_mkv(tmp_path))
    assert mkv.video_end_s == pytest.approx(mkv.duration_s, abs=0.01)
    assert probe(make_video("b.mp4")).video_end_s == pytest.approx(6.0, abs=0.1)
    # Shifted timestamps: video starts at 1.5 s, its DURATION tag (end) is 3.5 s, the container starts at
    # ~1.475 s (audio priming), so the video ends ~2.025 s after the container start.
    shifted = probe(_make_h264_mp3_mkv(tmp_path, ts_offset_s=1.5))
    assert shifted.video_end_s == pytest.approx(2.025, abs=0.01)


def test_shifted_timestamps_keep_frames_within_the_video(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _make_h264_mp3_mkv(tmp_path, ts_offset_s=1.5)
    last_frame = _last_video_frame_s(source)
    requested = _spy_requested_frame_times(monkeypatch)
    media = MediaService(_settings(tmp_path)).extract(_job_dir(tmp_path, source))
    assert requested and max(requested) <= last_frame, (requested, last_frame)
    assert media.frames


def test_frame_time_at_the_container_end_does_not_fail_the_job(tmp_path: Path) -> None:
    # 2 s H.264/MP3 .mkv: the container lasts 2.025 s, the last video frame starts at ~1.958 s.
    source = _make_h264_mp3_mkv(tmp_path)
    last_frame = _last_video_frame_s(source)
    job = _job_dir(tmp_path, source)
    media = MediaService(_settings(tmp_path)).extract(job)
    assert media.frames
    assert all(f.t <= last_frame for f in media.frames)
    assert all((job / f.file).is_file() for f in media.frames)


def test_audio_longer_than_video_keeps_frames_within_the_video(
    tmp_path: Path, make_video: MakeVideo, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = make_video(colors=("red", "blue"), segment_s=2.0, audio=True, audio_s=8.0)
    last_frame = _last_video_frame_s(source)
    requested = _spy_requested_frame_times(monkeypatch)
    job = _job_dir(tmp_path, source)
    media = MediaService(_settings(tmp_path)).extract(job)
    assert requested and max(requested) <= last_frame  # no seek past the video, not just skipped failures
    assert media.duration_s == pytest.approx(8.0, abs=0.1)
    times = [f.t for f in media.frames]
    assert times and all(t <= last_frame for t in times), (times, last_frame)
    assert any(abs(t - 2.0) < 0.3 for t in times), times  # the scene change is still captured


def test_a_frame_that_cannot_be_extracted_is_skipped(
    tmp_path: Path, make_video: MakeVideo, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(media_module, "select_frame_times", lambda *_: [0.0, 3.0, 30.0])
    job = _job_dir(tmp_path, make_video())
    media = MediaService(_settings(tmp_path)).extract(job)
    assert [f.t for f in media.frames] == [0.0, 3.0]
    assert sorted(p.name for p in (job / "frames").iterdir()) == ["0.jpg", "3000.jpg"]


def test_no_extractable_frame_still_fails_extraction(
    tmp_path: Path, make_video: MakeVideo, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(media_module, "select_frame_times", lambda *_: [30.0, 40.0])
    job = _job_dir(tmp_path, make_video())
    with pytest.raises(AppError) as exc:
        MediaService(_settings(tmp_path)).extract(job)
    assert exc.value.code == "extraction_failed"


def test_no_frame_written_fails_extraction_even_when_ffmpeg_exits_cleanly(
    tmp_path: Path, make_video: MakeVideo, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Older ffmpeg releases exit 0 without writing a file when the seek lands past the last frame;
    # newer ones exit non-zero. Either way, a job with no frame at all must fail the same way.
    real_run = media_module._run

    def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        if "-frames:v" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return real_run(cmd)

    monkeypatch.setattr(media_module, "_run", run)
    job = _job_dir(tmp_path, make_video())
    with pytest.raises(AppError) as exc:
        MediaService(_settings(tmp_path)).extract(job)
    assert exc.value.code == "extraction_failed"


def _mp3_info(path: Path) -> dict[str, Any]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,sample_rate,channels,bit_rate:format_tags",
         "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    )  # fmt: skip
    info: dict[str, Any] = json.loads(out.stdout)
    return info


def _extract_mp3(tmp_path: Path, source: Path) -> tuple[AudioTrack, dict[str, Any], Path]:
    job = _job_dir(tmp_path, source)
    track = MediaService(_settings(tmp_path)).extract_mp3(job)
    return track, _mp3_info(job / track.file), job


def test_extract_mp3_encodes_192k_mp3_and_deletes_source(tmp_path: Path, make_video: MakeVideo) -> None:
    track, info, job = _extract_mp3(tmp_path, make_video(audio=True))
    stream = info["streams"][0]
    assert track.file == "audio.mp3"
    assert stream["codec_name"] == "mp3"
    assert abs(int(stream["bit_rate"]) - 192_000) < 10_000
    assert stream["channels"] == 1  # mono stays mono
    assert abs(track.duration_s - 6.0) < 1.0
    assert track.size_bytes == (job / "audio.mp3").stat().st_size > 0
    assert not (job / "source.bin").exists()


def test_extract_mp3_prefers_the_default_flagged_track(tmp_path: Path, make_video: MakeVideo) -> None:
    _, info, _ = _extract_mp3(tmp_path, make_video(two_audio_tracks=True))
    assert info["streams"][0]["channels"] == 2


def test_extract_mp3_downmixes_surround_to_stereo(tmp_path: Path, make_video: MakeVideo) -> None:
    _, info, _ = _extract_mp3(tmp_path, make_video(audio_channels=6))
    assert info["streams"][0]["channels"] == 2


def test_extract_mp3_resamples_above_48khz(tmp_path: Path, make_video: MakeVideo) -> None:
    _, info, _ = _extract_mp3(tmp_path, make_video(audio_rate=96000))
    assert info["streams"][0]["sample_rate"] == "48000"


@pytest.mark.parametrize("rate", [16000, 22050, 24000])
def test_extract_mp3_upsamples_low_rates_to_keep_192k(tmp_path: Path, make_video: MakeVideo, rate: int) -> None:
    _, info, _ = _extract_mp3(tmp_path, make_video(audio_rate=rate))
    stream = info["streams"][0]
    assert stream["sample_rate"] == "44100"
    assert abs(int(stream["bit_rate"]) - 192_000) < 10_000


def test_extract_mp3_keeps_32khz(tmp_path: Path, make_video: MakeVideo) -> None:
    _, info, _ = _extract_mp3(tmp_path, make_video(audio_rate=32000))
    assert info["streams"][0]["sample_rate"] == "32000"


def test_extract_mp3_drops_metadata(tmp_path: Path, make_video: MakeVideo) -> None:
    source = make_video(audio=True, location=True)
    assert "location" in json.dumps(_mp3_info(source)["format"].get("tags", {}))  # fixture really has the tag
    _, info, _ = _extract_mp3(tmp_path, source)
    assert "location" not in json.dumps(info["format"].get("tags", {})).lower()


def test_extract_mp3_without_audio_is_no_audio(tmp_path: Path, make_video: MakeVideo) -> None:
    job = _job_dir(tmp_path, make_video())
    with pytest.raises(AppError) as exc:
        MediaService(_settings(tmp_path)).extract_mp3(job)
    assert (exc.value.code, exc.value.status) == ("no_audio", 422)


def test_extract_mp3_of_a_text_file_is_not_a_video(tmp_path: Path, make_non_video: Callable[[], Path]) -> None:
    job = _job_dir(tmp_path, make_non_video())
    with pytest.raises(AppError) as exc:
        MediaService(_settings(tmp_path)).extract_mp3(job)
    assert exc.value.code == "not_a_video"


def test_extract_mp3_ffmpeg_failure_is_extraction_failed(
    tmp_path: Path, make_video: MakeVideo, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = _job_dir(tmp_path, make_video(audio=True))
    real_run = media_module._run

    def failing(cmd: list[str]) -> Any:
        if cmd[0] == "ffmpeg":
            raise subprocess.CalledProcessError(1, cmd)
        return real_run(cmd)

    monkeypatch.setattr(media_module, "_run", failing)
    with pytest.raises(AppError) as exc:
        MediaService(_settings(tmp_path)).extract_mp3(job)
    assert (exc.value.code, exc.value.status) == ("extraction_failed", 500)


def _ffprobe(path: Path, entries: str) -> dict[str, Any]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", entries, "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    )  # fmt: skip
    info: dict[str, Any] = json.loads(out.stdout)
    return info


def _normalize(tmp_path: Path, src: Path) -> Path:
    out = tmp_path / "pictures" / "1.jpg"
    out.parent.mkdir()
    MediaService(_settings(tmp_path)).normalize_picture(src, out)
    return out


def _size(path: Path) -> tuple[int, int]:
    stream = _ffprobe(path, "stream=width,height")["streams"][0]
    return stream["width"], stream["height"]


@pytest.mark.parametrize("fmt", ["png", "jpeg", "webp", "heic"])
def test_normalize_picture_writes_a_jpeg(tmp_path: Path, make_picture: MakePicture, fmt: str) -> None:
    out = _normalize(tmp_path, make_picture(fmt, 64, 48))
    assert _ffprobe(out, "stream=codec_name")["streams"][0]["codec_name"] == "mjpeg"
    assert out.read_bytes()[:2] == b"\xff\xd8"
    assert _size(out) == (64, 48)


@pytest.mark.parametrize(("w", "h", "expected"), [(4000, 3000, (2048, 1536)), (3000, 4000, (1536, 2048))])
def test_normalize_picture_limits_the_long_side_to_2048(
    tmp_path: Path, make_picture: MakePicture, w: int, h: int, expected: tuple[int, int]
) -> None:
    assert _size(_normalize(tmp_path, make_picture("png", w, h))) == expected


def test_normalize_picture_never_upscales(tmp_path: Path, make_picture: MakePicture) -> None:
    assert _size(_normalize(tmp_path, make_picture("png", 2048, 100))) == (2048, 100)


def _tile_centers(path: Path, cols: int, rows: int) -> list[tuple[int, ...]]:
    """The RGB at the centre of each cell of a ``cols`` x ``rows`` split of the picture, row by row."""
    with Image.open(path) as image:
        rgb = image.convert("RGB")
        cw, ch = rgb.width // cols, rgb.height // rows
        return [rgb.getpixel((cw * (i % cols) + cw // 2, ch * (i // cols) + ch // 2)) for i in range(cols * rows)]  # type: ignore[misc]


@pytest.mark.parametrize(("grid", "size"), [((2, 1), (128, 48)), ((2, 2), (128, 96)), ((3, 1), (192, 48))])
def test_normalize_picture_composes_a_tiled_heic_into_the_whole_picture(
    tmp_path: Path, make_picture: MakePicture, grid: tuple[int, int], size: tuple[int, int]
) -> None:
    out = _normalize(tmp_path, make_picture("heic", *size, grid=grid))
    assert _size(out) == size
    red, blue, green, yellow = ((255, 0, 0), (0, 0, 255), (0, 128, 0), (255, 255, 0))
    expected = [red, blue, green, yellow][: grid[0] * grid[1]]
    for got, want in zip(_tile_centers(out, *grid), expected, strict=True):
        assert all(abs(g - w) < 60 for g, w in zip(got, want, strict=True)), (got, want)


def test_normalize_picture_limits_a_large_tiled_heic_to_2048(tmp_path: Path, make_picture: MakePicture) -> None:
    assert _size(_normalize(tmp_path, make_picture("heic", 4096, 1536, grid=(8, 3)))) == (2048, 768)


def test_normalize_picture_of_a_tiled_heic_has_no_metadata(tmp_path: Path, make_picture: MakePicture) -> None:
    out = _normalize(tmp_path, make_picture("heic", 128, 48, grid=(2, 1)))
    assert b"Exif" not in out.read_bytes()
    assert _ffprobe(out, "format_tags").get("format", {}).get("tags") is None


def test_normalize_picture_rejects_a_truncated_heic(tmp_path: Path, make_picture: MakePicture) -> None:
    src = make_picture("heic", 128, 48, grid=(2, 1))
    src.write_bytes(src.read_bytes()[:150])
    out = tmp_path / "1.jpg"
    with pytest.raises(AppError) as exc:
        MediaService(_settings(tmp_path)).normalize_picture(src, out)
    assert (exc.value.code, exc.value.status) == ("invalid_picture", 422)
    assert not out.exists()


def test_normalize_picture_strips_exif_gps(tmp_path: Path, make_picture: MakePicture) -> None:
    src = make_picture("jpeg", 64, 48, exif_gps=True)
    assert b"Exif\0\0" in src.read_bytes()
    out = _normalize(tmp_path, src)
    assert b"Exif" not in out.read_bytes()
    assert _ffprobe(out, "format_tags").get("format", {}).get("tags") is None


def test_normalize_picture_rejects_a_text_file(tmp_path: Path, make_non_video: Callable[[], Path]) -> None:
    out = tmp_path / "1.jpg"
    with pytest.raises(AppError) as exc:
        MediaService(_settings(tmp_path)).normalize_picture(make_non_video(), out)
    assert (exc.value.code, exc.value.status) == ("invalid_picture", 422)
    assert not out.exists()


def _finalize(tmp_path: Path, raw: Path) -> tuple[Clip, Path]:
    job = tmp_path / "jobs" / "vid"
    job.mkdir(parents=True)
    shutil.move(raw, job / "raw.mp4")
    return MediaService(_settings(tmp_path)).finalize_clip(job / "raw.mp4", job), job


def _top_level_boxes(path: Path) -> list[str]:
    data = path.read_bytes()
    boxes, at = [], 0
    while at + 8 <= len(data):
        size = int.from_bytes(data[at : at + 4], "big")
        boxes.append(data[at + 4 : at + 8].decode("latin-1"))
        at += size or len(data)
    return boxes


def test_finalize_clip_removes_audio_and_metadata_and_keeps_h264(tmp_path: Path, make_clip: MakeClip) -> None:
    clip, job = _finalize(tmp_path, make_clip("h264", with_audio=True, title="secret-title"))
    out = job / "clip.mp4"
    streams = _ffprobe(out, "stream=codec_type,codec_name")["streams"]
    assert [(s["codec_type"], s["codec_name"]) for s in streams] == [("video", "h264")]
    assert b"secret-title" not in out.read_bytes()
    assert _ffprobe(out, "format_tags")["format"].get("tags", {}).get("title") is None
    assert not (job / "raw.mp4").exists()
    assert clip.file == "clip.mp4"


def test_finalize_clip_is_faststart(tmp_path: Path, make_clip: MakeClip) -> None:
    _, job = _finalize(tmp_path, make_clip())
    boxes = _top_level_boxes(job / "clip.mp4")
    assert boxes.index("moov") < boxes.index("mdat")


def test_finalize_clip_reports_the_probed_values(tmp_path: Path, make_clip: MakeClip) -> None:
    clip, job = _finalize(tmp_path, make_clip(duration_s=2.0, size="320x240"))
    assert (clip.width, clip.height) == (320, 240)
    assert clip.duration_s == pytest.approx(2.0, abs=0.2)
    assert clip.size_bytes == (job / "clip.mp4").stat().st_size


def test_finalize_clip_writes_a_thumbnail_within_480px(tmp_path: Path, make_clip: MakeClip) -> None:
    clip, job = _finalize(tmp_path, make_clip(size="1280x720"))
    assert clip.thumbnail == "thumbnail.jpg"
    assert _size(job / "thumbnail.jpg") == (480, 270)
    assert _ffprobe(job / "thumbnail.jpg", "stream=codec_name")["streams"][0]["codec_name"] == "mjpeg"


def test_finalize_clip_thumbnail_does_not_upscale(tmp_path: Path, make_clip: MakeClip) -> None:
    _, job = _finalize(tmp_path, make_clip(size="160x120"))
    assert _size(job / "thumbnail.jpg") == (160, 120)


def test_finalize_clip_shorter_than_half_a_second_still_gets_a_thumbnail(tmp_path: Path, make_clip: MakeClip) -> None:
    _, job = _finalize(tmp_path, make_clip(duration_s=0.3))
    assert (job / "thumbnail.jpg").stat().st_size > 0


def test_finalize_clip_reencodes_hevc_to_h264(tmp_path: Path, make_clip: MakeClip) -> None:
    _, job = _finalize(tmp_path, make_clip("hevc", with_audio=True))
    streams = _ffprobe(job / "clip.mp4", "stream=codec_type,codec_name")["streams"]
    assert [(s["codec_type"], s["codec_name"]) for s in streams] == [("video", "h264")]


@pytest.mark.parametrize("pix_fmt", ["yuv444p", "yuv420p10le"])
def test_finalize_clip_reencodes_to_8_bit_420_so_phone_galleries_play_it(tmp_path: Path, pix_fmt: str) -> None:
    raw = tmp_path / "wide-gamut.mkv"  # lossless FFV1 keeps the source pixel format; libx264 would keep it too
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=duration=1:size=160x120:rate=15",
         "-pix_fmt", pix_fmt, "-c:v", "ffv1", str(raw)],
        check=True, capture_output=True,
    )  # fmt: skip
    assert _ffprobe(raw, "stream=pix_fmt")["streams"][0]["pix_fmt"] == pix_fmt
    _, job = _finalize(tmp_path, raw)
    stream = _ffprobe(job / "clip.mp4", "stream=codec_name,pix_fmt")["streams"][0]
    assert (stream["codec_name"], stream["pix_fmt"]) == ("h264", "yuv420p")


def test_finalize_clip_without_audio_input(tmp_path: Path, make_clip: MakeClip) -> None:
    _, job = _finalize(tmp_path, make_clip(with_audio=False))
    assert [s["codec_type"] for s in _ffprobe(job / "clip.mp4", "stream=codec_type")["streams"]] == ["video"]


def test_finalize_clip_of_a_text_file_is_video_failed(tmp_path: Path, make_non_video: Callable[[], Path]) -> None:
    with pytest.raises(AppError) as exc:
        _finalize(tmp_path, make_non_video())
    assert (exc.value.code, exc.value.status) == ("video_failed", 502)


def test_finalize_clip_ffmpeg_failure_is_video_failed(
    tmp_path: Path, make_clip: MakeClip, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_run = media_module._run

    def failing(cmd: list[str]) -> Any:
        if cmd[0] == "ffmpeg":
            raise subprocess.CalledProcessError(1, cmd)
        return real_run(cmd)

    monkeypatch.setattr(media_module, "_run", failing)
    with pytest.raises(AppError) as exc:
        _finalize(tmp_path, make_clip())
    assert (exc.value.code, exc.value.status) == ("video_failed", 502)
