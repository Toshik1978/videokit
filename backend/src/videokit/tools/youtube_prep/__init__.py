"""YouTube Prep: cover titles, title, description, tags, hashtags and chapters for a video."""

import asyncio

from fastapi import APIRouter

from videokit.core.registry import CoreContext, JobHandle
from videokit.tools.youtube_prep import pipeline


class YouTubePrepTool:
    id = "youtube_prep"
    name = "YouTube Prep"
    description = "Title, description, tags and chapters for a video."
    router = APIRouter()

    async def run(self, job: JobHandle, ctx: CoreContext) -> None:
        await asyncio.to_thread(pipeline.run, job, ctx)


TOOL = YouTubePrepTool()
