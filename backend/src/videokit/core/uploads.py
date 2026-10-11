"""tus 1.0.0 core resumable uploads (see contracts/http-api.md)."""

import asyncio
import shutil
from collections.abc import Awaitable, Callable, MutableMapping
from pathlib import Path
from typing import Any, BinaryIO

from fastapi import APIRouter, Depends, Request, Response
from starlette.requests import ClientDisconnect

from videokit.core.errors import AppError
from videokit.core.jobs import SOURCE_FILE, InvalidTransition, Job, JobNotFound, JobState, JobStore

UPLOAD_CHUNK_BYTES = 50_000_000  # 50 MB per-PATCH cap; also advertised by /api/config
WRITE_BYTES = 1024 * 1024  # body bytes gathered before each disk write and offset update
TUS_VERSION = "1.0.0"
OFFSET_CONTENT_TYPE = "application/offset+octet-stream"

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]


class TusResumableMiddleware:
    """Adds `Tus-Resumable` to every response under `/api/uploads/`, errors included."""

    def __init__(self, app: Callable[[Scope, Receive, Send], Awaitable[None]]) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/api/uploads/"):
            await self.app(scope, receive, send)
            return

        async def send_with_header(message: Message) -> None:
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).append((b"tus-resumable", TUS_VERSION.encode()))
            await send(message)

        await self.app(scope, receive, send_with_header)


def _require_tus_version(request: Request) -> None:
    if request.headers.get("tus-resumable") != TUS_VERSION:
        raise AppError("unsupported_tus_version", "Unsupported upload protocol version.", 412)


router = APIRouter(prefix="/uploads")
_patching: set[str] = set()  # ids of jobs with a PATCH in progress (one at a time per upload)


def _not_found() -> AppError:
    return AppError("upload_not_found", "This upload was not found. It may have expired.", 404)


def _too_large() -> AppError:
    return AppError("chunk_too_large", "The uploaded chunk is too large.", 413)


def _uploading_job(store: JobStore, job_id: str) -> Job:
    job = store.get(job_id)
    if job is None or job.state is not JobState.UPLOADING or store.is_expired(job):
        raise _not_found()
    return job


def _parse_offset(request: Request) -> int:
    raw = request.headers.get("upload-offset", "")
    if not raw.isascii() or not raw.isdigit():
        raise AppError("invalid_request", "A valid Upload-Offset header is required.", 400)
    return int(raw)


@router.options("/{job_id}")
async def upload_options(request: Request) -> Response:
    return Response(
        status_code=204,
        headers={"Tus-Version": TUS_VERSION, "Tus-Max-Size": str(request.app.state.settings.max_upload_bytes)},
    )


@router.head("/{job_id}", dependencies=[Depends(_require_tus_version)])
async def upload_head(job_id: str, request: Request) -> Response:
    job = await asyncio.to_thread(_uploading_job, request.app.state.store, job_id)
    return Response(
        status_code=200,
        headers={
            "Upload-Offset": str(job.upload_offset),
            "Upload-Length": str(job.upload_length),
            "Cache-Control": "no-store",
        },
    )


def _prepare(path: Path, offset: int) -> None:
    """Create the file if needed and drop any bytes beyond the recorded offset (an aborted earlier PATCH)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    with path.open("r+b") as f:
        f.truncate(offset)


def _open(path: Path, offset: int) -> BinaryIO:
    f = path.open("r+b")
    f.seek(offset)
    return f


def _append(f: BinaryIO, data: bytearray) -> None:
    f.write(data)
    f.flush()


def _write(f: BinaryIO, data: bytearray, store: JobStore, job_id: str, new_offset: int) -> None:
    """Append `data` and record the new offset, so an interrupted request keeps every byte written so far."""
    _append(f, data)
    store.set_upload_offset(job_id, new_offset)


def _discard_if_gone(store: JobStore, job_id: str) -> None:
    """After a write raced a cancel or purge: remove whatever this request (re)created in the job directory."""
    job = store.get(job_id)
    if job is None or job.state is JobState.CANCELLED:
        shutil.rmtree(store.job_dir(job_id), ignore_errors=True)


async def _receive(request: Request, store: JobStore, job_id: str, offset: int, limit: int) -> int:
    """Stream the body into the source file at `offset` through one file handle, in writes of about `WRITE_BYTES`,
    recording the offset after each. Returns the new offset. If the client disconnects mid-body, the bytes that
    arrived are kept; a body over `limit` is rolled back to `offset` (413)."""
    path = store.job_dir(job_id) / SOURCE_FILE
    await asyncio.to_thread(_prepare, path, offset)
    f = await asyncio.to_thread(_open, path, offset)
    written = 0
    pending = bytearray()
    try:
        try:
            async for chunk in request.stream():
                if written + len(pending) + len(chunk) > limit:
                    if written:
                        await asyncio.to_thread(store.set_upload_offset, job_id, offset)
                    await asyncio.to_thread(_prepare, path, offset)
                    raise _too_large()
                pending += chunk
                if len(pending) >= WRITE_BYTES:
                    await asyncio.to_thread(_write, f, pending, store, job_id, offset + written + len(pending))
                    written += len(pending)
                    pending.clear()
        except ClientDisconnect:
            pass  # keep what arrived; the client resumes from the offset HEAD reports
        if pending:
            await asyncio.to_thread(_write, f, pending, store, job_id, offset + written + len(pending))
            written += len(pending)
        if not written:  # no byte arrived: still fail (404) if the job was cancelled or purged meanwhile
            await asyncio.to_thread(store.set_upload_offset, job_id, offset)
    finally:
        await asyncio.to_thread(f.close)
    return offset + written


@router.patch("/{job_id}", dependencies=[Depends(_require_tus_version)])
async def upload_patch(job_id: str, request: Request) -> Response:
    """Append one chunk. One PATCH per upload at a time: a concurrent one gets `409 upload_in_progress` at once
    (waiting could hang behind a stalled connection); like on `offset_mismatch`, the client resyncs with HEAD."""
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != OFFSET_CONTENT_TYPE:
        raise AppError("unsupported_media_type", f"Content-Type must be {OFFSET_CONTENT_TYPE}.", 415)
    offset = _parse_offset(request)
    if job_id in _patching:
        raise AppError("upload_in_progress", "This file is already being uploaded by another request.", 409)
    _patching.add(job_id)
    try:
        return await _patch(job_id, offset, request)
    finally:
        _patching.discard(job_id)


async def _patch(job_id: str, offset: int, request: Request) -> Response:
    store: JobStore = request.app.state.store
    job = await asyncio.to_thread(_uploading_job, store, job_id)
    if offset != job.upload_offset:
        raise AppError("offset_mismatch", "Upload-Offset does not match the stored offset.", 409)

    limit = min(UPLOAD_CHUNK_BYTES, job.upload_length - offset)
    declared = request.headers.get("content-length", "")
    if declared.isascii() and declared.isdigit() and int(declared) > limit:
        raise _too_large()

    try:
        new_offset = await _receive(request, store, job_id, offset, limit)
    except AppError:  # chunk too large
        await asyncio.to_thread(_discard_if_gone, store, job_id)
        raise
    except FileNotFoundError, JobNotFound, InvalidTransition:  # cancelled or purged mid-upload
        await asyncio.to_thread(_discard_if_gone, store, job_id)
        raise _not_found() from None
    if new_offset == job.upload_length:  # also on a repeated PATCH, to retry a completion that never ran (idempotent)
        await asyncio.to_thread(_uploading_job, store, job_id)  # expired while receiving: 404, do not queue
        await request.app.state.on_upload_complete(job_id)
    return Response(status_code=204, headers={"Upload-Offset": str(new_offset)})
