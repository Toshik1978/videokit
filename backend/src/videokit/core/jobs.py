"""Job model, state machine and SQLite-backed job store (see data-model.md)."""

import contextlib
import json
import re
import secrets
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from videokit.core import db

RESTART_FAILURE_REASON = "Server restarted, please upload again"
SOURCE_FILE = "source.bin"  # the uploaded video inside a job directory
_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


class JobState(StrEnum):
    UPLOADING = "uploading"
    QUEUED = "queued"
    EXTRACTING = "extracting"
    TRANSCRIBING = "transcribing"
    GENERATING = "generating"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


S = JobState
TRANSITIONS: dict[JobState, set[JobState]] = {
    S.UPLOADING: {S.QUEUED, S.CANCELLED, S.FAILED},
    S.QUEUED: {S.EXTRACTING, S.CANCELLED, S.FAILED},
    S.EXTRACTING: {S.TRANSCRIBING, S.CANCELLED, S.FAILED},
    S.TRANSCRIBING: {S.GENERATING, S.CANCELLED, S.FAILED},
    S.GENERATING: {S.DONE, S.CANCELLED, S.FAILED},
    S.DONE: set(),
    S.FAILED: set(),
    S.CANCELLED: set(),
}
RUNNING_STATES = (S.QUEUED, S.EXTRACTING, S.TRANSCRIBING, S.GENERATING)


class InvalidTransition(ValueError):
    """Raised when a state change is not in the state machine."""


class JobNotFound(KeyError):
    """Raised when a job id does not exist."""


@dataclass(frozen=True)
class Job:
    id: str
    tool_id: str
    state: JobState
    failure_reason: str | None
    cancel_requested: bool
    upload_length: int
    upload_offset: int
    created_at: float
    queued_at: float | None
    updated_at: float
    expires_at: float
    duration_s: float | None
    language: str | None
    no_speech: bool
    providers: dict[str, str]
    usage: dict[str, Any]


def _job_from_row(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"],
        tool_id=row["tool_id"],
        state=JobState(row["state"]),
        failure_reason=row["failure_reason"],
        cancel_requested=bool(row["cancel_requested"]),
        upload_length=row["upload_length"],
        upload_offset=row["upload_offset"],
        created_at=row["created_at"],
        queued_at=row["queued_at"],
        updated_at=row["updated_at"],
        expires_at=row["expires_at"],
        duration_s=row["duration_s"],
        language=row["language"],
        no_speech=bool(row["no_speech"]),
        providers=json.loads(row["providers"]),
        usage=json.loads(row["usage"]),
    )


def _empty_usage() -> dict[str, Any]:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "audio_minutes": 0.0,
        "cost_usd": 0.0,
    }


class JobStore:
    """Synchronous job persistence. Each call uses its own connection, so it is thread-safe."""

    def __init__(
        self,
        data_dir: Path,
        ttl_hours: int,
        max_upload_bytes: int,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._data_dir = data_dir
        self._ttl_seconds = ttl_hours * 3600
        self._max_upload_bytes = max_upload_bytes
        self._clock = clock
        self._db_path = data_dir / "videokit.db"
        db.connect(self._db_path).close()  # create schema eagerly

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        """One write transaction (BEGIN IMMEDIATE) on a fresh connection."""
        conn = db.connect(self._db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        finally:
            conn.close()

    @staticmethod
    def _load(conn: sqlite3.Connection, job_id: str) -> Job:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise JobNotFound(job_id)
        return _job_from_row(row)

    def job_dir(self, job_id: str) -> Path:
        """`DATA_DIR/jobs/<id>/` (not created). Rejects ids that could escape the jobs directory."""
        if not _ID_PATTERN.fullmatch(job_id):
            raise ValueError("invalid job id")
        return self._data_dir / "jobs" / job_id

    def create(self, tool_id: str, upload_length: int) -> Job:
        if not 0 < upload_length <= self._max_upload_bytes:
            raise ValueError("upload_length out of range")
        now = self._clock()
        job_id = secrets.token_urlsafe(16)
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO jobs (id, tool_id, state, upload_length, created_at, updated_at, expires_at, usage)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    job_id,
                    tool_id,
                    S.UPLOADING.value,
                    upload_length,
                    now,
                    now,
                    now + self._ttl_seconds,
                    json.dumps(_empty_usage()),
                ),
            )
            return self._load(conn, job_id)

    def get(self, job_id: str) -> Job | None:
        conn = db.connect(self._db_path)
        try:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        finally:
            conn.close()
        return None if row is None else _job_from_row(row)

    def _transition(self, conn: sqlite3.Connection, job: Job, new: JobState, failure_reason: str | None = None) -> None:
        if new not in TRANSITIONS[job.state]:
            raise InvalidTransition(f"{job.state.value} -> {new.value}")
        if job.state is S.UPLOADING and new is S.QUEUED and job.upload_offset != job.upload_length:
            raise InvalidTransition("upload is not complete")
        now = self._clock()
        conn.execute(
            "UPDATE jobs SET state = ?, failure_reason = ?, updated_at = ?,"
            " queued_at = CASE WHEN ? = 'queued' THEN ? ELSE queued_at END WHERE id = ?",
            (new.value, failure_reason, now, new.value, now, job.id),
        )

    def set_state(self, job_id: str, new: JobState, failure_reason: str | None = None) -> Job:
        if (new is S.FAILED) != (failure_reason is not None):
            raise ValueError("failure_reason must be given if and only if the new state is failed")
        with self._tx() as conn:
            self._transition(conn, self._load(conn, job_id), new, failure_reason)
            return self._load(conn, job_id)

    def set_upload_offset(self, job_id: str, offset: int) -> Job:
        """Record received bytes. Queueing is a separate, explicit `set_state` once the upload is complete."""
        with self._tx() as conn:
            job = self._load(conn, job_id)
            if job.state is not S.UPLOADING:
                raise InvalidTransition(f"cannot upload in state {job.state.value}")
            if not 0 <= offset <= job.upload_length:
                raise ValueError("upload_offset out of range")
            conn.execute(
                "UPDATE jobs SET upload_offset = ?, updated_at = ? WHERE id = ?", (offset, self._clock(), job_id)
            )
            return self._load(conn, job_id)

    def request_cancel(self, job_id: str) -> Job:
        with self._tx() as conn:
            self._load(conn, job_id)
            conn.execute("UPDATE jobs SET cancel_requested = 1, updated_at = ? WHERE id = ?", (self._clock(), job_id))
            return self._load(conn, job_id)

    def cancel(self, job_id: str, *, active: bool = False) -> Job:
        """Cancel a job (FR-037): `uploading` or idle `queued` jobs become `cancelled` at once; a running job, or
        a `queued` one the worker is already starting (`active`), gets `cancel_requested` for the next stage
        boundary. Raises `InvalidTransition` for terminal jobs and `JobNotFound` for unknown ones."""
        with self._tx() as conn:
            job = self._load(conn, job_id)
            if not TRANSITIONS[job.state]:
                raise InvalidTransition(f"cannot cancel a {job.state.value} job")
            if job.state is S.UPLOADING or (job.state is S.QUEUED and not active):
                self._transition(conn, job, S.CANCELLED)
            else:
                conn.execute(
                    "UPDATE jobs SET cancel_requested = 1, updated_at = ? WHERE id = ?", (self._clock(), job_id)
                )
            return self._load(conn, job_id)

    def next_queued(self) -> str | None:
        """Id of the job that has waited longest in the queue (FIFO by `queued_at`), or None."""
        conn = db.connect(self._db_path)
        try:
            row = conn.execute(
                "SELECT id FROM jobs WHERE state = 'queued' ORDER BY queued_at, rowid LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
        return None if row is None else str(row["id"])

    def queue_position(self, job_id: str) -> int:
        """1 + number of queued jobs ahead of this one; 0 when the job is not queued."""
        conn = db.connect(self._db_path)
        try:
            row = conn.execute("SELECT rowid, state, queued_at FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise JobNotFound(job_id)
            if row["state"] != S.QUEUED.value:
                return 0
            ahead = conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE state = 'queued' AND (queued_at < ? OR (queued_at = ? AND rowid < ?))",
                (row["queued_at"], row["queued_at"], row["rowid"]),
            ).fetchone()[0]
        finally:
            conn.close()
        return int(ahead) + 1

    def queue_length(self) -> int:
        """Number of jobs currently waiting in the queue."""
        conn = db.connect(self._db_path)
        try:
            return int(conn.execute("SELECT COUNT(*) FROM jobs WHERE state = 'queued'").fetchone()[0])
        finally:
            conn.close()

    def add_usage(
        self,
        job_id: str,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        audio_minutes: float = 0.0,
        cost_usd: float | None = 0.0,
    ) -> Job:
        """Atomically add to the job's usage totals. An unknown cost (None) makes the total cost unknown."""
        with self._tx() as conn:
            usage = self._load(conn, job_id).usage
            usage["input_tokens"] += input_tokens
            usage["output_tokens"] += output_tokens
            usage["cache_read_tokens"] += cache_read_tokens
            usage["cache_write_tokens"] += cache_write_tokens
            usage["audio_minutes"] += audio_minutes
            usage["cost_usd"] = None if usage["cost_usd"] is None or cost_usd is None else usage["cost_usd"] + cost_usd
            conn.execute(
                "UPDATE jobs SET usage = ?, updated_at = ? WHERE id = ?", (json.dumps(usage), self._clock(), job_id)
            )
            return self._load(conn, job_id)

    def update_info(
        self,
        job_id: str,
        *,
        duration_s: float | None = None,
        language: str | None = None,
        no_speech: bool | None = None,
        providers: dict[str, str] | None = None,
    ) -> Job:
        """Set the given (non-None) media/provider fields."""
        with self._tx() as conn:
            job = self._load(conn, job_id)
            conn.execute(
                "UPDATE jobs SET duration_s = ?, language = ?, no_speech = ?, providers = ?, updated_at = ?"
                " WHERE id = ?",
                (
                    job.duration_s if duration_s is None else duration_s,
                    job.language if language is None else language,
                    int(job.no_speech if no_speech is None else no_speech),
                    json.dumps(job.providers if providers is None else providers),
                    self._clock(),
                    job_id,
                ),
            )
            return self._load(conn, job_id)

    def list_expired(self, now: float) -> list[Job]:
        """Jobs with `expires_at < now` that are not waiting or running (those are never purged)."""
        conn = db.connect(self._db_path)
        try:
            rows = conn.execute(
                f"SELECT * FROM jobs WHERE expires_at < ? AND state NOT IN ({','.join('?' * len(RUNNING_STATES))})",
                [now, *(s.value for s in RUNNING_STATES)],
            ).fetchall()
        finally:
            conn.close()
        return [_job_from_row(row) for row in rows]

    def delete(self, job_id: str) -> bool:
        """Delete the row of a job that is not waiting or running; returns whether a row was removed."""
        with self._tx() as conn:
            cur = conn.execute(
                f"DELETE FROM jobs WHERE id = ? AND state NOT IN ({','.join('?' * len(RUNNING_STATES))})",
                [job_id, *(s.value for s in RUNNING_STATES)],
            )
            return cur.rowcount > 0

    def recover_after_restart(self) -> int:
        """Fail every job that was waiting or running when the server stopped and delete its source video
        (FR-019, FR-014); returns how many."""
        with self._tx() as conn:
            rows = conn.execute(
                f"SELECT * FROM jobs WHERE state IN ({','.join('?' * len(RUNNING_STATES))})",
                [s.value for s in RUNNING_STATES],
            ).fetchall()
            for row in rows:
                self._transition(conn, _job_from_row(row), S.FAILED, RESTART_FAILURE_REASON)
        for row in rows:
            with contextlib.suppress(OSError, ValueError):  # never block startup on a leftover file
                (self.job_dir(row["id"]) / SOURCE_FILE).unlink(missing_ok=True)
        return len(rows)
