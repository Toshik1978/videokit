"""Video Generation: a short silent clip from a prompt and up to seven reference pictures."""

from videokit.core.config import Settings
from videokit.core.registry import CoreContext, JobHandle
from videokit.tools.video_generation import pipeline
from videokit.tools.video_generation.inputs import load_inputs
from videokit.tools.video_generation.routes import TOOL_ID, router


class VideoGenerationTool:
    id = TOOL_ID
    name = "Video Generation"
    description = "Generate a short silent clip from a prompt and pictures."
    router = router
    concurrency = 3
    resume_after_restart = True

    def enabled(self, settings: Settings) -> bool:
        return bool(settings.video_providers)

    def accept(self, job: JobHandle, ctx: CoreContext) -> None:
        """Inputs are complete when `inputs.json` is there and valid with every picture it lists (it is written
        last); anything else fails the job."""
        load_inputs(job.dir)

    async def run(self, job: JobHandle, ctx: CoreContext) -> None:
        await pipeline.run(job, ctx)


TOOL = VideoGenerationTool()
