import json
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from videokit.core.config import Settings
from videokit.core.errors import AppError
from videokit.core.media import MediaArtifacts, MediaService, probe

MakeVideo = Callable[..., Path]
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


def test_probe_duration_and_audio(make_video: MakeVideo) -> None:
    with_audio = probe(make_video("a.mp4", audio=True))
    assert with_audio.has_audio is True
    assert with_audio.duration_s == pytest.approx(6.0, abs=0.3)
    assert probe(make_video("b.mp4")).has_audio is False


def test_probe_rejects_non_video(make_non_video: Callable[[], Path]) -> None:
    with pytest.raises(AppError) as exc:
        probe(make_non_video())
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
