"""Audio Extract: a video's audio as a 192 kbps MP3, named after the video."""

import asyncio
import json
import os

from videokit.core.registry import CoreContext, JobHandle
from videokit.tools.audio_extract import routes
from videokit.tools.audio_extract.naming import download_name

RESULT_JSON = "result.json"
BITRATE_KBPS = 192


def _run(job: JobHandle, ctx: CoreContext) -> None:
    """Blocking part (one stage, no provider calls, no usage): encode the MP3 and write `result.json` (A10)."""
    job.set_state("extracting")
    job.check_cancelled()
    track = ctx.media.extract_mp3(job.dir)
    result = {
        "file": track.file,
        "download_name": download_name(job.source_name),
        "duration_s": track.duration_s,
        "size_bytes": track.size_bytes,
        "bitrate_kbps": BITRATE_KBPS,
    }
    target = job.dir / RESULT_JSON
    tmp = target.with_name(f"{RESULT_JSON}.tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, target)  # readers never see a partial file


class AudioExtractTool:
    id = routes.TOOL_ID
    name = "Audio Extract"
    description = "Get a video's audio as an MP3."
    router = routes.router

    async def run(self, job: JobHandle, ctx: CoreContext) -> None:
        await asyncio.to_thread(_run, job, ctx)


TOOL = AudioExtractTool()
