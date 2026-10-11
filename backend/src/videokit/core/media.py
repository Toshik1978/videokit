"""ffprobe/ffmpeg media probing and scene-aware extraction (research R6, data-model MediaArtifacts).

All functions are synchronous and work on a job directory, so the worker can call them through
``asyncio.to_thread``. ffmpeg is run with argument lists only; media content is never logged.
"""

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from videokit.core.config import Settings
from videokit.core.errors import AppError

SOURCE_NAME = "source.bin"
MEDIA_JSON = "media.json"
SCENE_THRESHOLD = 0.3
SCENE_PASS_WIDTH = 320
MIN_FRAME_GAP_S = 2.0
_PTS_TIME = re.compile(r"pts_time:(\d+(?:\.\d+)?)")


class Segment(BaseModel):
    start: float
    end: float
    text: str


class Frame(BaseModel):
    t: float
    file: str


class MediaArtifacts(BaseModel):
    """Content of ``media.json``; transcription fields are filled in later by the pipeline."""

    duration_s: float
    has_audio: bool
    language: str | None = None
    language_probability: float | None = None
    no_speech: bool = False
    segments: list[Segment] = []
    frames: list[Frame] = []


@dataclass(frozen=True)
class ProbeResult:
    duration_s: float
    has_audio: bool


def _not_a_video() -> AppError:
    return AppError("not_a_video", "The uploaded file is not a playable video.", 400)


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=True, capture_output=True, text=True, errors="replace")


def probe(path: Path) -> ProbeResult:
    """Validate that ``path`` is a playable video and read its duration and audio presence."""
    try:
        out = _run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)])
        info = json.loads(out.stdout)
        streams = info.get("streams", [])
        has_video = any(
            s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic") for s in streams
        )
        duration = float(info["format"]["duration"])
    except subprocess.CalledProcessError, json.JSONDecodeError, KeyError, ValueError, TypeError:
        raise _not_a_video() from None
    if not has_video or duration <= 0:
        raise _not_a_video()
    return ProbeResult(duration_s=duration, has_audio=any(s.get("codec_type") == "audio" for s in streams))


def _scene_times(source: Path) -> list[float]:
    vf = f"scale={SCENE_PASS_WIDTH}:-2,select='gt(scene,{SCENE_THRESHOLD})',showinfo"
    cmd = ["ffmpeg", "-v", "info", "-i", str(source), "-an", "-vf", vf, "-f", "null", "-"]
    return [float(m) for m in _PTS_TIME.findall(_run(cmd).stderr)]


def select_frame_times(scenes: list[float], duration_s: float, max_frames: int) -> list[float]:
    """Pick frame timestamps: scene candidates first, uniform baseline fills gaps, >= 2 s apart."""
    step = duration_s / max_frames
    baseline = [i * step for i in range(max_frames)]
    kept: list[float] = []
    for t in [*sorted(scenes), *baseline]:
        t = round(min(max(t, 0.0), duration_s), 3)
        if all(abs(t - k) >= MIN_FRAME_GAP_S for k in kept):
            kept.append(t)
    kept.sort()
    if len(kept) > max_frames:
        last = len(kept) - 1
        kept = [kept[round(i * last / (max_frames - 1))] if max_frames > 1 else kept[0] for i in range(max_frames)]
    return sorted(set(kept))


def _extract_audio(source: Path, job_dir: Path) -> None:
    base = ["ffmpeg", "-v", "error", "-y", "-i", str(source), "-vn", "-ac", "1"]
    _run([*base, "-ar", "16000", str(job_dir / "audio.wav")])
    _run([*base, "-b:a", "32k", str(job_dir / "audio.mp3")])


def _extract_frames(source: Path, job_dir: Path, times: list[float], width: int) -> list[Frame]:
    frames_dir = job_dir / "frames"
    frames_dir.mkdir(exist_ok=True)
    frames: list[Frame] = []
    for t in times:
        rel = f"frames/{round(t * 1000)}.jpg"
        cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(source)]
        _run([*cmd, "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "3", str(job_dir / rel)])
        if (job_dir / rel).is_file():
            frames.append(Frame(t=t, file=rel))
    return frames


class MediaService:
    """`CoreContext.media`: turns an uploaded source video into audio files, frames and media.json."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def extract(self, job_dir: Path) -> MediaArtifacts:
        source = job_dir / SOURCE_NAME
        info = probe(source)
        try:
            if info.has_audio:
                _extract_audio(source, job_dir)
            times = select_frame_times(_scene_times(source), info.duration_s, self._settings.max_frames)
            frames = _extract_frames(source, job_dir, times, self._settings.frame_width)
        except subprocess.CalledProcessError:
            raise AppError("extraction_failed", "The video could not be processed.", 500) from None
        media = MediaArtifacts(duration_s=info.duration_s, has_audio=info.has_audio, frames=frames)
        (job_dir / MEDIA_JSON).write_text(media.model_dump_json(indent=2), encoding="utf-8")
        source.unlink()
        return media
