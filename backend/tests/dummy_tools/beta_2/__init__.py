from fastapi import APIRouter

from videokit.core.config import Settings
from videokit.core.registry import CoreContext, JobHandle


class DummyTool:
    id = "beta_2"
    name = "Dummy beta"
    description = "Another dummy tool."
    router = APIRouter()
    concurrency = 1
    resume_after_restart = False

    def enabled(self, settings: Settings) -> bool:
        return True

    def accept(self, job: JobHandle, ctx: CoreContext) -> None:
        return None

    async def run(self, job: JobHandle, ctx: CoreContext) -> None:
        job.check_cancelled()


TOOL = DummyTool()
