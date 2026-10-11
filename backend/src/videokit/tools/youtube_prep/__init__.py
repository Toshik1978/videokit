"""YouTube Prep: cover titles, title, description, tags, hashtags and chapters for a video."""

import asyncio

from videokit.core.registry import CoreContext, JobHandle
from videokit.tools.youtube_prep import pipeline, routes


class YouTubePrepTool:
    id = routes.TOOL_ID
    name = "YouTube Prep"
    description = "Title, description, tags and chapters for a video."
    router = routes.router

    async def run(self, job: JobHandle, ctx: CoreContext) -> None:
        await asyncio.to_thread(pipeline.run, job, ctx)


TOOL = YouTubePrepTool()
