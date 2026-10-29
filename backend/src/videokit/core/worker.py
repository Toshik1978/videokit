"""In-process job worker: upload completion, the FIFO queues, tool runs and cancellation (FR-005, FR-014, FR-018,
FR-037).

Each registered tool has its own lane of `tool.concurrency` slots: each slot is an asyncio task that claims the
oldest queued job of its tool that no slot is running yet (`queued_at` order) and runs it, so jobs of different
tools run side by side and a tool never runs more jobs at once than its concurrency. Each slot sleeps on its own
event, set whenever a job is queued (with a slow fallback poll). Blocking work runs in threads via
`asyncio.to_thread`.

A tool with `resume_after_restart` keeps its mid-run jobs across a restart (see `JobStore.recover_after_restart`):
its lane runs those again first, then takes queued jobs; the tool decides in `run` how to carry on.
"""

import asyncio
import contextlib
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from videokit.core.config import Settings
from videokit.core.errors import AppError, logger
from videokit.core.jobs import (
    INTERNAL_ERROR_CODE,
    RUNNING_STATES,
    InvalidTransition,
    Job,
    JobNotFound,
    JobState,
    JobStore,
)
from videokit.core.llm.base import Usage
from videokit.core.llm.chain import LLMChain
from videokit.core.media import SOURCE_NAME, MediaService
from videokit.core.registry import JobCancelled, Tool
from videokit.core.transcribe.base import AllProvidersFailed
from videokit.core.transcribe.chain import TranscriberChain
from videokit.core.videogen import VideoChain

POLL_INTERVAL_S = 5.0  # fallback only: queueing a job wakes the worker immediately
INTERNAL_ERROR = "Internal error"
_STAGE_LABELS = {
    JobState.EXTRACTING: "Extraction",
    JobState.TRANSCRIBING: "Transcription",
    JobState.GENERATING: "Generation",
}


@dataclass(frozen=True)
class Providers:
    """The configured provider chains (built from settings, or injected by tests)."""

    transcriber: TranscriberChain
    llm: LLMChain
    video: VideoChain | None = None  # None when VIDEO_PROVIDERS is empty


@dataclass
class Context:
    """`CoreContext` implementation handed to `Tool.run`."""

    media: MediaService
    transcriber: TranscriberChain
    llm: LLMChain
    settings: Settings
    video: VideoChain | None = None


class WorkerJobHandle:
    """`JobHandle` implementation. Every call is a short SQLite transaction, so it is safe from any thread."""

    def __init__(self, store: JobStore, job_id: str) -> None:
        self._store = store
        self.id = job_id
        self.dir = store.job_dir(job_id)

    @property
    def source_name(self) -> str | None:
        job = self._store.get(self.id)
        if job is None:
            raise JobNotFound(self.id)
        return job.source_name

    def set_state(self, state: str, **fields: Any) -> None:
        if fields:
            self._store.update_info(self.id, **fields)
        job = self._store.get(self.id)
        if job is not None and job.state is JobState(state):
            return  # a run resumed after a restart enters the stage it was interrupted in
        self._store.set_state(self.id, JobState(state))

    def record(self, **fields: Any) -> None:
        self._store.update_info(self.id, **fields)

    def add_usage(self, usage: Usage, cost_usd: float | None) -> None:
        self._store.add_usage(
            self.id,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            audio_minutes=usage.audio_minutes,
            video_output_tokens=usage.video_output_tokens,
            cost_usd=cost_usd,
        )

    def check_cancelled(self) -> None:
        job = self._store.get(self.id)
        if job is None or job.cancel_requested or job.state is JobState.CANCELLED:
            raise JobCancelled(self.id)


class StoreJobAccess:
    """`JobAccess` implementation for tool routes."""

    def __init__(self, store: JobStore, ctx: Context, worker: Worker) -> None:
        self._store = store
        self._ctx = ctx
        self._worker = worker

    @property
    def ctx(self) -> Context:
        return self._ctx

    def get(self, job_id: str) -> Job | None:
        """The job, or None if it does not exist or has expired (FR-015)."""
        job = self._store.get(job_id)
        return None if job is None or self._store.is_expired(job) else job

    def handle(self, job_id: str) -> WorkerJobHandle:
        return WorkerJobHandle(self._store, job_id)

    def create(self, tool_id: str, *, upload_length: int = 0, source_name: str | None = None) -> Job:
        if upload_length < 0:
            raise AppError("invalid_request", "The file size must be a positive number of bytes.", 422)
        if upload_length > self._ctx.settings.max_upload_bytes:
            limit_gb = self._ctx.settings.max_upload_bytes / 1024**3
            raise AppError("file_too_large", f"The file is larger than the {limit_gb:g} GB limit.", 413)
        return self._store.create(tool_id, upload_length, source_name)

    def job_dir(self, job_id: str) -> Path:
        return self._store.job_dir(job_id)

    async def complete(self, job_id: str) -> None:
        await self._worker.complete_inputs(job_id)

    async def discard(self, job_id: str) -> None:
        await asyncio.to_thread(self._discard, job_id)

    def _discard(self, job_id: str) -> None:
        job = self._store.get(job_id)
        if job is None or job.state is not JobState.UPLOADING:
            return
        self._store.delete(job_id)
        shutil.rmtree(self._store.job_dir(job_id), ignore_errors=True)


class Worker:
    def __init__(self, store: JobStore, tools: Mapping[str, Tool], ctx: Context) -> None:
        self._store = store
        self._tools = tools
        self._ctx = ctx
        # One event per lane: a lane clears only its own, so a wake-up is never lost to another lane.
        self._wakes: list[asyncio.Event] = []
        # Serializes "which jobs are the lanes starting" with cancel decisions (see `cancel`).
        self._lock = asyncio.Lock()
        self._current: set[str] = set()
        # Per resumable tool: its jobs found mid-run at startup, still to be run again (taken under the lock).
        self._interrupted: dict[str, list[str]] = {}
        self._tasks: list[asyncio.Task[None]] = []

    # --- lifecycle ------------------------------------------------------------------------------------------------

    def start(self) -> None:
        """Start one lane per registered tool (FR-018), each with `tool.concurrency` slots."""
        for tool_id, tool in self._tools.items():
            if tool.resume_after_restart:
                self._interrupted[tool_id] = self._store.interrupted(tool_id)
            for _ in range(tool.concurrency):
                wake = asyncio.Event()
                self._wakes.append(wake)
                self._tasks.append(asyncio.create_task(self._loop(tool_id, wake)))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._wakes.clear()
        self._interrupted.clear()

    def notify(self) -> None:
        """Wake every slot: a job was queued. Call from the event loop thread."""
        for wake in self._wakes:
            wake.set()

    async def _loop(self, tool_id: str, wake: asyncio.Event) -> None:
        """One slot of a lane: claim the next job, run it, repeat; sleep when the lane has nothing to claim."""
        while True:
            wake.clear()  # before looking, so a job queued meanwhile is not missed
            try:
                job_id = await self._claim(tool_id)
                if job_id is not None:
                    await self._process(job_id)
                    continue
            except Exception as exc:  # never let one bad job stop the slot
                logger.error("Worker error (%s)", type(exc).__name__)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(wake.wait(), POLL_INTERVAL_S)

    async def _claim(self, tool_id: str) -> str | None:
        """The next job of the tool for a slot, now marked as running (in `_current`): first a job interrupted by
        a restart (once each), then the oldest queued one that no slot has claimed.

        Lookup and claim happen under the lock, so two slots never take one job and a cancel sees a job either
        unclaimed (cancelled at once) or active (flagged)."""
        async with self._lock:
            pending = self._interrupted.get(tool_id)
            if pending:
                job_id: str | None = pending.pop(0)
            else:
                job_id = await asyncio.to_thread(self._store.next_queued, tool_id, frozenset(self._current))
            if job_id is not None:
                self._current.add(job_id)
            return job_id

    # --- upload completion ----------------------------------------------------------------------------------------

    async def complete_inputs(self, job_id: str) -> None:
        """All inputs of an `uploading` job are in: ask its tool to accept them, then queue the job, or fail it
        with the tool's error and delete its source. The one inputs-complete path (tus, startup re-fire, tools
        without an upload). Idempotent."""
        job = await asyncio.to_thread(self._store.get, job_id)
        if job is None or job.state is not JobState.UPLOADING or job.upload_offset != job.upload_length:
            return
        if self._store.is_expired(job):  # FR-015: leave it for the purge, which deletes it
            return
        tool = self._tools.get(job.tool_id)
        if tool is None:  # e.g. a tool removed across a restart: no lane would ever run it
            await asyncio.to_thread(self._fail, job_id, INTERNAL_ERROR, INTERNAL_ERROR_CODE)
            return
        try:
            await asyncio.to_thread(tool.accept, WorkerJobHandle(self._store, job_id), self._ctx)
        except AppError as exc:
            await asyncio.to_thread(self._fail, job_id, exc.message, exc.code)
            return
        try:
            await asyncio.to_thread(self._store.set_state, job_id, JobState.QUEUED)
        except InvalidTransition, JobNotFound:  # completed twice, cancelled or purged meanwhile
            return
        self.notify()

    # --- cancellation ---------------------------------------------------------------------------------------------

    async def cancel(self, job_id: str) -> Job:
        """FR-037. Raises `JobNotFound` for unknown jobs and `InvalidTransition` for terminal ones."""
        async with self._lock:
            job = await asyncio.to_thread(self._store.cancel, job_id, active=job_id in self._current)
        if job.state is JobState.CANCELLED:
            await asyncio.to_thread(self._remove_dir, job_id)
        return job

    # --- running jobs ---------------------------------------------------------------------------------------------

    async def _process(self, job_id: str) -> None:
        """Run a claimed job (see `_claim`); the claim is released when it ends, however it ends."""
        try:
            job = await asyncio.to_thread(self._store.get, job_id)
            if job is not None and job.state in RUNNING_STATES:  # queued, or interrupted by a restart
                await self._run(job)
        finally:
            self._current.discard(job_id)

    async def _run(self, job: Job) -> None:
        handle = WorkerJobHandle(self._store, job.id)
        try:
            tool = self._tools[job.tool_id]
            await asyncio.to_thread(handle.check_cancelled)
            await tool.run(handle, self._ctx)
            await asyncio.to_thread(handle.check_cancelled)  # the boundary after the last stage
            await asyncio.to_thread(self._finish, job.id)
        except JobCancelled:
            await asyncio.to_thread(self._mark_cancelled, job.id)
        except AllProvidersFailed as exc:
            reason = f"{exc.stage.capitalize()} failed: all providers failed"
            await asyncio.to_thread(self._fail, job.id, reason, f"{exc.stage}_failed")
        except AppError as exc:
            await asyncio.to_thread(self._fail_in_stage, job.id, exc.message, exc.code)
        except Exception as exc:
            # Type only: messages and tracebacks may carry transcript text or request details (FR-016).
            logger.error("Job %s failed with an internal error (%s)", job.id, type(exc).__name__)
            await asyncio.to_thread(self._fail, job.id, INTERNAL_ERROR, INTERNAL_ERROR_CODE)

    # --- blocking helpers (run in threads) -------------------------------------------------------------------------

    def _finish(self, job_id: str) -> None:
        job = self._store.get(job_id)
        if job is not None and job.state is not JobState.DONE:
            self._store.set_state(job_id, JobState.DONE)

    def _mark_cancelled(self, job_id: str) -> None:
        try:
            self._store.set_state(job_id, JobState.CANCELLED)
        except InvalidTransition, JobNotFound:  # already terminal, or purged meanwhile
            logger.warning("Job %s could not be marked cancelled", job_id)
            return
        self._remove_dir(job_id)

    def _fail_in_stage(self, job_id: str, message: str, code: str) -> None:
        job = self._store.get(job_id)
        label = None if job is None else _STAGE_LABELS.get(job.state)
        self._fail(job_id, f"{label} failed: {message}" if label else message, code)

    def _fail(self, job_id: str, reason: str, code: str) -> None:
        """Mark the job failed with a user-safe reason and catalog code; delete its source video (FR-014)."""
        try:
            self._store.set_state(job_id, JobState.FAILED, reason, code)
        except InvalidTransition, JobNotFound:  # already terminal (e.g. cancelled meanwhile) or purged
            return
        (self._store.job_dir(job_id) / SOURCE_NAME).unlink(missing_ok=True)

    def _remove_dir(self, job_id: str) -> None:
        shutil.rmtree(self._store.job_dir(job_id), ignore_errors=True)
