"""Worker, upload completion and cancellation, driven through the HTTP API with a scripted tool."""

import asyncio
import logging
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from pydantic import SecretStr

from fakes_llm import FakeLLM
from fakes_transcribe import FakeTranscriber
from videokit.core import uploads
from videokit.core import worker as worker_module
from videokit.core.config import Settings
from videokit.core.errors import AppError
from videokit.core.jobs import RESTART_FAILURE_REASON, Job, JobState, JobStore
from videokit.core.llm import AnthropicProvider, LLMChain
from videokit.core.media import MediaService
from videokit.core.registry import CoreContext, JobHandle
from videokit.core.transcribe import AllProvidersFailed, LocalWhisperTranscriber, TranscriberChain
from videokit.core.worker import Providers, Worker
from videokit.main import create_app

TUS = {"Tus-Resumable": "1.0.0"}
PATCH_HEADERS = {**TUS, "Content-Type": "application/offset+octet-stream"}
STAGES = ("extracting", "transcribing", "generating")
NOT_A_VIDEO = "The uploaded file is not a playable video."
MakeVideo = Callable[..., Path]


class ScriptedTool:
    """Walks the three stages, checking for cancellation at each boundary.

    Clearing ``gate`` holds every run inside stage ``hold_at`` until it is set again; ``error`` is raised
    inside stage ``fail_at``.
    """

    id = "scripted"
    name = "Scripted"
    description = "A tool driven by the test."
    router = APIRouter()

    def __init__(self) -> None:
        self.gate = threading.Event()
        self.gate.set()
        self.hold_at = "extracting"
        self.fail_at = "transcribing"
        self.error: Exception | None = None
        self.runs: list[str] = []
        self.stages: list[tuple[str, str]] = []
        self.contexts: list[CoreContext] = []
        self.running = 0
        self.max_running = 0

    async def run(self, job: JobHandle, ctx: CoreContext) -> None:
        self.runs.append(job.id)
        self.contexts.append(ctx)
        self.running += 1
        self.max_running = max(self.max_running, self.running)
        try:
            for stage in STAGES:
                job.check_cancelled()
                if stage == "transcribing":
                    job.set_state(stage, language="en")
                else:
                    job.set_state(stage)
                self.stages.append((job.id, stage))
                if stage == self.hold_at:
                    await asyncio.to_thread(self.gate.wait, 10)
                if stage == self.fail_at and self.error is not None:
                    raise self.error
            (job.dir / "result.json").write_text('{"title": "ok"}', encoding="utf-8")
        finally:
            self.running -= 1


@pytest.fixture
def tool() -> ScriptedTool:
    return ScriptedTool()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data", anthropic_api_key=SecretStr("sk-test"), max_upload_gb=1)


@pytest.fixture
def providers() -> Providers:
    return Providers(transcriber=TranscriberChain([FakeTranscriber()]), llm=LLMChain([FakeLLM([])]))


@pytest.fixture
def client(
    tmp_path: Path, settings: Settings, providers: Providers, tool: ScriptedTool, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    # A long fallback poll proves jobs are picked up by the wake-up event, not by polling.
    monkeypatch.setattr(worker_module, "POLL_INTERVAL_S", 60.0)
    app = create_app(settings, providers, tools_package="videokit.tools", static_dir=tmp_path / "static")
    app.state.tools = {tool.id: tool}
    with TestClient(app) as c:
        try:
            yield c
        finally:
            tool.gate.set()


@pytest.fixture
def video(make_video: MakeVideo) -> bytes:
    return make_video().read_bytes()


def _store(client: TestClient) -> JobStore:
    store: JobStore = client.app.state.store  # type: ignore[attr-defined]
    return store


def _worker(client: TestClient) -> Worker:
    worker: Worker = client.app.state.worker  # type: ignore[attr-defined]
    return worker


def _job(client: TestClient, job_id: str) -> Job:
    job = _store(client).get(job_id)
    assert job is not None
    return job


def _wait(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached in time")
        time.sleep(0.01)


def _wait_state(client: TestClient, job_id: str, state: JobState) -> Job:
    _wait(lambda: _job(client, job_id).state is state)
    return _job(client, job_id)


def _create(client: TestClient, size: int) -> str:
    r = client.post("/api/jobs", json={"tool_id": "scripted", "size": size})
    assert r.status_code == 201
    return str(r.json()["job_id"])


def _patch(client: TestClient, job_id: str, data: bytes, offset: int = 0) -> int:
    headers = {**PATCH_HEADERS, "Upload-Offset": str(offset)}
    return client.patch(f"/api/uploads/{job_id}", content=data, headers=headers).status_code


def _upload(client: TestClient, data: bytes) -> str:
    job_id = _create(client, len(data))
    assert _patch(client, job_id, data) == 204
    return job_id


def _cancel(client: TestClient, job_id: str) -> tuple[int, dict[str, Any]]:
    r = client.post(f"/api/jobs/{job_id}/cancel")
    return r.status_code, r.json()


# --- upload completion -------------------------------------------------------------------------------------------


def test_completed_video_is_probed_queued_and_run(
    client: TestClient, tool: ScriptedTool, video: bytes, settings: Settings, providers: Providers
) -> None:
    job_id = _upload(client, video)
    job = _wait_state(client, job_id, JobState.DONE)
    assert job.duration_s == pytest.approx(6.0, abs=0.2)
    assert job.language == "en"  # recorded through JobHandle.set_state fields
    assert tool.runs == [job_id]
    assert [s for j, s in tool.stages if j == job_id] == list(STAGES)
    ctx = tool.contexts[0]
    assert isinstance(ctx.media, MediaService)
    assert ctx.transcriber is providers.transcriber
    assert ctx.llm is providers.llm
    assert ctx.settings is settings
    body = client.get(f"/api/jobs/{job_id}").json()
    assert body["state"] == "done"
    assert body["result"] == {"title": "ok"}


def test_not_a_video_fails_and_deletes_source(client: TestClient, tool: ScriptedTool) -> None:
    job_id = _upload(client, b"definitely not a video\n" * 50)
    job = _wait_state(client, job_id, JobState.FAILED)
    assert (job.failure_reason, job.failure_code) == (NOT_A_VIDEO, "not_a_video")
    assert not (_store(client).job_dir(job_id) / "source.bin").exists()
    assert tool.runs == []
    body = client.get(f"/api/jobs/{job_id}").json()
    assert (body["failure_reason"], body["failure_code"]) == (NOT_A_VIDEO, "not_a_video")


def test_upload_completion_is_idempotent(client: TestClient, tool: ScriptedTool, video: bytes) -> None:
    store, worker = _store(client), _worker(client)

    # Two concurrent completions of one finished upload queue and run it once.
    job = store.create("scripted", len(video))
    store.job_dir(job.id).mkdir(parents=True)
    (store.job_dir(job.id) / "source.bin").write_bytes(video)
    store.set_upload_offset(job.id, len(video))

    async def complete_twice() -> None:
        await asyncio.gather(worker.on_upload_complete(job.id), worker.on_upload_complete(job.id))

    client.portal.call(complete_twice)  # type: ignore[union-attr]
    _wait_state(client, job.id, JobState.DONE)

    # Repeated completions after the fact change nothing.
    bad_id = _upload(client, b"not a video" * 10)
    _wait_state(client, bad_id, JobState.FAILED)
    for job_id in (job.id, bad_id, "unknown-job"):
        client.portal.call(worker.on_upload_complete, job_id)  # type: ignore[union-attr]
    assert _job(client, job.id).state is JobState.DONE
    assert _job(client, bad_id).failure_reason == NOT_A_VIDEO
    assert tool.runs == [job.id]

    # An empty PATCH at offset == length is rejected once the job left `uploading`.
    assert _patch(client, job.id, b"", offset=len(video)) == 404


def test_incomplete_upload_is_not_queued(client: TestClient, video: bytes) -> None:
    job_id = _create(client, len(video))
    assert _patch(client, job_id, video[:100]) == 204
    client.portal.call(_worker(client).on_upload_complete, job_id)  # type: ignore[union-attr]
    assert _job(client, job_id).state is JobState.UPLOADING


# --- queue ------------------------------------------------------------------------------------------------------


def test_jobs_run_one_at_a_time_in_fifo_order(client: TestClient, tool: ScriptedTool, video: bytes) -> None:
    tool.gate.clear()
    first = _upload(client, video)
    _wait_state(client, first, JobState.EXTRACTING)
    second, third = _upload(client, video), _upload(client, video)
    assert client.get(f"/api/jobs/{second}").json()["queue_position"] == 1
    assert client.get(f"/api/jobs/{third}").json()["queue_position"] == 2
    assert client.get("/api/health").json()["queue_length"] == 2
    assert _job(client, second).duration_s == pytest.approx(6.0, abs=0.2)

    tool.gate.set()
    for job_id in (first, second, third):
        _wait_state(client, job_id, JobState.DONE)
    assert tool.runs == [first, second, third]
    assert tool.max_running == 1


# --- cancellation -----------------------------------------------------------------------------------------------


def test_cancel_uploading_job(client: TestClient, video: bytes) -> None:
    job_id = _create(client, len(video))
    assert _patch(client, job_id, video[:100]) == 204
    status, body = _cancel(client, job_id)
    assert (status, body["state"]) == (202, "cancelled")
    assert not _store(client).job_dir(job_id).exists()
    assert client.head(f"/api/uploads/{job_id}", headers=TUS).status_code == 404
    assert _patch(client, job_id, video[100:], offset=100) == 404
    assert not _store(client).job_dir(job_id).exists()
    assert client.get(f"/api/jobs/{job_id}").json()["state"] == "cancelled"


def _cancel_before_first_call(client: TestClient, monkeypatch: pytest.MonkeyPatch, name: str, job_id: str) -> None:
    """Make the cancel request land right before the first call of `uploads.<name>` (which runs in a thread)."""
    original = getattr(uploads, name)
    calls = 0

    def patched(*args: Any) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            client.portal.call(_worker(client).cancel, job_id)  # type: ignore[union-attr]
        original(*args)

    monkeypatch.setattr(uploads, name, patched)


def test_cancel_while_a_patch_is_streaming(
    client: TestClient, video: bytes, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    job_id = _create(client, len(video))
    _cancel_before_first_call(client, monkeypatch, "_append", job_id)
    assert _patch(client, job_id, video) == 404
    assert _job(client, job_id).state is JobState.CANCELLED
    assert not _store(client).job_dir(job_id).exists()
    assert "Traceback" not in caplog.text
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_cancel_before_a_patch_writes_leaves_no_directory(
    client: TestClient, video: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = _create(client, len(video))
    assert _patch(client, job_id, video[:100]) == 204
    _cancel_before_first_call(client, monkeypatch, "_prepare", job_id)
    assert _patch(client, job_id, video[100:], offset=100) == 404
    assert not _store(client).job_dir(job_id).exists()


def test_cancel_before_an_empty_patch_leaves_no_directory(
    client: TestClient, video: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = _create(client, len(video))
    assert _patch(client, job_id, video[:100]) == 204
    _cancel_before_first_call(client, monkeypatch, "_prepare", job_id)
    assert _patch(client, job_id, b"", offset=100) == 404
    assert not _store(client).job_dir(job_id).exists()


def test_cancel_before_an_oversized_patch_leaves_no_directory(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(uploads, "UPLOAD_CHUNK_BYTES", 4)
    job_id = _create(client, 10)
    _cancel_before_first_call(client, monkeypatch, "_prepare", job_id)

    def body() -> Iterator[bytes]:
        yield b"012"
        yield b"345"

    r = client.patch(f"/api/uploads/{job_id}", content=body(), headers={**PATCH_HEADERS, "Upload-Offset": "0"})
    assert r.status_code == 413
    assert not _store(client).job_dir(job_id).exists()


def test_cancel_queued_job(client: TestClient, tool: ScriptedTool, video: bytes) -> None:
    tool.gate.clear()
    running = _upload(client, video)
    _wait_state(client, running, JobState.EXTRACTING)
    waiting = _upload(client, video)
    status, body = _cancel(client, waiting)
    assert (status, body["state"], body["queue_position"]) == (202, "cancelled", None)
    assert not _store(client).job_dir(waiting).exists()

    tool.gate.set()
    _wait_state(client, running, JobState.DONE)
    assert tool.runs == [running]
    assert _job(client, waiting).state is JobState.CANCELLED


def test_cancel_running_job_stops_at_next_stage_boundary(client: TestClient, tool: ScriptedTool, video: bytes) -> None:
    tool.gate.clear()
    running = _upload(client, video)
    _wait_state(client, running, JobState.EXTRACTING)
    waiting = _upload(client, video)

    status, body = _cancel(client, running)
    assert (status, body["state"]) == (202, "extracting")
    assert _job(client, running).cancel_requested is True
    assert _store(client).job_dir(running).exists()

    tool.gate.set()
    _wait_state(client, running, JobState.CANCELLED)
    assert (running, "transcribing") not in tool.stages
    assert not _store(client).job_dir(running).exists()
    _wait_state(client, waiting, JobState.DONE)  # the next queued job starts


def test_cancel_during_last_stage_is_honored_when_the_tool_returns(
    client: TestClient, tool: ScriptedTool, video: bytes
) -> None:
    tool.hold_at = "generating"
    tool.gate.clear()
    job_id = _upload(client, video)
    _wait_state(client, job_id, JobState.GENERATING)
    status, body = _cancel(client, job_id)
    assert (status, body["state"]) == (202, "generating")
    tool.gate.set()
    _wait_state(client, job_id, JobState.CANCELLED)
    assert not _store(client).job_dir(job_id).exists()


def test_cancel_terminal_jobs_is_rejected(client: TestClient, video: bytes) -> None:
    done = _upload(client, video)
    _wait_state(client, done, JobState.DONE)
    failed = _upload(client, b"not a video" * 10)
    _wait_state(client, failed, JobState.FAILED)
    cancelled = _create(client, 10)
    assert _cancel(client, cancelled)[0] == 202

    for job_id in (done, failed, cancelled):
        status, body = _cancel(client, job_id)
        assert status == 409
        assert body["error"]["code"] == "not_cancellable"
    assert _job(client, done).state is JobState.DONE
    assert _store(client).job_dir(done).exists()


def test_cancel_unknown_job(client: TestClient) -> None:
    status, body = _cancel(client, "no-such-job")
    assert (status, body["error"]["code"]) == (404, "job_not_found")


# --- failures ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("stage", "error", "reason", "code"),
    [
        (
            "transcribing",
            AllProvidersFailed("transcription"),
            "Transcription failed: all providers failed",
            "transcription_failed",
        ),
        (
            "generating",
            AllProvidersFailed("generation"),
            "Generation failed: all providers failed",
            "generation_failed",
        ),
        (
            "extracting",
            AppError("extraction_failed", "The video could not be processed.", 500),
            "Extraction failed: The video could not be processed.",
            "extraction_failed",
        ),
        (
            "generating",
            AppError("generation_invalid", "the output still broke the YouTube rules", 502),
            "Generation failed: the output still broke the YouTube rules",
            "generation_invalid",
        ),
        ("generating", RuntimeError("transcript: the secret words"), "Internal error", "internal_error"),
    ],
)
def test_failures_map_to_user_safe_reasons(
    client: TestClient,
    tool: ScriptedTool,
    video: bytes,
    caplog: pytest.LogCaptureFixture,
    stage: str,
    error: Exception,
    reason: str,
    code: str,
) -> None:
    caplog.set_level(logging.DEBUG)
    tool.fail_at, tool.error = stage, error
    job_id = _upload(client, video)
    job = _wait_state(client, job_id, JobState.FAILED)
    assert (job.failure_reason, job.failure_code) == (reason, code)
    assert client.get(f"/api/jobs/{job_id}").json()["failure_code"] == code
    assert not (_store(client).job_dir(job_id) / "source.bin").exists()
    assert "secret words" not in caplog.text
    assert "Traceback" not in caplog.text

    tool.error = None  # the worker moves on to the next job
    _wait_state(client, _upload(client, video), JobState.DONE)


def test_unknown_tool_fails_with_internal_error(client: TestClient, video: bytes) -> None:
    store = _store(client)
    job = store.create("vanished_tool", len(video))
    store.set_upload_offset(job.id, len(video))
    store.set_state(job.id, JobState.QUEUED)
    client.portal.call(_worker(client).notify)  # type: ignore[union-attr]
    failed = _wait_state(client, job.id, JobState.FAILED)
    assert (failed.failure_reason, failed.failure_code) == ("Internal error", "internal_error")


# --- lifespan ---------------------------------------------------------------------------------------------------


def test_restart_recovery_fails_interrupted_jobs(
    tmp_path: Path, settings: Settings, providers: Providers, tool: ScriptedTool
) -> None:
    settings.data_dir.mkdir(parents=True)
    store = JobStore(settings.data_dir, settings.job_ttl_hours, settings.max_upload_bytes)
    job = store.create("scripted", 10)
    store.set_upload_offset(job.id, 10)
    store.set_state(job.id, JobState.QUEUED)
    source = store.job_dir(job.id) / "source.bin"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"0123456789")

    app = create_app(settings, providers, tools_package="videokit.tools", static_dir=tmp_path / "static")
    app.state.tools = {tool.id: tool}
    with TestClient(app) as c:
        body = c.get(f"/api/jobs/{job.id}").json()
    assert (body["failure_reason"], body["failure_code"]) == (RESTART_FAILURE_REASON, "server_restarted")
    assert tool.runs == []
    assert not source.exists()  # FR-014: a failed job's source is deleted


def test_marking_a_vanished_job_cancelled_is_harmless(client: TestClient) -> None:
    _worker(client)._mark_cancelled("vanished-job")  # e.g. purged meanwhile: no exception escapes


def test_default_providers_are_built_from_settings_without_loading_anything(tmp_path: Path, settings: Settings) -> None:
    app = create_app(settings, tools_package="videokit.tools", static_dir=tmp_path / "static")
    with TestClient(app):
        providers: Providers = app.state.providers
    [transcriber] = providers.transcriber.providers
    [llm] = providers.llm.providers
    assert isinstance(transcriber, LocalWhisperTranscriber)
    assert transcriber._model is None  # no Whisper model loaded at startup
    assert isinstance(llm, AnthropicProvider)
    assert llm._client is None  # no SDK client (and no network) at startup
