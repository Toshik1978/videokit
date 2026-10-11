from fastapi import APIRouter

from videokit.core.registry import CoreContext, JobHandle


class DummyTool:
    id = "alpha"
    name = "Dummy alpha"
    description = "A dummy tool."
    router = APIRouter()

    async def run(self, job: JobHandle, ctx: CoreContext) -> None:
        job.check_cancelled()


TOOL = DummyTool()
