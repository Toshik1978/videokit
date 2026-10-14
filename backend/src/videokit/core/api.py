"""Platform API: health, config, tool discovery and job status (see contracts/http-api.md)."""

import asyncio
import json
import re
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, StrictInt

from videokit.core import providers, uploads
from videokit.core.config import Settings
from videokit.core.errors import AppError
from videokit.core.jobs import InvalidTransition, Job, JobNotFound, JobState, JobStore
from videokit.core.registry import Tool
from videokit.core.worker import Worker

router = APIRouter(prefix="/api")


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def job_view(job: Job, queue_position: int, result: Any = None) -> dict[str, Any]:
    return {
        "job_id": job.id,
        "tool_id": job.tool_id,
        "state": job.state.value,
        "upload": {"offset": job.upload_offset, "length": job.upload_length},
        "queue_position": queue_position or None,
        "failure_reason": job.failure_reason,
        "failure_code": job.failure_code,
        "expires_at": _iso(job.expires_at),
        "duration_s": job.duration_s,
        "language": job.language,
        "providers": job.providers,
        "usage": job.usage,
        "result": result,
    }


def _read_result(store: JobStore, job: Job) -> Any:
    """The tool's `result.json` for a done job; None if absent or unreadable."""
    if job.state is not JobState.DONE:
        return None
    try:
        return json.loads((store.job_dir(job.id) / "result.json").read_text(encoding="utf-8"))
    except OSError, ValueError:
        return None


def _not_found() -> AppError:
    return AppError("job_not_found", "This job was not found. It may have expired.", 404)


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    store: JobStore = request.app.state.store
    return {
        "status": "ok",
        "providers": await asyncio.to_thread(providers.status, request.app.state.providers),
        "queue_length": await asyncio.to_thread(store.queue_length),
    }


@router.get("/config")
async def config(request: Request) -> dict[str, int]:
    settings: Settings = request.app.state.settings
    return {
        "max_upload_bytes": settings.max_upload_bytes,
        "upload_chunk_bytes": uploads.UPLOAD_CHUNK_BYTES,
        "job_ttl_hours": settings.job_ttl_hours,
    }


@router.get("/tools")
async def list_tools(request: Request) -> list[dict[str, str]]:
    tools: dict[str, Tool] = request.app.state.tools
    return [{"id": t.id, "name": t.name, "description": t.description} for t in tools.values()]


_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _clean_file_name(name: str | None) -> str | None:
    """Last path component only, no control characters, trimmed, at most 255 characters; empty becomes None."""
    if name is None:
        return None
    last = re.split(r"[/\\]", name)[-1]
    return _CONTROL_CHARS.sub("", last).strip()[:255] or None


class CreateJobRequest(BaseModel):
    tool_id: str
    size: StrictInt
    file_name: str | None = Field(default=None, max_length=1024)


@router.post("/jobs", status_code=201)
async def create_job(body: CreateJobRequest, request: Request) -> dict[str, str]:
    settings: Settings = request.app.state.settings
    tools: dict[str, Tool] = request.app.state.tools
    store: JobStore = request.app.state.store
    if body.size <= 0:
        raise AppError("invalid_request", "The file size must be a positive number of bytes.", 422)
    if body.tool_id not in tools:
        raise AppError("unknown_tool", "This tool does not exist.", 404)
    if body.size > settings.max_upload_bytes:
        limit_gb = settings.max_upload_bytes / 1024**3
        raise AppError("file_too_large", f"The file is larger than the {limit_gb:g} GB limit.", 413)
    job = await asyncio.to_thread(store.create, body.tool_id, body.size, _clean_file_name(body.file_name))
    return {"job_id": job.id, "upload_url": f"/api/uploads/{job.id}"}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, request: Request) -> dict[str, Any]:
    store: JobStore = request.app.state.store

    def load() -> dict[str, Any]:
        job = store.get(job_id)
        if job is None or store.is_expired(job):
            raise _not_found()
        try:
            position = store.queue_position(job_id)
        except JobNotFound:  # purged between the two reads
            raise _not_found() from None
        return job_view(job, position, _read_result(store, job))

    return await asyncio.to_thread(load)


@router.post("/jobs/{job_id}/cancel", status_code=202)
async def cancel_job(job_id: str, request: Request) -> dict[str, Any]:
    """FR-037: uploading/queued jobs are cancelled at once; running jobs stop at the next stage boundary."""
    store: JobStore = request.app.state.store
    worker: Worker = request.app.state.worker
    try:
        job = await worker.cancel(job_id)
        position = await asyncio.to_thread(store.queue_position, job_id)
    except JobNotFound:
        raise _not_found() from None
    except InvalidTransition:
        raise AppError("not_cancellable", "This job has already finished and cannot be cancelled.", 409) from None
    return job_view(job, position)
