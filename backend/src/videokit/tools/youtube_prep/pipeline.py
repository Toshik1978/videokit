"""YouTube Prep pipeline: extracting -> transcribing -> generating (FR-020-FR-029, FR-032, FR-033).

`run` is synchronous; the tool runs it in a worker thread, so every provider call, file access and job
update stays off the event loop. The request prefix (system prompt, transcript text, frames) is built
only from `media.json` and the frame files, so generation, repair and later per-section regeneration
send a byte-identical prefix that providers can serve from their cache (research R9, SC-004). Anything
that varies per call, including the video duration, goes into the turns.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from videokit.core.config import Settings
from videokit.core.errors import AppError
from videokit.core.llm import (
    AllProvidersFailed,
    GenerationRequest,
    GenerationResponse,
    LabeledFrame,
    Turn,
    Usage,
    format_timestamp,
)
from videokit.core.media import MEDIA_JSON, MediaArtifacts
from videokit.core.media import Segment as MediaSegment
from videokit.core.pricing import estimate
from videokit.core.registry import CoreContext, JobHandle
from videokit.core.transcribe import AudioFiles, Transcript
from videokit.core.transcribe import Segment as TranscriptSegment
from videokit.tools.youtube_prep import prompt, rules
from videokit.tools.youtube_prep.schema import OUTPUT_SCHEMA, ModelOutput, Rendered, YouTubePrepResult

RESULT_JSON = "result.json"
DEFAULT_LANGUAGE = "en"  # no speech: the output language defaults to English
NOTICE_NO_SPEECH = "no_speech"
LOCAL_TRANSCRIPTION = "local"  # runs on this machine: free (cost ruling on T020)
OPENAI_TRANSCRIPTION = "openai"
ENGLISH_INSTRUCTION = "No speech was detected; write all output in English (language code en)."
MAX_LISTED_PROBLEMS = 3  # in a failure reason; the repair turn lists them all


# ---------------------------------------------------------------- model output checks


def structure_problems(error: ValidationError) -> list[str]:
    """Readable schema problems: locations and messages only, never the submitted values."""
    problems = []
    for err in error.errors():
        loc = ".".join(str(part) for part in err["loc"])
        problems.append(f"{loc}: {err['msg']}" if loc else str(err["msg"]))
    return problems


def check_output(data: dict[str, Any], *, duration_s: float) -> tuple[dict[str, Any] | None, list[str]]:
    """Schema-check, normalize and validate a model output: `(normalized, [])` or `(None | normalized, problems)`."""
    try:
        parsed = ModelOutput.model_validate(data).model_dump()
    except ValidationError as exc:
        return None, structure_problems(exc)
    normalized = rules.normalize(parsed, duration_s=duration_s)
    return normalized, [str(v) for v in rules.validate(normalized, duration_s=duration_s)]


def check_section(section: str, data: dict[str, Any], *, duration_s: float) -> tuple[Any, list[str]]:
    """Schema-check a full model output, then normalize and validate only `section`: `(value, problems)`.

    Regeneration asks for the other sections empty; whatever they contain is ignored.
    """
    try:
        parsed = ModelOutput.model_validate(data).model_dump()
    except ValidationError as exc:
        return None, structure_problems(exc)
    value = rules.normalize_section(section, parsed[section], duration_s=duration_s)
    return value, [str(v) for v in rules.validate_section(section, value, duration_s=duration_s)]


# ---------------------------------------------------------------- request building


@dataclass(frozen=True)
class RequestPrefix:
    """The stable, cacheable part of every generation request for one job."""

    system: str
    context_text: str
    frames: list[LabeledFrame]

    def request(self, schema: dict[str, Any], turns: list[Turn]) -> GenerationRequest:
        return GenerationRequest(
            system=self.system, context_text=self.context_text, frames=self.frames, schema=schema, turns=turns
        )


def load_media(job_dir: Path) -> MediaArtifacts:
    return MediaArtifacts.model_validate_json((job_dir / MEDIA_JSON).read_text(encoding="utf-8"))


def build_prefix(media: MediaArtifacts, job_dir: Path) -> RequestPrefix:
    """Prefix from the job's `media.json` content and frame files (reused byte-for-byte by regeneration)."""
    frames = [LabeledFrame(t_seconds=f.t, jpeg=(job_dir / f.file).read_bytes()) for f in media.frames]
    return RequestPrefix(
        system=prompt.SYSTEM_PROMPT, context_text=prompt.build_context_text(media.segments), frames=frames
    )


def duration_note(duration_s: float) -> str:
    """Duration facts for a turn (kept out of the cached prefix)."""
    note = (
        f"The video is {format_timestamp(duration_s)} long ({duration_s:.1f} seconds); "
        f"every chapter must start before {duration_s:.1f} seconds."
    )
    if duration_s < rules.CHAPTERS_MIN_DURATION_S:
        note = (
            f"The video is {format_timestamp(duration_s)} long ({duration_s:.1f} seconds), shorter than "
            f"{rules.CHAPTERS_MIN_DURATION_S} seconds: return an empty chapters list."
        )
    return note


def turn_notes(media: MediaArtifacts) -> str:
    """Per-job facts appended to every generate/repair/regenerate turn (never part of the cached prefix)."""
    notes = duration_note(media.duration_s)
    return f"{notes}\n{ENGLISH_INSTRUCTION}" if media.no_speech else notes


# ---------------------------------------------------------------- stages


def sanitize_segments(segments: Sequence[TranscriptSegment], duration_s: float) -> list[MediaSegment]:
    """Sort by start, clamp into `[0, duration_s]` and drop segments left empty (media.json rules)."""
    out: list[MediaSegment] = []
    for seg in sorted(segments, key=lambda s: (s.start, s.end)):
        start, end = max(seg.start, 0.0), min(seg.end, duration_s)
        if start < end:
            out.append(MediaSegment(start=start, end=end, text=seg.text))
    return out


def _transcription_cost(transcript: Transcript, settings: Settings) -> float | None:
    if transcript.provider == LOCAL_TRANSCRIPTION:
        return 0.0
    model = settings.openai_transcribe_model if transcript.provider == OPENAI_TRANSCRIPTION else transcript.provider
    return estimate(Usage(audio_minutes=transcript.audio_minutes), model, settings.pricing_overrides)


def _transcribe(job: JobHandle, ctx: CoreContext, media: MediaArtifacts) -> tuple[MediaArtifacts, dict[str, str]]:
    """Fill the transcript fields of `media` (or mark no speech when there is no audio)."""
    if not media.has_audio:
        return media.model_copy(update={"language": DEFAULT_LANGUAGE, "no_speech": True}), {}
    transcript = ctx.transcriber.transcribe(AudioFiles(wav=job.dir / "audio.wav", mp3=job.dir / "audio.mp3"))
    job.add_usage(Usage(audio_minutes=transcript.audio_minutes), _transcription_cost(transcript, ctx.settings))
    providers = {"transcription": transcript.provider}
    job.record(providers=providers)
    segments = sanitize_segments(transcript.segments, media.duration_s)
    no_speech = transcript.no_speech or not segments
    update = {
        "language": DEFAULT_LANGUAGE if no_speech else transcript.language or DEFAULT_LANGUAGE,
        "language_probability": transcript.language_probability,
        "no_speech": no_speech,
        "segments": [] if no_speech else segments,
    }
    return media.model_copy(update=update), providers


def _call(job: JobHandle, ctx: CoreContext, req: GenerationRequest, providers: dict[str, str]) -> GenerationResponse:
    response = ctx.llm.generate(req)
    job.add_usage(response.usage, estimate(response.usage, response.model, ctx.settings.pricing_overrides))
    job.record(providers={**providers, "llm": response.provider})
    return response


def _generate(job: JobHandle, ctx: CoreContext, media: MediaArtifacts, providers: dict[str, str]) -> dict[str, Any]:
    """Generate, normalize and validate, with at most one repair call (FR-029)."""
    prefix = build_prefix(media, job.dir)
    duration = media.duration_s
    turns = [Turn("user", f"{prompt.generate_turn()}\n{turn_notes(media)}")]
    response = _call(job, ctx, prefix.request(OUTPUT_SCHEMA, turns), providers)
    normalized, problems = check_output(response.data, duration_s=duration)
    if not problems and normalized is not None:
        return normalized
    turns += [
        Turn("assistant", json.dumps(response.data, ensure_ascii=False)),
        Turn("user", f"{prompt.repair_turn(problems)}\n{turn_notes(media)}"),
    ]
    response = _call(job, ctx, prefix.request(OUTPUT_SCHEMA, turns), providers)
    normalized, problems = check_output(response.data, duration_s=duration)
    if not problems and normalized is not None:
        return normalized
    listed = "; ".join(problems[:MAX_LISTED_PROBLEMS]) + ("; ..." if len(problems) > MAX_LISTED_PROBLEMS else "")
    raise AppError(
        "generation_invalid", f"the output still broke the YouTube rules after one repair attempt ({listed})", 502
    )


def build_result(normalized: dict[str, Any], media: MediaArtifacts) -> YouTubePrepResult:
    notices = [NOTICE_NO_SPEECH] if media.no_speech else []
    notices += rules.notices(duration_s=media.duration_s)
    if media.no_speech:
        language = DEFAULT_LANGUAGE  # no speech: the output is English whatever the model claims (spec edge case)
    else:
        language = normalized["language"].strip() or media.language or DEFAULT_LANGUAGE
    sections = {section: normalized[section] for section in rules.SECTIONS}
    return YouTubePrepResult(
        language=language, notices=notices, rendered=Rendered(**rules.render(normalized)), **sections
    )


def write_result(job_dir: Path, result: YouTubePrepResult) -> None:
    """Write `result.json` atomically (temp file + replace), so readers never see a partial file."""
    target = job_dir / RESULT_JSON
    tmp = target.with_name(f"{RESULT_JSON}.tmp")
    tmp.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    os.replace(tmp, target)


def regenerate(
    job: JobHandle, ctx: CoreContext, section: str, hint: str | None, providers: dict[str, str]
) -> YouTubePrepResult:
    """Replace one section of a done job's result (FR-031), with at most one repair call (blocking).

    Reuses the stored `media.json`, the frames and the full `OUTPUT_SCHEMA`, so the request differs from the
    original generation's only in its turns and providers can serve the prefix from cache (SC-004); a different
    output schema would invalidate it. Usage is added to the job for every call; on failure `result.json` is left
    untouched. Raises `FileNotFoundError` / `JobNotFound` if the job's files / row are gone (purged).
    """
    media = load_media(job.dir)
    current = YouTubePrepResult.model_validate_json((job.dir / RESULT_JSON).read_text(encoding="utf-8"))
    prefix = build_prefix(media, job.dir)
    turns = [Turn("user", f"{prompt.regenerate_turn(section, hint, current.model_dump())}\n{turn_notes(media)}")]
    try:
        response = _call(job, ctx, prefix.request(OUTPUT_SCHEMA, turns), providers)
        value, problems = check_section(section, response.data, duration_s=media.duration_s)
        if problems:
            turns += [
                Turn("assistant", json.dumps(response.data, ensure_ascii=False)),
                Turn("user", f"{prompt.repair_turn(problems)}\n{turn_notes(media)}"),
            ]
            response = _call(job, ctx, prefix.request(OUTPUT_SCHEMA, turns), providers)
            value, problems = check_section(section, response.data, duration_s=media.duration_s)
    except AllProvidersFailed:
        raise AppError(
            "generation_failed", "All generation providers failed; the previous version was kept.", 502
        ) from None
    if problems:
        raise AppError(
            "generation_failed",
            f"The new {section} still broke the YouTube rules after one repair attempt; the previous version was kept.",
            502,
        )
    data = current.model_dump()
    data[section] = value
    data["rendered"][section] = rules.render({section: value})[section]
    data["regenerations"][section] = current.regenerations.get(section, 0) + 1
    updated = YouTubePrepResult.model_validate(data)
    write_result(job.dir, updated)
    return updated


def run(job: JobHandle, ctx: CoreContext) -> None:
    """The whole pipeline (blocking). The worker sets `done` once it returns and maps raised errors."""
    job.check_cancelled()
    job.set_state("extracting")
    media = ctx.media.extract(job.dir)

    job.check_cancelled()
    job.set_state("transcribing", duration_s=media.duration_s)
    media, providers = _transcribe(job, ctx, media)
    (job.dir / MEDIA_JSON).write_text(media.model_dump_json(indent=2), encoding="utf-8")

    job.check_cancelled()
    job.set_state("generating", language=media.language, no_speech=media.no_speech)
    write_result(job.dir, build_result(_generate(job, ctx, media, providers), media))
