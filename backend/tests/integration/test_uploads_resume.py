"""Resumable tus uploads (US3, FR-003, SC-003): streaming, interruption and resume, conflicts, large files."""

import asyncio
import hashlib
import itertools
import time
from collections.abc import Awaitable, Callable, Iterable, Iterator, MutableMapping
from pathlib import Path
from typing import Any

import pytest
from anyio.from_thread import BlockingPortal
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from videokit.core import uploads
from videokit.core import worker as worker_module
from videokit.core.config import Settings
from videokit.core.jobs import JobState, JobStore
from videokit.main import create_app

MiB = 1024 * 1024
TUS = {"Tus-Resumable": "1.0.0"}
PATCH_HEADERS = {**TUS, "Content-Type": "application/offset+octet-stream"}

Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]


@pytest.fixture
def completed() -> list[str]:
    return []


@pytest.fixture
def client(tmp_path: Path, completed: list[str]) -> Iterator[TestClient]:
    settings = Settings(data_dir=tmp_path / "data", anthropic_api_key=SecretStr("sk-test"), max_upload_gb=1)
    app = create_app(settings, tools_package="videokit.tools", static_dir=tmp_path / "static")

    async def on_complete(job_id: str) -> None:
        completed.append(job_id)

    app.state.on_upload_complete = on_complete
    with TestClient(app) as c:
        yield c


def _app(client: TestClient) -> FastAPI:
    app: FastAPI = client.app  # type: ignore[assignment]
    return app


def _portal(client: TestClient) -> BlockingPortal:
    assert client.portal is not None
    return client.portal


def _store(client: TestClient) -> JobStore:
    store: JobStore = _app(client).state.store
    return store


def _create(client: TestClient, size: int) -> str:
    return _store(client).create("demo_tool", size).id


def _offset(client: TestClient, job_id: str) -> int:
    r = client.head(f"/api/uploads/{job_id}", headers=TUS)
    assert r.status_code == 200
    return int(r.headers["Upload-Offset"])


def _source(client: TestClient, job_id: str) -> Path:
    return _store(client).job_dir(job_id) / "source.bin"


async def _asgi_patch(
    app: FastAPI, job_id: str, offset: int, receive: Receive, length: int | None = None
) -> tuple[int, dict[str, str]]:
    """Send a PATCH straight to the ASGI app so the test controls the body messages, including a disconnect.
    (TestClient joins a streamed request body into a single message.) `length` sets Content-Length."""
    headers = [
        (b"host", b"testserver"),
        (b"tus-resumable", b"1.0.0"),
        (b"content-type", b"application/offset+octet-stream"),
        (b"upload-offset", str(offset).encode()),
    ]
    if length is not None:
        headers.append((b"content-length", str(length).encode()))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "PATCH",
        "scheme": "http",
        "path": f"/api/uploads/{job_id}",
        "raw_path": f"/api/uploads/{job_id}".encode(),
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    response: dict[str, Any] = {"status": 0, "headers": {}}

    async def send(message: Message) -> None:
        if message["type"] == "http.response.start":
            response["status"] = message["status"]
            response["headers"] = {k.decode().lower(): v.decode() for k, v in message.get("headers", [])}

    await app(scope, receive, send)
    return response["status"], response["headers"]


def _scripted(messages: Iterable[Message]) -> Receive:
    pending = iter(messages)

    async def receive() -> Message:
        return next(pending)

    return receive


def _body(data: bytes) -> Message:
    return {"type": "http.request", "body": data, "more_body": True}


DISCONNECT: Message = {"type": "http.disconnect"}


# --- interrupted PATCH and resume ----------------------------------------------------------------------------------


def test_interrupted_patch_keeps_received_bytes_and_resumes(client: TestClient, completed: list[str]) -> None:
    job_id = _create(client, 20)
    receive = _scripted([_body(b"0123456789"), _body(b"abc"), DISCONNECT])
    _portal(client).call(_asgi_patch, _app(client), job_id, 0, receive)

    assert _offset(client, job_id) == 13  # every byte that reached the server before the drop is kept
    assert _source(client, job_id).read_bytes() == b"0123456789abc"
    assert completed == []

    r = client.patch(f"/api/uploads/{job_id}", content=b"defghij", headers={**PATCH_HEADERS, "Upload-Offset": "13"})
    assert r.status_code == 204
    assert r.headers["Upload-Offset"] == "20"
    assert _source(client, job_id).read_bytes() == b"0123456789abcdefghij"
    assert completed == [job_id]


def test_disconnect_before_any_byte_changes_nothing(client: TestClient) -> None:
    job_id = _create(client, 10)
    status, headers = _portal(client).call(_asgi_patch, _app(client), job_id, 0, _scripted([DISCONNECT]))
    assert (status, headers["upload-offset"]) == (204, "0")  # nobody listens any more; it just must not crash
    assert _offset(client, job_id) == 0


def test_cancel_racing_a_patch_that_drops_before_any_byte(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    job_id = _create(client, 10)
    original = uploads._prepare

    def cancel_then_prepare(path: Path, offset: int) -> None:
        _portal(client).call(_app(client).state.worker.cancel, job_id)
        original(path, offset)

    monkeypatch.setattr(uploads, "_prepare", cancel_then_prepare)
    status, _ = _portal(client).call(_asgi_patch, _app(client), job_id, 0, _scripted([DISCONNECT]))
    assert status == 404
    assert not _store(client).job_dir(job_id).exists()


def test_resume_from_a_stale_offset_is_a_conflict(client: TestClient) -> None:
    job_id = _create(client, 20)
    receive = _scripted([_body(b"0123456789"), DISCONNECT])
    _portal(client).call(_asgi_patch, _app(client), job_id, 0, receive)
    url = f"/api/uploads/{job_id}"

    # A client that assumes nothing arrived (or too much) is told to resync with HEAD.
    for stale in ("0", "15"):
        r = client.patch(url, content=b"0123456789", headers={**PATCH_HEADERS, "Upload-Offset": stale})
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "offset_mismatch"
    assert _offset(client, job_id) == 10
    assert _source(client, job_id).read_bytes() == b"0123456789"


def test_repeat_patch_at_full_length_retries_completion(client: TestClient, completed: list[str]) -> None:
    """A finished upload still `uploading` (its completion never ran through) can be completed by an empty PATCH;
    the hook is idempotent, so firing it again is safe."""
    job_id = _create(client, 4)
    url = f"/api/uploads/{job_id}"
    assert client.patch(url, content=b"abcd", headers={**PATCH_HEADERS, "Upload-Offset": "0"}).status_code == 204
    r = client.patch(url, content=b"", headers={**PATCH_HEADERS, "Upload-Offset": "4"})
    assert r.status_code == 204
    assert r.headers["Upload-Offset"] == "4"
    assert completed == [job_id, job_id]


def test_finished_uploads_are_completed_at_startup(tmp_path: Path) -> None:
    """The process died after the last byte was recorded but before completion ran: the next start completes it
    (tus clients treat HEAD offset == length as done and never send another PATCH)."""
    settings = Settings(data_dir=tmp_path / "data", anthropic_api_key=SecretStr("sk-test"), max_upload_gb=1)
    store = JobStore(settings.data_dir, settings.job_ttl_hours, settings.max_upload_bytes)
    finished = store.create("demo_tool", 4).id
    store.set_upload_offset(finished, 4)
    partial = store.create("demo_tool", 4).id
    store.set_upload_offset(partial, 2)
    queued = store.create("demo_tool", 4).id
    store.set_upload_offset(queued, 4)
    store.set_state(queued, JobState.QUEUED)

    app = create_app(settings, tools_package="videokit.tools", static_dir=tmp_path / "static")
    completed: list[str] = []

    async def on_complete(job_id: str) -> None:
        completed.append(job_id)
        if len(completed) == 1:
            raise OSError("probe crashed")  # a failing completion must not stop the server from starting

    app.state.on_upload_complete = on_complete
    with TestClient(app) as c:
        assert completed == [finished]
        assert c.get("/api/health").status_code == 200


def test_expired_finished_upload_is_not_queued_at_startup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-015: a finished upload already past `expires_at` must not be probed or queued; the purge deletes it."""
    settings = Settings(data_dir=tmp_path / "data", anthropic_api_key=SecretStr("sk-test"), max_upload_gb=1)
    past = time.time() - (settings.job_ttl_hours + 1) * 3600
    old = JobStore(settings.data_dir, settings.job_ttl_hours, settings.max_upload_bytes, clock=lambda: past)
    job_id = old.create("demo_tool", 4).id
    old.set_upload_offset(job_id, 4)
    probed: list[Path] = []

    def spy_probe(path: Path) -> None:
        probed.append(path)
        raise AssertionError("an expired upload must not be probed")

    monkeypatch.setattr(worker_module, "probe", spy_probe)
    monkeypatch.setattr("videokit.main.run_purge_loop", _never_purge)

    app = create_app(settings, tools_package="videokit.tools", static_dir=tmp_path / "static")
    with TestClient(app):
        job = old.get(job_id)
        assert job is not None
        assert job.state is JobState.UPLOADING  # not queued
        assert probed == []  # and the worker never ran it


async def _never_purge(store: JobStore) -> None:
    await asyncio.Event().wait()


# --- streaming and concurrency ---------------------------------------------------------------------------------------


def test_streaming_patch_saves_progress_and_blocks_a_concurrent_patch(client: TestClient) -> None:
    app = _app(client)
    store = _store(client)
    job_id = _create(client, 6 * MiB)
    first = bytes([1]) * (3 * MiB)
    rest = bytes([2]) * (3 * MiB)
    stalled = asyncio.Event()
    resume = asyncio.Event()
    sent: list[Message] = [_body(first[i : i + MiB]) for i in range(0, len(first), MiB)]

    async def receive() -> Message:
        if sent:
            return sent.pop(0)
        if not resume.is_set():
            stalled.set()
            await resume.wait()
            return {"type": "http.request", "body": rest, "more_body": False}
        raise AssertionError("body already sent")

    async def scenario() -> None:
        task = asyncio.create_task(_asgi_patch(app, job_id, 0, receive))
        await asyncio.wait_for(stalled.wait(), 5)

        async def stored_offset() -> int:
            job = await asyncio.to_thread(store.get, job_id)
            assert job is not None
            return job.upload_offset

        for _ in range(500):  # the last write before the stall lands on disk and in the job record
            if await stored_offset() == len(first):
                break
            await asyncio.sleep(0.01)
        assert await stored_offset() == len(first)  # progress is persisted while the request is still open
        on_disk = await asyncio.to_thread(_source(client, job_id).read_bytes)
        assert on_disk == first

        # A second PATCH for the same upload is refused while the first one is still streaming.
        status, headers = await _asgi_patch(app, job_id, len(first), _scripted([_body(rest[:10]), DISCONNECT]))
        assert status == 409
        assert headers["tus-resumable"] == "1.0.0"
        assert await stored_offset() == len(first)

        resume.set()
        status, headers = await asyncio.wait_for(task, 10)
        assert status == 204
        assert headers["upload-offset"] == str(6 * MiB)

    _portal(client).call(scenario)
    assert _source(client, job_id).read_bytes() == first + rest


def test_patch_lock_is_released_after_an_error(client: TestClient) -> None:
    job_id = _create(client, 10)
    url = f"/api/uploads/{job_id}"
    assert client.patch(url, content=b"abc", headers={**PATCH_HEADERS, "Upload-Offset": "5"}).status_code == 409
    assert client.patch(url, content=b"abc", headers={**PATCH_HEADERS, "Upload-Offset": "0"}).status_code == 204


@pytest.mark.parametrize("state", [JobState.QUEUED, JobState.FAILED, JobState.CANCELLED])
def test_patch_rejected_when_not_uploading(client: TestClient, state: JobState) -> None:
    store = _store(client)
    job_id = _create(client, 3)
    if state is JobState.QUEUED:
        store.set_upload_offset(job_id, 3)
        store.set_state(job_id, state)
    else:
        store.set_state(job_id, state, "boom" if state is JobState.FAILED else None)
    offset = "3" if state is JobState.QUEUED else "0"
    r = client.patch(f"/api/uploads/{job_id}", content=b"abc", headers={**PATCH_HEADERS, "Upload-Offset": offset})
    assert r.status_code == 404
    assert client.head(f"/api/uploads/{job_id}", headers=TUS).status_code == 404


# --- more than 100 MB in 50 MB chunks (SC-003) -----------------------------------------------------------------------


def _blocks(first: int, count: int) -> Iterator[bytes]:
    """1 MiB blocks whose byte value encodes the block number, so misplaced data is detected."""
    for i in range(first, first + count):
        yield bytes([i % 251]) * MiB


def _streamed(first: int, count: int) -> Receive:
    """A body arriving as `count` 1 MiB messages, generated lazily."""
    messages = (_body(block) for block in _blocks(first, count))
    return _scripted(itertools.chain(messages, [{"type": "http.request", "body": b"", "more_body": False}]))


def test_upload_over_100_mb_in_50_mb_chunks(client: TestClient, completed: list[str]) -> None:
    assert uploads.UPLOAD_CHUNK_BYTES == 50 * MiB
    app = _app(client)
    total_blocks = 110
    job_id = _create(client, total_blocks * MiB)

    # A streamed body without Content-Length that overruns 50 MB is cut off and leaves nothing behind,
    # although most of it was already written and recorded.
    status, _ = _portal(client).call(_asgi_patch, app, job_id, 0, _streamed(0, 51))
    assert status == 413
    assert _offset(client, job_id) == 0
    assert _source(client, job_id).stat().st_size == 0

    expected = hashlib.sha256()
    for first, count in ((0, 50), (50, 50), (100, 10)):
        offset = first * MiB
        status, headers = _portal(client).call(_asgi_patch, app, job_id, offset, _streamed(first, count), count * MiB)
        assert status == 204
        assert int(headers["upload-offset"]) == offset + count * MiB
        for block in _blocks(first, count):
            expected.update(block)

    assert completed == [job_id]
    actual = hashlib.sha256()
    with _source(client, job_id).open("rb") as f:
        while block := f.read(MiB):
            actual.update(block)
    assert actual.hexdigest() == expected.hexdigest()
    assert _source(client, job_id).stat().st_size == total_blocks * MiB > 100 * 1000 * 1000
