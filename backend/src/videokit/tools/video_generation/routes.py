"""Video Generation HTTP routes, mounted at `/api/tools/video_generation` (contracts/http-api.md, research V13, V14).

Creation takes one multipart request (no upload stage): the fields are validated, the pictures are normalized into the
job directory and the job is queued. The clip and its thumbnail are served from the job's `result.json`. Nothing here
logs the prompt, the pictures or a provider message.
"""

import asyncio
import json
import os
import re
from collections.abc import Mapping, MutableMapping
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from starlette.datastructures import FormData, UploadFile
from starlette.exceptions import HTTPException
from starlette.requests import Request as StarletteRequest

from videokit.core.downloads import content_disposition
from videokit.core.errors import AppError
from videokit.core.jobs import Job
from videokit.core.pricing import estimate, price_table
from videokit.core.registry import JobAccess, job_access
from videokit.core.videogen import Capabilities, Usage, VideoChain
from videokit.tools.video_generation.inputs import (
    MAX_PICTURE_BYTES,
    MAX_PICTURES,
    MAX_PROMPT_CHARS,
    PICTURES_DIR,
    load_inputs,
    parse_fields,
    store_inputs,
)
from videokit.tools.video_generation.naming import download_name

TOOL_ID = "video_generation"
RESULT_JSON = "result.json"
CLIP_FALLBACK_NAME = "clip.mp4"  # the plain `filename` of a download whose name has no ASCII letters or digits
MAX_BODY_BYTES = MAX_PICTURES * MAX_PICTURE_BYTES + 1_000_000  # seven pictures plus the small fields
MAX_FORM_FIELDS = 8  # the four text fields, with room to spare
DEFAULT_DURATION_S = 8
ASPECT_RATIOS = ("9:16", "16:9")
RESOLUTIONS = ("1080p", "720p")
PICTURE_TYPES = ("image/jpeg", "image/png", "image/webp", "image/heic", "image/heif")
PICTURE_NUMBER = re.compile(r"[1-9][0-9]{0,2}")  # 1-based and canonical: no sign, zero padding or non-ASCII digit

router = APIRouter()


def _not_found() -> AppError:
    return AppError("job_not_found", "This job was not found. It may have expired.", 404)


def _too_large() -> AppError:
    return AppError("picture_too_large", "The pictures are too large to upload together.", 413)


def _video(access: JobAccess) -> VideoChain:
    video = access.ctx.video
    if video is None:  # unreachable while the tool is only enabled with a provider
        raise AppError("provider_unavailable", "The video provider is not available right now.", 503)
    return video


# --- options ---------------------------------------------------------------------------------------------------


def _default_duration(durations: list[int]) -> int:
    """8 s when offered, else the offered value nearest to it (the shorter one on a tie)."""
    return min(durations, key=lambda seconds: (abs(seconds - DEFAULT_DURATION_S), seconds))


def _prices(
    caps: Capabilities, model: str, overrides: Mapping[str, Mapping[str, float]]
) -> tuple[dict[str, float] | None, float | None]:
    """USD per second of clip per resolution and per picture; each is None when the model has no such price."""
    prices = price_table(overrides).get(model, {})
    per_second: dict[str, float] | None = None
    if "video_output" in prices:
        per_second = {}
        for resolution, tokens in caps.video_tokens_per_second.items():
            cost = estimate(Usage(video_output_tokens=tokens), model, overrides)
            assert cost is not None  # the model is priced
            per_second[resolution] = cost
    per_picture = estimate(Usage(input_tokens=caps.tokens_per_image), model, overrides) if "input" in prices else None
    return per_second, per_picture


@router.get("/options")
async def options(request: Request) -> dict[str, Any]:
    """What the form offers and what its cost estimate needs (from the first provider and the price table)."""
    access = job_access(request)
    video = _video(access)
    caps = video.capabilities()
    per_second, per_picture = _prices(caps, video.model, access.ctx.settings.pricing_overrides)
    return {
        "durations_s": caps.durations_s,
        "default_duration_s": _default_duration(caps.durations_s),
        "aspect_ratios": list(ASPECT_RATIOS),
        "resolutions": list(RESOLUTIONS),
        "max_pictures": min(MAX_PICTURES, caps.max_pictures),
        "max_picture_bytes": MAX_PICTURE_BYTES,
        "picture_types": list(PICTURE_TYPES),
        "max_prompt_chars": MAX_PROMPT_CHARS,
        "price_per_second_usd": per_second,
        "price_per_picture_usd": per_picture,
    }


# --- creation --------------------------------------------------------------------------------------------------


async def _read_form(request: Request, max_files: int) -> FormData:
    """Parse the multipart body, stopping as soon as it passes `MAX_BODY_BYTES` (declared or actually received)."""
    declared = request.headers.get("content-length", "")
    if declared.isascii() and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise _too_large()
    received = 0

    async def capped_receive() -> MutableMapping[str, Any]:
        nonlocal received
        message: MutableMapping[str, Any] = await request.receive()
        if message["type"] == "http.request":
            received += len(message.get("body", b""))
            if received > MAX_BODY_BYTES:
                raise _too_large()
        return message

    try:
        return await StarletteRequest(request.scope, capped_receive).form(
            max_files=max_files, max_fields=MAX_FORM_FIELDS, max_part_size=MAX_PICTURE_BYTES
        )
    except HTTPException as exc:  # starlette's multipart parser: it only says what was wrong in its message
        if str(exc.detail).startswith("Too many files"):
            raise AppError("too_many_pictures", f"At most {max_files} pictures can be used.", 422) from None
        raise AppError("invalid_request", "The form could not be read. Check the submitted values.", 422) from None


def _text(form: FormData, name: str) -> str | None:
    """A text field's value; ``None`` when absent. A file sent under a text field's name is no valid value."""
    value = form.get(name)
    if value is None:
        return None
    return value if isinstance(value, str) else ""


def _picture_files(form: FormData) -> list[UploadFile]:
    """The `pictures` parts in form order; a file input left empty (no file name, no bytes) is no picture."""
    files: list[UploadFile] = []
    for value in form.getlist("pictures"):
        if isinstance(value, UploadFile):
            if value.filename or value.size:
                files.append(value)
        elif value:  # text sent under the file field's name; an empty value is just an empty file input
            raise AppError("invalid_request", "Pictures must be sent as files.", 422)
    return files


@router.post("/jobs", status_code=201)
async def create_job(request: Request) -> dict[str, str]:
    """Create a job from the prompt, options and pictures; it is queued when this answers (no upload stage)."""
    access = job_access(request)
    caps = _video(access).capabilities()
    limit = min(MAX_PICTURES, caps.max_pictures)
    form = await _read_form(request, MAX_PICTURES)
    try:
        fields = parse_fields(
            _text(form, "prompt"),
            _text(form, "duration_s"),
            _text(form, "aspect_ratio"),
            _text(form, "resolution"),
            durations=caps.durations_s,
        )
        pictures = _picture_files(form)
        if len(pictures) > limit:
            raise AppError("too_many_pictures", f"At most {limit} pictures can be used.", 422)
        job = await asyncio.to_thread(access.create, TOOL_ID)
        try:
            await asyncio.to_thread(
                store_inputs, access.job_dir(job.id), access.ctx.media, fields, [p.file for p in pictures]
            )
            await access.complete(job.id)
        except BaseException:  # also a cancelled request: never leave an `uploading` job nobody holds
            await asyncio.shield(access.discard(job.id))
            raise
    finally:
        await form.close()
    return {"job_id": job.id}


# --- files and inputs ------------------------------------------------------------------------------------------


def _read_result(job_dir: Path) -> dict[str, Any]:
    try:
        result = json.loads((job_dir / RESULT_JSON).read_text(encoding="utf-8"))
    except OSError, ValueError:
        raise _not_found() from None
    if not isinstance(result, dict):
        raise _not_found()
    return result


def _stored_file(result: dict[str, Any], key: str) -> str:
    """A plain file name from `result.json` (never a path), else the job counts as purged."""
    name = result.get(key)
    if not isinstance(name, str) or not name or Path(name).name != name or name.startswith("."):
        raise _not_found()
    return name


def _stored_download_name(result: dict[str, Any]) -> str:
    """The `download_name` in `result.json` when it is one `download_name` could have produced, else `clip.mp4`."""
    name = result.get("download_name")
    if isinstance(name, str) and name.endswith(".mp4") and download_name(name.removesuffix(".mp4")) == name:
        return name
    return CLIP_FALLBACK_NAME


async def _tool_job(job_id: str, request: Request) -> tuple[JobAccess, Job]:
    """The access and the job; 404 `job_not_found` for an unknown, expired or other tool's job."""
    access = job_access(request)
    job = await asyncio.to_thread(access.get, job_id)
    if job is None or job.tool_id != TOOL_ID:
        raise _not_found()
    return access, job


async def _finished_file(job_id: str, request: Request, key: str) -> tuple[Path, os.stat_result, dict[str, Any]]:
    """The path and stat of a finished job's file (`file` or `thumbnail` in `result.json`), and the result itself.

    404 `job_not_found` for an unknown, expired or other tool's job (or a purged file), 409 `job_not_done` otherwise.
    """
    access, job = await _tool_job(job_id, request)
    if job.state != "done":
        raise AppError("job_not_done", "This job is not finished yet, so there is nothing to download.", 409)
    job_dir = access.job_dir(job_id)
    result = await asyncio.to_thread(_read_result, job_dir)
    path = job_dir / _stored_file(result, key)
    try:
        stat = await asyncio.to_thread(os.stat, path)
    except FileNotFoundError:  # purged between the checks
        raise _not_found() from None
    return path, stat, result


@router.get("/jobs/{job_id}/clip")
@router.head("/jobs/{job_id}/clip")
async def clip(job_id: str, request: Request) -> FileResponse:
    """The MP4 as an attachment named after the prompt; HEAD and byte ranges work (preview seeking)."""
    path, stat, result = await _finished_file(job_id, request, "file")
    name = _stored_download_name(result)
    return FileResponse(
        path,
        media_type="video/mp4",
        filename=name,
        stat_result=stat,
        headers={"Content-Disposition": content_disposition(name, fallback=CLIP_FALLBACK_NAME)},
    )


@router.get("/jobs/{job_id}/thumbnail")
@router.head("/jobs/{job_id}/thumbnail")
async def thumbnail(job_id: str, request: Request) -> FileResponse:
    """The clip's first-frame JPEG."""
    path, stat, _ = await _finished_file(job_id, request, "thumbnail")
    return FileResponse(path, media_type="image/jpeg", stat_result=stat)


def _stored_inputs(job_dir: Path, job_id: str) -> dict[str, Any]:
    """The job's inputs as the API shows them; incomplete or missing inputs (data deleted or purged) count as gone."""
    try:
        stored = load_inputs(job_dir)
    except AppError:
        raise _not_found() from None
    return {
        "prompt": stored.fields.prompt,
        "duration_s": stored.fields.duration_s,
        "aspect_ratio": stored.fields.aspect_ratio,
        "resolution": stored.fields.resolution,
        "pictures": [f"/api/tools/{TOOL_ID}/jobs/{job_id}/{PICTURES_DIR}/{path.stem}" for path in stored.pictures],
    }


@router.get("/jobs/{job_id}/inputs")
async def inputs(job_id: str, request: Request) -> dict[str, Any]:
    """What the job was created from (any state), for Try again and the recent clips: fields and picture URLs."""
    access, _ = await _tool_job(job_id, request)
    return await asyncio.to_thread(_stored_inputs, access.job_dir(job_id), job_id)


@router.get("/jobs/{job_id}/pictures/{n}")
@router.head("/jobs/{job_id}/pictures/{n}")
async def picture(job_id: str, n: str, request: Request) -> FileResponse:
    """The normalized picture `n` (1-based) as a JPEG, in any job state."""
    access, _ = await _tool_job(job_id, request)
    if not PICTURE_NUMBER.fullmatch(n):
        raise _not_found()
    path = access.job_dir(job_id) / PICTURES_DIR / f"{n}.jpg"
    try:
        stat = await asyncio.to_thread(os.stat, path)
    except OSError:  # out of range, or the data was deleted or purged
        raise _not_found() from None
    return FileResponse(path, media_type="image/jpeg", stat_result=stat)
