"""YouTube Prep HTTP routes, mounted at `/api/tools/youtube_prep` (contracts/http-api.md)."""

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from videokit.core import uploads
from videokit.core.errors import AppError
from videokit.core.registry import JobAccess, JobNotFound, job_access
from videokit.tools.youtube_prep import pipeline
from videokit.tools.youtube_prep.languages import AUTO, LANGUAGES, LanguageSetting, normalize_fallback, write_setting
from videokit.tools.youtube_prep.schema import SECTIONS

TOOL_ID = "youtube_prep"
MAX_HINT_CHARS = 500

router = APIRouter()

# Per-job regeneration locks (one process, one event loop). A request finding its job's lock held is rejected
# instead of waiting; the entry is dropped once released, so the dict only holds jobs being regenerated.
_locks: dict[str, asyncio.Lock] = {}


class RegenerateRequest(BaseModel):
    section: str
    hint: str | None = Field(default=None, max_length=MAX_HINT_CHARS)


def _not_found() -> AppError:
    return AppError("job_not_found", "This job was not found. It may have expired.", 404)


class CreateJobRequest(uploads.CreateUploadRequest):
    language: str = AUTO
    fallback_language: str | None = Field(default=None, max_length=35)


def _store_language(job_dir: Path, setting: LanguageSetting) -> None:
    job_dir.mkdir(parents=True, exist_ok=True)  # a new job has no directory until its first file
    write_setting(job_dir, setting)


@router.get("/options")
async def options() -> dict[str, list[str]]:
    """The output languages a job can be asked for (no provider call)."""
    return {"languages": sorted(LANGUAGES)}


@router.post("/jobs", status_code=201)
async def create_job(body: CreateJobRequest, request: Request) -> dict[str, str]:
    """Create a job and its tus upload (the client then sends the video to the returned `upload_url`)."""
    if body.language != AUTO and body.language not in LANGUAGES:
        raise AppError("invalid_language", "This language is not supported.", 422)
    setting = LanguageSetting(choice=body.language, fallback=normalize_fallback(body.fallback_language))
    created = await uploads.create_upload_job(request, TOOL_ID, body)
    access = job_access(request)
    try:
        await asyncio.to_thread(_store_language, access.job_dir(created["job_id"]), setting)
    except BaseException:
        await access.discard(created["job_id"])
        raise
    return created


def _regenerate(access: JobAccess, job_id: str, section: str, hint: str | None) -> dict[str, Any]:
    """Blocking part: checks, provider calls and the result update (run in a worker thread)."""
    job = access.get(job_id)
    if job is None or job.tool_id != TOOL_ID:
        raise _not_found()
    if job.state != "done":
        raise AppError("job_not_done", "This job is not finished yet, so it cannot be regenerated.", 409)
    try:
        result = pipeline.regenerate(access.handle(job_id), access.ctx, section, hint, job.providers)
    except FileNotFoundError, JobNotFound:  # the job (files or row) was purged meanwhile
        raise _not_found() from None
    after = access.get(job_id)
    if after is None:
        raise _not_found()
    # The job's accumulated usage, exactly as in GET /api/jobs/{id} (not this call's delta).
    return {"result": result.model_dump(mode="json"), "usage": after.usage}


@router.post("/jobs/{job_id}/regenerate")
async def regenerate(job_id: str, body: RegenerateRequest, request: Request) -> dict[str, Any]:
    """Regenerate one section of a done job, optionally steered by a hint (FR-031, US2)."""
    if body.section not in SECTIONS:
        raise AppError("invalid_section", "This section cannot be regenerated.", 422)
    lock = _locks.setdefault(job_id, asyncio.Lock())
    if lock.locked():
        raise AppError("regeneration_in_progress", "This job is already being regenerated. Please wait.", 409)
    try:
        async with lock:
            return await asyncio.to_thread(_regenerate, job_access(request), job_id, body.section, body.hint)
    finally:
        if not lock.locked():
            _locks.pop(job_id, None)
