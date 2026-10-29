"""YouTube Prep: cover titles, title, description, tags, hashtags and chapters for a video."""

import asyncio

from videokit.core.config import Settings
from videokit.core.registry import CoreContext, JobHandle
from videokit.tools.youtube_prep import pipeline, routes


class YouTubePrepTool:
    id = routes.TOOL_ID
    name = "YouTube Prep"
    description = "Title, description, tags and chapters for a video."
    router = routes.router
    concurrency = 1
    resume_after_restart = False

    def enabled(self, settings: Settings) -> bool:
        return True

    def accept(self, job: JobHandle, ctx: CoreContext) -> None:
        """Inputs are complete: check the upload is a playable video and record its duration."""
        job.record(duration_s=ctx.media.probe_source(job.dir).duration_s)

    async def run(self, job: JobHandle, ctx: CoreContext) -> None:
        await asyncio.to_thread(pipeline.run, job, ctx)


TOOL = YouTubePrepTool()
