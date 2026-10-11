import sqlite3
import threading
from itertools import count
from pathlib import Path

import pytest

from videokit.core import db
from videokit.core.jobs import (
    TRANSITIONS,
    InvalidTransition,
    Job,
    JobNotFound,
    JobState,
    JobStore,
)

RESTART_MESSAGE = "Server restarted, please upload again"


class Clock:
    def __init__(self) -> None:
        self._ticks = count(1000)

    def __call__(self) -> float:
        return float(next(self._ticks))


@pytest.fixture
def store(tmp_path: Path) -> JobStore:
    return JobStore(tmp_path, ttl_hours=24, max_upload_bytes=1000, clock=Clock())


def advance(store: JobStore, job_id: str, *states: JobState) -> None:
    for state in states:
        store.set_state(job_id, state)


def queue(store: JobStore, length: int = 10) -> Job:
    job = store.create("youtube_prep", length)
    store.set_upload_offset(job.id, length)
    return store.set_state(job.id, JobState.QUEUED)


def test_wal_mode_and_schema(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "x" / "videokit.db")
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    finally:
        conn.close()
    assert {
        "id", "tool_id", "state", "failure_reason", "cancel_requested", "upload_length", "upload_offset",
        "created_at", "queued_at", "updated_at", "expires_at", "duration_s", "language", "no_speech",
        "providers", "usage", "failure_code",
    } <= cols  # fmt: skip


def test_connect_adds_failure_code_to_an_existing_database(tmp_path: Path) -> None:
    path = tmp_path / "videokit.db"
    old = sqlite3.connect(path)
    old.execute(
        "CREATE TABLE jobs (id TEXT PRIMARY KEY, tool_id TEXT NOT NULL, state TEXT NOT NULL, failure_reason TEXT,"
        " cancel_requested INTEGER NOT NULL DEFAULT 0, upload_length INTEGER NOT NULL,"
        " upload_offset INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, queued_at REAL,"
        " updated_at REAL NOT NULL, expires_at REAL NOT NULL, duration_s REAL, language TEXT,"
        " no_speech INTEGER NOT NULL DEFAULT 0, providers TEXT NOT NULL DEFAULT '{}', usage TEXT NOT NULL)"
    )
    old.execute(
        "INSERT INTO jobs (id, tool_id, state, failure_reason, upload_length, created_at, updated_at, expires_at,"
        " usage) VALUES ('old', 't', 'failed', 'Internal error', 10, 1, 1, 2, '{}')"
    )
    old.commit()
    old.close()

    store = JobStore(tmp_path, ttl_hours=24, max_upload_bytes=1000)
    got = store.get("old")
    assert got is not None
    assert (got.failure_reason, got.failure_code) == ("Internal error", None)
    db.connect(path).close()  # migrating twice is a no-op


def test_create_defaults(store: JobStore) -> None:
    job = store.create("youtube_prep", 500)
    assert len(job.id) >= 22  # token_urlsafe(16)
    assert job.state is JobState.UPLOADING
    assert job.tool_id == "youtube_prep"
    assert job.upload_length == 500
    assert job.upload_offset == 0
    assert job.failure_reason is None
    assert job.failure_code is None
    assert job.cancel_requested is False
    assert job.queued_at is None
    assert job.expires_at == job.created_at + 24 * 3600
    assert job.no_speech is False
    assert job.providers == {}
    assert job.usage == {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "audio_minutes": 0.0,
        "cost_usd": 0.0,
    }
    assert store.get(job.id) == job


def test_ids_are_unique(store: JobStore) -> None:
    assert len({store.create("t", 1).id for _ in range(20)}) == 20


def test_create_rejects_bad_length(store: JobStore) -> None:
    with pytest.raises(ValueError):
        store.create("t", 1001)
    with pytest.raises(ValueError):
        store.create("t", 0)


def test_get_missing_is_none_and_set_state_missing_raises(store: JobStore) -> None:
    assert store.get("nope") is None
    with pytest.raises(JobNotFound):
        store.set_state("nope", JobState.QUEUED)


def test_upload_offset_bounds_does_not_queue(store: JobStore) -> None:
    job = store.create("t", 100)
    partial = store.set_upload_offset(job.id, 40)
    assert (partial.upload_offset, partial.state) == (40, JobState.UPLOADING)
    with pytest.raises(ValueError):
        store.set_upload_offset(job.id, 101)
    with pytest.raises(ValueError):
        store.set_upload_offset(job.id, -1)
    full = store.set_upload_offset(job.id, 100)
    assert (full.state, full.queued_at) == (JobState.UPLOADING, None)
    queued = store.set_state(job.id, JobState.QUEUED)
    assert queued.state is JobState.QUEUED
    assert queued.queued_at is not None


def test_queueing_requires_complete_upload(store: JobStore) -> None:
    job = store.create("t", 100)
    store.set_upload_offset(job.id, 99)
    with pytest.raises(InvalidTransition):
        store.set_state(job.id, JobState.QUEUED)
    current = store.get(job.id)
    assert current is not None
    assert current.state is JobState.UPLOADING


def test_completed_upload_can_fail_probing(store: JobStore) -> None:
    job = store.create("t", 10)
    store.set_upload_offset(job.id, 10)
    failed = store.set_state(job.id, JobState.FAILED, failure_reason="Not a video", failure_code="not_a_video")
    assert (failed.state, failed.failure_reason, failed.failure_code) == (JobState.FAILED, "Not a video", "not_a_video")


def test_upload_offset_requires_uploading_state(store: JobStore) -> None:
    job = queue(store)
    with pytest.raises(InvalidTransition):
        store.set_upload_offset(job.id, 5)


def test_full_happy_path(store: JobStore) -> None:
    job = queue(store)
    advance(store, job.id, JobState.EXTRACTING, JobState.TRANSCRIBING, JobState.GENERATING, JobState.DONE)
    final = store.get(job.id)
    assert final is not None
    assert final.state is JobState.DONE
    assert final.updated_at > final.created_at


def test_transition_table_matches_state_machine() -> None:
    S = JobState
    assert TRANSITIONS[S.UPLOADING] == {S.QUEUED, S.CANCELLED, S.FAILED}
    assert TRANSITIONS[S.QUEUED] == {S.EXTRACTING, S.CANCELLED, S.FAILED}
    assert TRANSITIONS[S.EXTRACTING] == {S.TRANSCRIBING, S.CANCELLED, S.FAILED}
    assert TRANSITIONS[S.TRANSCRIBING] == {S.GENERATING, S.CANCELLED, S.FAILED}
    assert TRANSITIONS[S.GENERATING] == {S.DONE, S.CANCELLED, S.FAILED}
    for terminal in (S.DONE, S.FAILED, S.CANCELLED):
        assert TRANSITIONS[terminal] == set()


@pytest.mark.parametrize(
    ("start", "target"),
    [
        (JobState.UPLOADING, JobState.EXTRACTING),
        (JobState.UPLOADING, JobState.DONE),
        (JobState.QUEUED, JobState.DONE),
        (JobState.EXTRACTING, JobState.GENERATING),
        (JobState.DONE, JobState.QUEUED),
        (JobState.FAILED, JobState.QUEUED),
        (JobState.CANCELLED, JobState.EXTRACTING),
    ],
)
def test_disallowed_transitions_raise(store: JobStore, start: JobState, target: JobState) -> None:
    job = store.create("t", 10)
    store.set_upload_offset(job.id, 10)
    path = {
        JobState.UPLOADING: [],
        JobState.QUEUED: [JobState.QUEUED],
        JobState.EXTRACTING: [JobState.QUEUED, JobState.EXTRACTING],
        JobState.DONE: [
            JobState.QUEUED,
            JobState.EXTRACTING,
            JobState.TRANSCRIBING,
            JobState.GENERATING,
            JobState.DONE,
        ],
        JobState.FAILED: [JobState.QUEUED, JobState.FAILED],
        JobState.CANCELLED: [JobState.CANCELLED],
    }[start]
    for step in path:
        store.set_state(job.id, step, failure_reason="boom" if step is JobState.FAILED else None)
    with pytest.raises(InvalidTransition):
        store.set_state(job.id, target)
    current = store.get(job.id)
    assert current is not None
    assert current.state is start


def test_failed_requires_reason_and_only_failed_has_one(store: JobStore) -> None:
    job = queue(store)
    with pytest.raises(ValueError):
        store.set_state(job.id, JobState.FAILED)
    with pytest.raises(ValueError):
        store.set_state(job.id, JobState.EXTRACTING, failure_reason="oops")
    with pytest.raises(ValueError):
        store.set_state(job.id, JobState.EXTRACTING, failure_code="internal_error")
    assert store.get(job.id).failure_code is None  # type: ignore[union-attr]
    failed = store.set_state(
        job.id, JobState.FAILED, failure_reason="Transcription failed", failure_code="transcription_failed"
    )
    assert (failed.failure_reason, failed.failure_code) == ("Transcription failed", "transcription_failed")


def test_failure_without_a_code_is_an_internal_error(store: JobStore) -> None:
    job = queue(store)
    assert store.set_state(job.id, JobState.FAILED, "boom").failure_code == "internal_error"


def test_cancel_from_uploading_and_queued(store: JobStore) -> None:
    uploading = store.create("t", 10)
    assert store.set_state(uploading.id, JobState.CANCELLED).state is JobState.CANCELLED
    queued = queue(store)
    assert store.set_state(queued.id, JobState.CANCELLED).state is JobState.CANCELLED


def test_request_cancel_sets_flag(store: JobStore) -> None:
    job = queue(store)
    assert store.request_cancel(job.id).cancel_requested is True
    with pytest.raises(JobNotFound):
        store.request_cancel("nope")


def test_next_queued_is_fifo_by_queued_at(store: JobStore) -> None:
    assert store.next_queued() is None
    store.create("t", 10)  # still uploading: never picked
    first, second = queue(store), queue(store)
    assert store.next_queued() == first.id
    store.set_state(first.id, JobState.EXTRACTING)
    assert store.next_queued() == second.id


def test_cancel_uploading_or_idle_queued_is_immediate(store: JobStore) -> None:
    uploading = store.create("t", 10)
    assert store.cancel(uploading.id).state is JobState.CANCELLED
    queued = queue(store)
    assert store.cancel(queued.id).state is JobState.CANCELLED


def test_cancel_active_or_running_sets_flag(store: JobStore) -> None:
    claimed = queue(store)
    job = store.cancel(claimed.id, active=True)
    assert (job.state, job.cancel_requested) == (JobState.QUEUED, True)
    running = queue(store)
    store.set_state(running.id, JobState.EXTRACTING)
    job = store.cancel(running.id)
    assert (job.state, job.cancel_requested) == (JobState.EXTRACTING, True)


@pytest.mark.parametrize("terminal", [JobState.DONE, JobState.FAILED, JobState.CANCELLED])
def test_cancel_terminal_or_unknown_is_rejected(store: JobStore, terminal: JobState) -> None:
    job = queue(store)
    if terminal is JobState.DONE:
        advance(store, job.id, JobState.EXTRACTING, JobState.TRANSCRIBING, JobState.GENERATING, JobState.DONE)
    elif terminal is JobState.FAILED:
        store.set_state(job.id, JobState.FAILED, failure_reason="x")
    else:
        store.set_state(job.id, JobState.CANCELLED)
    with pytest.raises(InvalidTransition):
        store.cancel(job.id)
    with pytest.raises(JobNotFound):
        store.cancel("nope")


def test_queue_position(store: JobStore) -> None:
    first, second, third = queue(store), queue(store), queue(store)
    assert [store.queue_position(j.id) for j in (first, second, third)] == [1, 2, 3]
    store.set_state(first.id, JobState.EXTRACTING)
    assert store.queue_position(first.id) == 0
    assert store.queue_position(second.id) == 1
    assert store.queue_position(third.id) == 2
    assert store.queue_position(store.create("t", 5).id) == 0  # still uploading
    with pytest.raises(JobNotFound):
        store.queue_position("nope")


def test_queue_position_breaks_timestamp_ties_by_insertion_order(tmp_path: Path) -> None:
    flat = JobStore(tmp_path, ttl_hours=1, max_upload_bytes=1000, clock=lambda: 5.0)
    a, b = queue(flat), queue(flat)
    assert (flat.queue_position(a.id), flat.queue_position(b.id)) == (1, 2)


def test_add_usage_accumulates(store: JobStore) -> None:
    job = store.create("t", 10)
    store.add_usage(job.id, input_tokens=100, output_tokens=10, cache_read_tokens=5, cost_usd=0.5)
    updated = store.add_usage(job.id, input_tokens=50, cache_write_tokens=7, audio_minutes=1.5, cost_usd=0.25)
    assert updated.usage == {
        "input_tokens": 150,
        "output_tokens": 10,
        "cache_read_tokens": 5,
        "cache_write_tokens": 7,
        "audio_minutes": 1.5,
        "cost_usd": 0.75,
    }
    assert store.get(job.id) == updated


def test_add_usage_unknown_cost_is_sticky_null(store: JobStore) -> None:
    job = store.create("t", 10)
    store.add_usage(job.id, input_tokens=1, cost_usd=None)
    assert store.add_usage(job.id, input_tokens=1, cost_usd=1.0).usage["cost_usd"] is None


def test_add_usage_rejects_unknown_field_and_missing_job(store: JobStore) -> None:
    job = store.create("t", 10)
    with pytest.raises(TypeError):
        store.add_usage(job.id, bogus=1)  # type: ignore[call-arg]
    with pytest.raises(JobNotFound):
        store.add_usage("nope", input_tokens=1)


def test_add_usage_is_atomic_across_threads(store: JobStore) -> None:
    job = store.create("t", 10)

    def work() -> None:
        for _ in range(10):
            store.add_usage(job.id, input_tokens=1)

    threads = [threading.Thread(target=work) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    final = store.get(job.id)
    assert final is not None
    assert final.usage["input_tokens"] == 50


def test_update_media_info(store: JobStore) -> None:
    job = store.create("t", 10)
    updated = store.update_info(
        job.id, duration_s=12.5, language="en", no_speech=True, providers={"transcription": "local"}
    )
    assert (updated.duration_s, updated.language, updated.no_speech) == (12.5, "en", True)
    assert updated.providers == {"transcription": "local"}
    with pytest.raises(JobNotFound):
        store.update_info("nope", language="en")


def test_job_dir(store: JobStore, tmp_path: Path) -> None:
    job = store.create("t", 10)
    assert store.job_dir(job.id) == tmp_path / "jobs" / job.id
    for bad in ("../etc", "a/b", "", ".."):
        with pytest.raises(ValueError):
            store.job_dir(bad)


def test_recover_after_restart(store: JobStore) -> None:
    uploading = store.create("t", 10)
    queued = queue(store)
    extracting = queue(store)
    advance(store, extracting.id, JobState.EXTRACTING)
    transcribing = queue(store)
    advance(store, transcribing.id, JobState.EXTRACTING, JobState.TRANSCRIBING)
    generating = queue(store)
    advance(store, generating.id, JobState.EXTRACTING, JobState.TRANSCRIBING, JobState.GENERATING)
    done = queue(store)
    advance(store, done.id, JobState.EXTRACTING, JobState.TRANSCRIBING, JobState.GENERATING, JobState.DONE)

    sources = {}
    for job in (uploading, queued, extracting, transcribing, generating, done):
        sources[job.id] = store.job_dir(job.id) / "source.bin"
        sources[job.id].parent.mkdir(parents=True)
        sources[job.id].write_bytes(b"x")

    assert store.recover_after_restart() == 4

    # FR-014: failed jobs lose their source video; other jobs keep theirs.
    assert {job_id for job_id, path in sources.items() if path.exists()} == {uploading.id, done.id}
    for job in (queued, extracting, transcribing, generating):
        got = store.get(job.id)
        assert got is not None
        assert got.state is JobState.FAILED
        assert got.failure_reason == RESTART_MESSAGE
        assert got.failure_code == "server_restarted"
    for job, state in ((uploading, JobState.UPLOADING), (done, JobState.DONE)):
        got = store.get(job.id)
        assert got is not None
        assert got.state is state
    assert store.recover_after_restart() == 0


def test_state_survives_new_store_instance(store: JobStore, tmp_path: Path) -> None:
    job = queue(store)
    other = JobStore(tmp_path, ttl_hours=24, max_upload_bytes=1000)
    got = other.get(job.id)
    assert got is not None
    assert got.state is JobState.QUEUED


def test_connect_failure_surfaces(tmp_path: Path) -> None:
    blocker = tmp_path / "f"
    blocker.write_text("x")
    with pytest.raises((sqlite3.Error, OSError)):
        db.connect(blocker / "sub" / "db.sqlite")
