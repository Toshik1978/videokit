"""Audio Extract HTTP routes, mounted at `/api/tools/audio_extract` (contracts/http-api.md, research A7)."""

import asyncio
import json
import os
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

from videokit.core import uploads
from videokit.core.downloads import content_disposition
from videokit.core.errors import AppError
from videokit.core.registry import job_access
from videokit.tools.audio_extract.naming import download_name

TOOL_ID = "audio_extract"
MP3_FILE = "audio.mp3"
RESULT_JSON = "result.json"

router = APIRouter()


def _not_found() -> AppError:
    return AppError("job_not_found", "This job was not found. It may have expired.", 404)


@router.post("/jobs", status_code=201)
async def create_job(body: uploads.CreateUploadRequest, request: Request) -> dict[str, str]:
    """Create a job and its tus upload (the client then sends the video to the returned `upload_url`)."""
    return await uploads.create_upload_job(request, TOOL_ID, body)


def _stored_name(job_dir: Path, source_name: str | None) -> str:
    """The `download_name` in the job's `result.json`, so Download and Share always agree; derived again from the
    uploaded name only when the file is absent or unreadable or holds no safe download name."""
    try:
        name = json.loads((job_dir / RESULT_JSON).read_text(encoding="utf-8")).get("download_name")
    except OSError, ValueError, AttributeError:
        name = None
    if isinstance(name, str) and download_name(name) == name:
        return name
    return download_name(source_name)


@router.get("/jobs/{job_id}/audio")
@router.head("/jobs/{job_id}/audio")
async def audio(job_id: str, request: Request) -> FileResponse:
    """The job's MP3 as an attachment named after the uploaded video (FR-006); HEAD and byte ranges work."""
    access = job_access(request)
    job = await asyncio.to_thread(access.get, job_id)
    if job is None or job.tool_id != TOOL_ID:
        raise _not_found()
    if job.state != "done":
        raise AppError("job_not_done", "This job is not finished yet, so there is nothing to download.", 409)
    job_dir = access.handle(job_id).dir
    path = job_dir / MP3_FILE
    try:
        stat = await asyncio.to_thread(os.stat, path)
    except FileNotFoundError:  # purged between the checks
        raise _not_found() from None
    name = await asyncio.to_thread(_stored_name, job_dir, job.source_name)
    return FileResponse(
        path,
        media_type="audio/mpeg",
        filename=name,
        stat_result=stat,
        headers={"Content-Disposition": content_disposition(name)},
    )
