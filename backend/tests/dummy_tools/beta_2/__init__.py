from fastapi import APIRouter

from videokit.core.registry import CoreContext, JobHandle


class DummyTool:
    id = "beta_2"
    name = "Dummy beta"
    description = "Another dummy tool."
    router = APIRouter()

    async def run(self, job: JobHandle, ctx: CoreContext) -> None:
        job.check_cancelled()


TOOL = DummyTool()
