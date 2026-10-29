"""`JobAccess` job-creation building blocks (create, job_dir, complete, discard), used through a tool's own routes."""

import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi import APIRouter, Request
from fastapi.testclient import TestClient
from pydantic import SecretStr

from fakes_llm import FakeLLM
from fakes_transcribe import FakeTranscriber
from videokit.core.config import Settings
from videokit.core.errors import AppError
from videokit.core.jobs import Job, JobState, JobStore
from videokit.core.llm import LLMChain
from videokit.core.registry import CoreContext, JobHandle, job_access
from videokit.core.transcribe import TranscriberChain
from videokit.core.worker import Providers
from videokit.main import create_app

INPUT_FILE = "inputs.json"


class NoUploadTool:
    """A tool that takes no resumable upload: its routes write the inputs themselves, then complete the job."""

    id = "no_upload"
    name = "No upload"
    description = "Writes its own inputs."
    concurrency = 1
    resume_after_restart = False

    def __init__(self) -> None:
        self.router = APIRouter()
        self.router.add_api_route("/make", self._make, methods=["POST"], status_code=201)
        self.router.add_api_route("/make-twice", self._make_twice, methods=["POST"], status_code=201)
        self.router.add_api_route("/broken", self._broken, methods=["POST"], status_code=201)
        self.router.add_api_route("/sized", self._sized, methods=["POST"], status_code=201)
        self.accepted: list[str] = []
        self.accept_error: AppError | None = None

    def enabled(self, settings: Settings) -> bool:
        return True

    def accept(self, job: JobHandle, ctx: CoreContext) -> None:
        self.accepted.append(job.id)
        if self.accept_error is not None:
            raise self.accept_error
        assert (job.dir / INPUT_FILE).is_file()

    async def run(self, job: JobHandle, ctx: CoreContext) -> None:
        job.set_state("extracting")
        (job.dir / "result.json").write_text("{}", encoding="utf-8")

    def _write_inputs(self, request: Request, source_name: str | None = None) -> Job:
        access = job_access(request)
        job = access.create(self.id, source_name=source_name)
        directory = access.job_dir(job.id)
        directory.mkdir(parents=True)
        (directory / INPUT_FILE).write_text("{}", encoding="utf-8")
        return job

    async def _make(self, request: Request) -> dict[str, str]:
        job = self._write_inputs(request, "clip.mp4")
        await job_access(request).complete(job.id)
        return {"job_id": job.id}

    async def _make_twice(self, request: Request) -> dict[str, str]:
        job = self._write_inputs(request)
        await job_access(request).complete(job.id)
        await job_access(request).complete(job.id)
        return {"job_id": job.id}

    async def _broken(self, request: Request) -> dict[str, str]:
        job = self._write_inputs(request)
        await job_access(request).discard(job.id)
        return {"job_id": job.id}

    async def _sized(self, request: Request) -> dict[str, str]:
        job = job_access(request).create(self.id, upload_length=int(request.query_params["length"]))
        return {"job_id": job.id}


@pytest.fixture
def tool() -> NoUploadTool:
    return NoUploadTool()


@pytest.fixture
def client(tmp_path: Path, tool: NoUploadTool) -> Iterator[TestClient]:
    settings = Settings(data_dir=tmp_path / "data", anthropic_api_key=SecretStr("sk-test"), max_upload_gb=1)
    providers = Providers(transcriber=TranscriberChain([FakeTranscriber()]), llm=LLMChain([FakeLLM([])]))
    app = create_app(settings, providers, tools_package="videokit.tools", static_dir=tmp_path / "static")
    app.state.tools = {tool.id: tool}
    app.include_router(tool.router, prefix=f"/api/tools/{tool.id}")
    with TestClient(app) as c:
        yield c


def _store(client: TestClient) -> JobStore:
    store: JobStore = client.app.state.store  # type: ignore[attr-defined]
    return store


def _state(client: TestClient, job_id: str) -> JobState | None:
    job = _store(client).get(job_id)
    return None if job is None else job.state


def _wait(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached in time")
        time.sleep(0.01)


def _call(client: TestClient, method: str, *args: str) -> None:
    """Run an async `JobAccess` method on the app's event loop."""
    assert client.portal is not None
    client.portal.call(getattr(client.app.state.job_access, method), *args)  # type: ignore[attr-defined]


def test_create_without_upload_then_complete_queues_the_job(client: TestClient, tool: NoUploadTool) -> None:
    job_id = client.post("/api/tools/no_upload/make").json()["job_id"]

    job = _store(client).get(job_id)
    assert job is not None
    assert (job.tool_id, job.upload_length, job.source_name) == ("no_upload", 0, "clip.mp4")
    assert tool.accepted == [job_id]
    _wait(lambda: _state(client, job_id) is JobState.DONE)
    assert (_store(client).job_dir(job_id) / INPUT_FILE).is_file()


def test_complete_is_idempotent(client: TestClient, tool: NoUploadTool) -> None:
    job_id = client.post("/api/tools/no_upload/make-twice").json()["job_id"]
    assert tool.accepted == [job_id]


def test_complete_fails_the_job_with_the_tools_error(client: TestClient, tool: NoUploadTool) -> None:
    tool.accept_error = AppError("invalid_picture", "Bad picture.", 422)
    job_id = client.post("/api/tools/no_upload/make").json()["job_id"]
    job = _store(client).get(job_id)
    assert job is not None
    assert (job.state, job.failure_code, job.failure_reason) == (JobState.FAILED, "invalid_picture", "Bad picture.")


def test_discard_removes_the_row_and_the_directory(client: TestClient) -> None:
    job_id = client.post("/api/tools/no_upload/broken").json()["job_id"]
    assert _store(client).get(job_id) is None
    assert not _store(client).job_dir(job_id).exists()


def test_discard_leaves_a_job_that_is_no_longer_uploading(client: TestClient) -> None:
    job_id = client.post("/api/tools/no_upload/make").json()["job_id"]
    _call(client, "discard", job_id)
    assert _store(client).get(job_id) is not None
    assert _store(client).job_dir(job_id).exists()


def test_discard_of_an_unknown_job_is_a_no_op(client: TestClient) -> None:
    _call(client, "discard", "unknown-job-id")


def test_create_with_a_length_is_limited_like_an_upload(client: TestClient) -> None:
    ok = client.post("/api/tools/no_upload/sized", params={"length": 1024**3})
    assert ok.status_code == 201
    assert _store(client).get(ok.json()["job_id"]).upload_length == 1024**3  # type: ignore[union-attr]

    too_large = client.post("/api/tools/no_upload/sized", params={"length": 1024**3 + 1})
    assert too_large.status_code == 413
    assert too_large.json()["error"]["code"] == "file_too_large"

    negative = client.post("/api/tools/no_upload/sized", params={"length": -1})
    assert negative.status_code == 422
    assert negative.json()["error"]["code"] == "invalid_request"
