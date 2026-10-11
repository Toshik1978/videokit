"""Platform API: health, config, tool discovery and job status (see contracts/http-api.md)."""

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request

from videokit.core.config import Settings
from videokit.core.errors import AppError
from videokit.core.jobs import Job, JobNotFound, JobState, JobStore
from videokit.core.registry import Tool

UPLOAD_CHUNK_BYTES = 50 * 1024 * 1024

router = APIRouter(prefix="/api")


def provider_status(settings: Settings, ready: bool) -> dict[str, list[dict[str, Any]]]:
    """Configured providers with a non-secret detail. Real per-provider checks come with T036."""

    def transcription(name: str) -> str:
        if name == "local":
            return f"device={settings.whisper_device}"
        if name == "openai":
            return f"model={settings.openai_transcribe_model}"
        return "unknown provider"

    def llm(name: str) -> str:
        if name == "anthropic":
            return f"model={settings.anthropic_model}"
        if name == "openai":
            return f"model={settings.openai_model}"
        return "unknown provider"

    return {
        "transcription": [
            {"name": n, "ready": ready, "detail": transcription(n)} for n in settings.transcribe_providers
        ],
        "llm": [{"name": n, "ready": ready, "detail": llm(n)} for n in settings.llm_providers],
    }


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
    except (OSError, ValueError):
        return None


def _not_found() -> AppError:
    return AppError("job_not_found", "This job was not found. It may have expired.", 404)


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    store: JobStore = request.app.state.store
    return {
        "status": "ok",
        "providers": request.app.state.provider_status,
        "queue_length": await asyncio.to_thread(store.queue_length),
    }


@router.get("/config")
async def config(request: Request) -> dict[str, int]:
    settings: Settings = request.app.state.settings
    return {
        "max_upload_bytes": settings.max_upload_bytes,
        "upload_chunk_bytes": UPLOAD_CHUNK_BYTES,
        "job_ttl_hours": settings.job_ttl_hours,
    }


@router.get("/tools")
async def list_tools(request: Request) -> list[dict[str, str]]:
    tools: dict[str, Tool] = request.app.state.tools
    return [{"id": t.id, "name": t.name, "description": t.description} for t in tools.values()]


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, request: Request) -> dict[str, Any]:
    store: JobStore = request.app.state.store

    def load() -> dict[str, Any]:
        job = store.get(job_id)
        if job is None:
            raise _not_found()
        try:
            position = store.queue_position(job_id)
        except JobNotFound:  # purged between the two reads
            raise _not_found() from None
        return job_view(job, position, _read_result(store, job))

    return await asyncio.to_thread(load)
