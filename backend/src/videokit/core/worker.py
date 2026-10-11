"""In-process job worker: upload completion, the FIFO queue, tool runs and cancellation (FR-005, FR-014, FR-037).

One asyncio task runs queued jobs one at a time in `queued_at` order. It sleeps on an event that is set whenever a
job is queued (with a slow fallback poll). Blocking work runs in threads via `asyncio.to_thread`.
"""

import asyncio
import contextlib
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from videokit.core.config import Settings
from videokit.core.errors import AppError, logger
from videokit.core.jobs import InvalidTransition, Job, JobNotFound, JobState, JobStore
from videokit.core.llm.base import Usage
from videokit.core.llm.chain import LLMChain
from videokit.core.media import SOURCE_NAME, MediaService, probe
from videokit.core.registry import JobCancelled, Tool
from videokit.core.transcribe.base import AllProvidersFailed
from videokit.core.transcribe.chain import TranscriberChain

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


@dataclass
class Context:
    """`CoreContext` implementation handed to `Tool.run`."""

    media: MediaService
    transcriber: TranscriberChain
    llm: LLMChain
    settings: Settings


class WorkerJobHandle:
    """`JobHandle` implementation. Every call is a short SQLite transaction, so it is safe from any thread."""

    def __init__(self, store: JobStore, job_id: str) -> None:
        self._store = store
        self.id = job_id
        self.dir = store.job_dir(job_id)

    def set_state(self, state: str, **fields: Any) -> None:
        if fields:
            self._store.update_info(self.id, **fields)
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
            cost_usd=cost_usd,
        )

    def check_cancelled(self) -> None:
        job = self._store.get(self.id)
        if job is None or job.cancel_requested or job.state is JobState.CANCELLED:
            raise JobCancelled(self.id)


class StoreJobAccess:
    """`JobAccess` implementation for tool routes."""

    def __init__(self, store: JobStore, ctx: Context) -> None:
        self._store = store
        self._ctx = ctx

    @property
    def ctx(self) -> Context:
        return self._ctx

    def get(self, job_id: str) -> Job | None:
        return self._store.get(job_id)

    def handle(self, job_id: str) -> WorkerJobHandle:
        return WorkerJobHandle(self._store, job_id)


class Worker:
    def __init__(self, store: JobStore, tools: Mapping[str, Tool], ctx: Context) -> None:
        self._store = store
        self._tools = tools
        self._ctx = ctx
        self._wake = asyncio.Event()
        # Serializes "which job is the worker starting" with cancel decisions (see `cancel`).
        self._lock = asyncio.Lock()
        self._current: str | None = None
        self._task: asyncio.Task[None] | None = None

    # --- lifecycle ------------------------------------------------------------------------------------------------

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    def notify(self) -> None:
        """Wake the worker: a job was queued. Call from the event loop thread."""
        self._wake.set()

    async def _loop(self) -> None:
        while True:
            self._wake.clear()  # before looking, so a job queued meanwhile is not missed
            try:
                job_id = await asyncio.to_thread(self._store.next_queued)
                if job_id is not None:
                    await self._process(job_id)
                    continue
            except Exception as exc:  # never let one bad job stop the worker
                logger.error("Worker error (%s)", type(exc).__name__)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), POLL_INTERVAL_S)

    # --- upload completion ----------------------------------------------------------------------------------------

    async def on_upload_complete(self, job_id: str) -> None:
        """Probe a fully uploaded source: queue it, or fail it (`not_a_video`) and delete it. Idempotent."""
        job = await asyncio.to_thread(self._store.get, job_id)
        if job is None or job.state is not JobState.UPLOADING or job.upload_offset != job.upload_length:
            return
        source = self._store.job_dir(job_id) / SOURCE_NAME
        try:
            info = await asyncio.to_thread(probe, source)
        except AppError as exc:
            await asyncio.to_thread(self._fail, job_id, exc.message)
            return
        try:
            await asyncio.to_thread(self._store.update_info, job_id, duration_s=info.duration_s)
            await asyncio.to_thread(self._store.set_state, job_id, JobState.QUEUED)
        except (InvalidTransition, JobNotFound):  # completed twice, cancelled or purged meanwhile
            return
        self.notify()

    # --- cancellation ---------------------------------------------------------------------------------------------

    async def cancel(self, job_id: str) -> Job:
        """FR-037. Raises `JobNotFound` for unknown jobs and `InvalidTransition` for terminal ones."""
        async with self._lock:
            job = await asyncio.to_thread(self._store.cancel, job_id, active=job_id == self._current)
        if job.state is JobState.CANCELLED:
            await asyncio.to_thread(self._remove_dir, job_id)
        return job

    # --- running jobs ---------------------------------------------------------------------------------------------

    async def _process(self, job_id: str) -> None:
        async with self._lock:
            self._current = job_id
        try:
            job = await asyncio.to_thread(self._store.get, job_id)
            if job is not None and job.state is JobState.QUEUED:
                await self._run(job)
        finally:
            self._current = None

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
            await asyncio.to_thread(self._fail, job.id, f"{exc.stage.capitalize()} failed: all providers failed")
        except AppError as exc:
            await asyncio.to_thread(self._fail_in_stage, job.id, exc.message)
        except Exception as exc:
            # Type only: messages and tracebacks may carry transcript text or request details (FR-016).
            logger.error("Job %s failed with an internal error (%s)", job.id, type(exc).__name__)
            await asyncio.to_thread(self._fail, job.id, INTERNAL_ERROR)

    # --- blocking helpers (run in threads) -------------------------------------------------------------------------

    def _finish(self, job_id: str) -> None:
        job = self._store.get(job_id)
        if job is not None and job.state is not JobState.DONE:
            self._store.set_state(job_id, JobState.DONE)

    def _mark_cancelled(self, job_id: str) -> None:
        try:
            self._store.set_state(job_id, JobState.CANCELLED)
        except (InvalidTransition, JobNotFound):  # already terminal, or purged meanwhile
            logger.warning("Job %s could not be marked cancelled", job_id)
            return
        self._remove_dir(job_id)

    def _fail_in_stage(self, job_id: str, message: str) -> None:
        job = self._store.get(job_id)
        label = None if job is None else _STAGE_LABELS.get(job.state)
        self._fail(job_id, f"{label} failed: {message}" if label else message)

    def _fail(self, job_id: str, reason: str) -> None:
        """Mark the job failed and delete its source video (FR-014)."""
        try:
            self._store.set_state(job_id, JobState.FAILED, reason)
        except (InvalidTransition, JobNotFound):  # already terminal (e.g. cancelled meanwhile) or purged
            return
        (self._store.job_dir(job_id) / SOURCE_NAME).unlink(missing_ok=True)

    def _remove_dir(self, job_id: str) -> None:
        shutil.rmtree(self._store.job_dir(job_id), ignore_errors=True)
