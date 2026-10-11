import asyncio
import contextlib
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from videokit.core.jobs import JobState, JobStore
from videokit.core.purge import purge_expired, run_purge_loop

TTL_S = 3600


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(tmp_path: Path, clock: Clock) -> JobStore:
    return JobStore(tmp_path, ttl_hours=1, max_upload_bytes=1000, clock=clock)


def make_job(store: JobStore, *path: JobState, with_dir: bool = True) -> str:
    job = store.create("youtube_prep", 10)
    if path:
        store.set_upload_offset(job.id, 10)
    for state in path:
        store.set_state(job.id, state, "boom" if state is JobState.FAILED else None)
    if with_dir:
        directory = store.job_dir(job.id)
        directory.mkdir(parents=True)
        (directory / "source.mp4").write_bytes(b"x")
    return job.id


async def wait_until(condition: Callable[[], bool]) -> None:
    for _ in range(300):
        if condition():
            return
        await asyncio.sleep(0.01)


def test_purges_expired_terminal_and_uploading_jobs(store: JobStore) -> None:
    ids = {
        "uploading": make_job(store),
        "done": make_job(
            store,
            JobState.QUEUED,
            JobState.EXTRACTING,
            JobState.TRANSCRIBING,
            JobState.GENERATING,
            JobState.DONE,
        ),
        "failed": make_job(store, JobState.FAILED),
        "cancelled": make_job(store, JobState.CANCELLED),
    }

    assert purge_expired(store, TTL_S + 1) == 4

    for job_id in ids.values():
        assert store.get(job_id) is None
        assert not store.job_dir(job_id).exists()


def test_keeps_unexpired_jobs(store: JobStore) -> None:
    job_id = make_job(store)

    assert purge_expired(store, TTL_S) == 0  # strictly "expires_at < now"

    assert store.get(job_id) is not None
    assert store.job_dir(job_id).is_dir()


def test_missing_directory_is_fine(store: JobStore) -> None:
    job_id = make_job(store, JobState.CANCELLED, with_dir=False)

    assert purge_expired(store, TTL_S + 1) == 1

    assert store.get(job_id) is None


def test_running_jobs_are_never_purged(store: JobStore) -> None:
    job_id = make_job(store, JobState.QUEUED)

    assert purge_expired(store, TTL_S + 1) == 0

    assert store.get(job_id) is not None
    assert store.job_dir(job_id).is_dir()


def test_failing_directory_removal_keeps_row_for_retry(store: JobStore, monkeypatch: pytest.MonkeyPatch) -> None:
    job_id = make_job(store, JobState.FAILED)
    other = make_job(store, JobState.CANCELLED)
    real_rmtree = shutil.rmtree

    def flaky(path: Path) -> None:
        if path.name == job_id:
            raise OSError("busy")
        real_rmtree(path)

    monkeypatch.setattr("videokit.core.purge.shutil.rmtree", flaky)

    assert purge_expired(store, TTL_S + 1) == 1

    assert store.get(job_id) is not None
    assert store.get(other) is None


async def test_loop_purges_periodically_and_stops_on_cancel(store: JobStore, clock: Clock) -> None:
    job_id = make_job(store, JobState.CANCELLED)
    clock.now = TTL_S + 1

    task = asyncio.create_task(run_purge_loop(store, interval_s=0.01, clock=clock))
    await wait_until(lambda: store.get(job_id) is None)
    assert store.get(job_id) is None

    later = make_job(store, JobState.CANCELLED)  # expires an hour after "now"
    clock.now += 2 * TTL_S
    await wait_until(lambda: store.get(later) is None)
    assert store.get(later) is None

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert task.done()


async def test_loop_survives_purge_errors(store: JobStore, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def broken(_store: JobStore, _now: float) -> int:
        nonlocal calls
        calls += 1
        raise RuntimeError("db locked")

    monkeypatch.setattr("videokit.core.purge.purge_expired", broken)
    task = asyncio.create_task(run_purge_loop(store, interval_s=0.01))
    await wait_until(lambda: calls >= 2)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert calls >= 2
