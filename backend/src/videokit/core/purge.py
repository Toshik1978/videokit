"""TTL purge: delete expired jobs (rows and directories), FR-015."""

import asyncio
import shutil
import time
from collections.abc import Callable

from videokit.core.errors import logger
from videokit.core.jobs import JobStore

PURGE_INTERVAL_S = 600.0


def purge_expired(store: JobStore, now: float) -> int:
    """Delete jobs with `expires_at < now`, including stale `uploading` ones; returns how many were removed.

    Waiting or running jobs are skipped (the worker owns their directory). A job whose directory cannot be removed
    keeps its row so the next run retries it.
    """
    purged = 0
    for job in store.list_expired(now):
        try:
            directory = store.job_dir(job.id)
            if directory.exists():
                shutil.rmtree(directory)
            purged += store.delete(job.id)
        except OSError:
            logger.warning("Could not purge job directory %s; will retry", job.id)
    return purged


async def run_purge_loop(
    store: JobStore,
    interval_s: float = PURGE_INTERVAL_S,
    clock: Callable[[], float] = time.time,
) -> None:
    """Purge expired jobs every `interval_s` seconds until cancelled. Errors are logged, never fatal."""
    while True:
        try:
            removed = await asyncio.to_thread(purge_expired, store, clock())
            if removed:
                logger.info("Purged %d expired job(s)", removed)
        except Exception:
            logger.warning("Purge run failed; will retry", exc_info=False)
        await asyncio.sleep(interval_s)
