"""The Video Generation run: submit once, poll until the provider is done, then finalize the clip.

Research V3 (never pay twice), V5 (polling, 20-minute cap), V6 (silent clip, forget the stored request) and V8
(cost). The provider does the work; this module only waits on it with `asyncio.sleep`, so a slot holds no thread
while a clip renders. Nothing here logs the prompt, the pictures or a provider message: exception types only.
"""

import asyncio
import json
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from videokit.core.errors import AppError
from videokit.core.pricing import estimate
from videokit.core.registry import CoreContext, JobCancelled, JobHandle
from videokit.core.videogen import (
    Accepted,
    Capabilities,
    Picture,
    PollResult,
    Usage,
    VideoChain,
    VideoError,
    VideoRequest,
)
from videokit.tools.video_generation.inputs import CreationInputs, StoredInputs, load_inputs
from videokit.tools.video_generation.naming import download_name
from videokit.tools.video_generation.prompt import build_provider_prompt

logger = logging.getLogger("videokit")

SUBMIT_JSON = "submit.json"
OPERATION_JSON = "operation.json"
RESULT_JSON = "result.json"
POLL_INTERVAL_S = 10.0
WAIT_CAP_S = 20 * 60.0

# Catalog-safe texts; the frontend maps the codes to its own catalog. Provider messages are never shown.
_MESSAGES = {
    "provider_out_of_credit": "The video provider has run out of credit or quota.",
    "content_blocked": "The video provider declined this prompt or these pictures.",
    "invalid_picture": "The video provider could not use one of the pictures.",
    "provider_unavailable": "The video provider is not available right now.",
    "video_failed": "The video could not be generated.",
    "provider_timeout": "The video provider took too long to finish.",
    "resume_impossible": "The server restarted before the provider confirmed this clip, so it was not sent again.",
}


def _failure(code: str) -> AppError:
    return AppError(code, _MESSAGES.get(code, _MESSAGES["video_failed"]), 502)


def _write_json(path: Path, document: dict[str, Any]) -> None:
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)  # readers never see a partial file


def _now() -> str:
    return datetime.now(UTC).isoformat()


def estimated_usage(fields: CreationInputs, picture_count: int, caps: Capabilities) -> Usage:
    """The documented-rate usage of a clip, for when the provider reports none (research V8)."""
    rate = caps.video_tokens_per_second.get(fields.resolution, 0)
    return Usage(input_tokens=picture_count * caps.tokens_per_image, video_output_tokens=fields.duration_s * rate)


def _request(stored: StoredInputs) -> VideoRequest:
    pictures = [Picture(position=n, jpeg=path.read_bytes()) for n, path in enumerate(stored.pictures, start=1)]
    fields = stored.fields
    return VideoRequest(
        prompt=build_provider_prompt(fields.prompt, len(pictures)),
        pictures=pictures,
        duration_s=fields.duration_s,
        aspect_ratio=fields.aspect_ratio,
        resolution=fields.resolution,
    )


async def _submit(job: JobHandle, video: VideoChain, stored: StoredInputs) -> Accepted:
    """Record that a request may be in flight, hand it to the provider, record what it accepted (research V3)."""
    request = await asyncio.to_thread(_request, stored)
    await asyncio.to_thread(_write_json, job.dir / SUBMIT_JSON, {"started_at": _now()})
    try:
        accepted = await asyncio.to_thread(video.submit, request)
    except VideoError as exc:
        raise _failure(exc.code) from None
    await asyncio.to_thread(
        _write_json,
        job.dir / OPERATION_JSON,
        {"provider": accepted.provider, "ref": accepted.ref, "submitted_at": _now()},
    )
    job.record(providers={"video": accepted.provider})
    return accepted


def _read_operation(path: Path) -> tuple[Accepted, float]:
    """The operation a previous run stored, and how long ago it was accepted (the wait the cap has already used)."""
    document = json.loads(path.read_text(encoding="utf-8"))
    accepted = Accepted(provider=document["provider"], ref=document["ref"])
    try:
        waited = (datetime.now(UTC) - datetime.fromisoformat(document["submitted_at"])).total_seconds()
    except KeyError, TypeError, ValueError:
        waited = 0.0  # no usable timestamp: give the operation a full wait rather than fail it
    return accepted, max(waited, 0.0)


async def _accept(job: JobHandle, video: VideoChain, stored: StoredInputs) -> tuple[Accepted, float]:
    """The provider's operation for this job and the seconds it has already been waited on (research V3).

    A stored operation is polled, never submitted again. `submit.json` without an operation means a request may
    have been accepted whose reference was lost: resubmitting could charge twice, so the job fails instead.
    """
    operation = job.dir / OPERATION_JSON
    if await asyncio.to_thread(operation.exists):
        return await asyncio.to_thread(_read_operation, operation)
    if await asyncio.to_thread((job.dir / SUBMIT_JSON).exists):
        raise AppError("resume_impossible", _MESSAGES["resume_impossible"], 502)
    return await _submit(job, video, stored), 0.0


async def _wait(job: JobHandle, video: VideoChain, accepted: Accepted, waited_s: float = 0.0) -> PollResult:
    """Poll until the provider finishes. A cancel or the wait cap stops the generation at the provider too.

    The cap counts from the original submission (`waited_s` is already spent), so restarts cannot stretch it; a
    resumed job past the cap still gets one poll, because its clip may have finished while the server was down.
    """
    deadline = time.monotonic() + max(WAIT_CAP_S - waited_s, 0.0)
    while True:
        try:
            await asyncio.to_thread(job.check_cancelled)
        except JobCancelled:
            await asyncio.to_thread(video.cancel, accepted)
            raise
        try:
            result = await asyncio.to_thread(video.poll, accepted, job.dir)
        except VideoError as exc:
            # A raised provider_unavailable is a transport or 5xx hiccup: the request is accepted, so keep waiting
            # (until the cap). Every other raised code is final.
            if exc.code != "provider_unavailable":
                raise _failure(exc.code) from None
        except Exception as exc:  # the chain lets adapter and I/O errors through: type only, never the message
            logger.error("Video poll failed (%s)", type(exc).__name__)
            raise _failure("video_failed") from None
        else:
            if result.error is not None:
                raise _failure(result.error.code)
            if result.done:
                if result.video is None:
                    raise _failure("video_failed")
                return result
        if time.monotonic() >= deadline:
            await asyncio.to_thread(video.cancel, accepted)
            raise _failure("provider_timeout")
        await asyncio.sleep(POLL_INTERVAL_S)


def _charge(
    job: JobHandle, ctx: CoreContext, video: VideoChain, accepted: Accepted, stored: StoredInputs, reported: Usage
) -> None:
    """Add the clip's usage and cost. A provider that reports no video tokens (e.g. polled again after a restart)
    is billed at the documented rate for the stored inputs."""
    usage = reported
    if usage.video_output_tokens == 0:
        usage = estimated_usage(stored.fields, len(stored.pictures), video.capabilities())
    model = accepted.provider.partition(":")[2]
    job.add_usage(usage, estimate(usage, model, ctx.settings.pricing_overrides))


def _orientation(width: int, height: int) -> str:
    """The delivered clip's orientation as ``9:16`` (taller than wide) or ``16:9``; the provider may not honour the
    request, so this comes from the probed size."""
    return "9:16" if height > width else "16:9"


def _resolution(width: int, height: int) -> str:
    """The delivered clip's resolution from its short side: 1080 -> ``1080p``, 720 -> ``720p``, n -> ``<n>p``."""
    return f"{min(width, height)}p"


async def run(job: JobHandle, ctx: CoreContext) -> None:
    video = ctx.video
    if video is None:  # unreachable while the tool is only enabled with a provider; fail readably if it happens
        raise _failure("provider_unavailable")
    job.set_state("generating")
    stored = await asyncio.to_thread(load_inputs, job.dir)
    accepted, waited_s = await _accept(job, video, stored)
    result = await _wait(job, video, accepted, waited_s)
    assert result.video is not None  # _wait only returns finished results that carry the video
    await asyncio.to_thread(_charge, job, ctx, video, accepted, stored, result.usage)  # paid even if finalizing fails
    clip = await asyncio.to_thread(ctx.media.finalize_clip, result.video, job.dir)
    fields = stored.fields
    await asyncio.to_thread(
        _write_json,
        job.dir / RESULT_JSON,
        {
            "file": clip.file,
            "thumbnail": clip.thumbnail,
            "download_name": download_name(fields.prompt),
            "duration_s": clip.duration_s,
            "width": clip.width,
            "height": clip.height,
            "aspect_ratio": _orientation(clip.width, clip.height),
            "resolution": _resolution(clip.width, clip.height),
            "size_bytes": clip.size_bytes,
        },
    )
    await asyncio.to_thread(video.forget, accepted)  # best-effort inside the chain
