"""Audio Extract HTTP routes, mounted at `/api/tools/audio_extract` (contracts/http-api.md, research A7)."""

import asyncio
import json
import os
import re
import unicodedata
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

from videokit.core.errors import AppError
from videokit.core.registry import job_access
from videokit.tools.audio_extract.naming import download_name

TOOL_ID = "audio_extract"
MP3_FILE = "audio.mp3"
RESULT_JSON = "result.json"
_ASCII_FALLBACK = "audio.mp3"
# Characters kept in the ASCII fallback; anything else (quotes, backslashes, slashes, `;`, `=`, ...) becomes `_`.
_NOT_FALLBACK_SAFE = re.compile(r"[^A-Za-z0-9 ._()\[\],!&'+@#$%~-]")

router = APIRouter()


def _not_found() -> AppError:
    return AppError("job_not_found", "This job was not found. It may have expired.", 404)


def _ascii_fallback(name: str) -> str:
    """An ASCII stand-in for `name` in the plain `filename` parameter: accents dropped, everything outside a
    conservative allowlist `_`, `audio.mp3` when no letter or digit is left.

    The allowlist is applied after NFKD, which maps fullwidth and small forms back to ASCII syntax (U+FF02 to `"`,
    U+FE68 to `\\`, U+FF0F to `/`), so the result can never close the quoted string or add a parameter.
    """
    decomposed = "".join(c for c in unicodedata.normalize("NFKD", name) if not unicodedata.combining(c))
    ascii_name = _NOT_FALLBACK_SAFE.sub("_", decomposed)
    stem = ascii_name.removesuffix(".mp3")
    return ascii_name if any(c.isalnum() for c in stem) else _ASCII_FALLBACK


def content_disposition(name: str) -> str:
    """`attachment` with a plain `filename`, plus an RFC 5987 `filename*` for non-ASCII names and names with `%`.

    `name` comes from `download_name`, which removes quotes, backslashes, slashes and control characters, so an
    ASCII name is safe inside the quoted string as is; a non-ASCII name gets a sanitized ASCII fallback there. Some
    browsers decode `%XX` in a plain `filename`, so a name with `%` also gets `filename*`, percent-encoded in full,
    which browsers prefer.
    """
    if name.isascii() and "%" not in name:
        return f'attachment; filename="{name}"'
    fallback = name if name.isascii() else _ascii_fallback(name)
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(name, safe='')}"


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
