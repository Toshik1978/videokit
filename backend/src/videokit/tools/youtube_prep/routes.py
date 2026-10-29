"""YouTube Prep HTTP routes, mounted at `/api/tools/youtube_prep` (contracts/http-api.md)."""

import asyncio
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from videokit.core import uploads
from videokit.core.errors import AppError
from videokit.core.registry import JobAccess, JobNotFound, job_access
from videokit.tools.youtube_prep import pipeline
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


@router.post("/jobs", status_code=201)
async def create_job(body: uploads.CreateUploadRequest, request: Request) -> dict[str, str]:
    """Create a job and its tus upload (the client then sends the video to the returned `upload_url`)."""
    return await uploads.create_upload_job(request, TOOL_ID, body)


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
