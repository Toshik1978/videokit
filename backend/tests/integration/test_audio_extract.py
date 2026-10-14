"""Audio Extract: the tool run through the HTTP API and the worker, and its MP3 download route."""

import asyncio
import re
import subprocess
import threading
import time
import warnings
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from pydantic import SecretStr

from fakes_llm import FakeLLM
from fakes_transcribe import FakeTranscriber
from videokit.core import worker as worker_module
from videokit.core.config import Settings
from videokit.core.jobs import Job, JobState, JobStore
from videokit.core.llm import LLMChain, ProviderStatus
from videokit.core.media import MediaService
from videokit.core.registry import CoreContext, JobHandle
from videokit.core.transcribe import ProviderError, TranscriberChain
from videokit.core.worker import Providers
from videokit.main import create_app
from videokit.tools.audio_extract import TOOL

PATCH_HEADERS = {"Tus-Resumable": "1.0.0", "Content-Type": "application/offset+octet-stream"}
MakeVideo = Callable[..., Path]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data", anthropic_api_key=SecretStr("sk-test"), max_upload_gb=1)


@pytest.fixture
def providers() -> Providers:
    return Providers(transcriber=TranscriberChain([FakeTranscriber()]), llm=LLMChain([FakeLLM([])]))


def _app(tmp_path: Path, settings: Settings, providers: Providers) -> Any:
    return create_app(settings, providers, tools_package="videokit.tools", static_dir=tmp_path / "static")


@pytest.fixture
def client(tmp_path: Path, settings: Settings, providers: Providers) -> Iterator[TestClient]:
    with TestClient(_app(tmp_path, settings, providers)) as c:
        yield c


@pytest.fixture
def gate(monkeypatch: pytest.MonkeyPatch) -> Iterator[threading.Event]:
    """Clearing the returned event holds every MP3 extraction (inside `extracting`) until it is set again."""
    event = threading.Event()
    event.set()
    original = MediaService.extract_mp3

    def held(self: MediaService, job_dir: Path) -> Any:
        event.wait(10)
        return original(self, job_dir)

    monkeypatch.setattr(MediaService, "extract_mp3", held)
    try:
        yield event
    finally:
        event.set()


@pytest.fixture
def sound_video(make_video: MakeVideo) -> bytes:
    return make_video(audio=True).read_bytes()


def _store(client: TestClient) -> JobStore:
    store: JobStore = client.app.state.store  # type: ignore[attr-defined]
    return store


def _job(client: TestClient, job_id: str) -> Job:
    job = _store(client).get(job_id)
    assert job is not None
    return job


def _wait_state(client: TestClient, job_id: str, state: JobState, timeout: float = 15.0) -> Job:
    deadline = time.monotonic() + timeout
    while _job(client, job_id).state is not state:
        if time.monotonic() > deadline:
            raise AssertionError(f"job did not reach {state} (now {_job(client, job_id).state})")
        time.sleep(0.02)
    return _job(client, job_id)


def _create(client: TestClient, size: int, tool_id: str = "audio_extract", file_name: str | None = None) -> str:
    body: dict[str, Any] = {"tool_id": tool_id, "size": size}
    if file_name is not None:
        body["file_name"] = file_name
    r = client.post("/api/jobs", json=body)
    assert r.status_code == 201
    return str(r.json()["job_id"])


def _upload(client: TestClient, data: bytes, tool_id: str = "audio_extract", file_name: str | None = None) -> str:
    job_id = _create(client, len(data), tool_id, file_name)
    r = client.patch(f"/api/uploads/{job_id}", content=data, headers={**PATCH_HEADERS, "Upload-Offset": "0"})
    assert r.status_code == 204
    return job_id


def _url(job_id: str) -> str:
    return f"/api/tools/audio_extract/jobs/{job_id}/audio"


def _error(client: TestClient, method: str, job_id: str) -> tuple[int, str | None]:
    """Status and error code of a download request (HEAD responses carry no body, so no code)."""
    r = client.request(method, _url(job_id))
    return r.status_code, (r.json()["error"]["code"] if method == "GET" else None)


# --- tool ------------------------------------------------------------------------------------------------------


def test_tool_identity_and_listing(client: TestClient) -> None:
    audio = {"id": "audio_extract", "name": "Audio Extract", "description": "Get a video's audio as an MP3."}
    prep = {
        "id": "youtube_prep",
        "name": "YouTube Prep",
        "description": "Title, description, tags and chapters for a video.",
    }
    assert {"id": TOOL.id, "name": TOOL.name, "description": TOOL.description} == audio
    assert client.get("/api/tools").json() == [audio, prep]


def test_full_flow_produces_a_free_mp3_named_after_the_video(client: TestClient, sound_video: bytes) -> None:
    job_id = _upload(client, sound_video, file_name="trip.mp4")
    _wait_state(client, job_id, JobState.DONE)
    job_dir = _store(client).job_dir(job_id)
    mp3 = job_dir / "audio.mp3"

    body = client.get(f"/api/jobs/{job_id}").json()
    assert body["state"] == "done"
    result = body["result"]
    assert set(result) == {"file", "download_name", "duration_s", "size_bytes", "bitrate_kbps"}
    assert (result["file"], result["download_name"], result["bitrate_kbps"]) == ("audio.mp3", "trip.mp3", 192)
    assert result["duration_s"] == pytest.approx(6.0, abs=1.0)
    assert result["size_bytes"] == mp3.stat().st_size > 0
    assert body["usage"]["cost_usd"] == 0
    assert body["providers"] == {}
    assert body["language"] is None
    assert not (job_dir / "source.bin").exists()

    r = client.get(_url(job_id))
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/mpeg"
    assert r.headers["content-disposition"] == 'attachment; filename="trip.mp3"'
    assert r.headers["accept-ranges"] == "bytes"
    assert r.headers["content-length"] == str(mp3.stat().st_size)
    assert r.content == mp3.read_bytes()

    head = client.head(_url(job_id))
    assert head.status_code == 200
    assert head.content == b""
    for name in ("content-type", "content-disposition", "content-length", "accept-ranges"):
        assert head.headers[name] == r.headers[name]

    part = client.get(_url(job_id), headers={"Range": "bytes=0-99"})
    assert part.status_code == 206
    assert part.headers["content-range"] == f"bytes 0-99/{mp3.stat().st_size}"
    assert part.content == mp3.read_bytes()[:100]


@pytest.mark.parametrize(
    ("file_name", "disposition"),
    [
        ("my trip.mov", 'attachment; filename="my trip.mp3"'),
        ("Café.mp4", "attachment; filename=\"Cafe.mp3\"; filename*=UTF-8''Caf%C3%A9.mp3"),
        (
            "Поездка.mp4",
            "attachment; filename=\"audio.mp3\"; filename*=UTF-8''%D0%9F%D0%BE%D0%B5%D0%B7%D0%B4%D0%BA%D0%B0.mp3",
        ),
        (None, 'attachment; filename="audio.mp3"'),
    ],
)
def test_download_name_header(client: TestClient, sound_video: bytes, file_name: str | None, disposition: str) -> None:
    job_id = _upload(client, sound_video, file_name=file_name)
    _wait_state(client, job_id, JobState.DONE)
    for method in ("GET", "HEAD"):
        r = client.request(method, _url(job_id))
        assert r.status_code == 200
        assert r.headers["content-disposition"] == disposition


_DISPOSITION = re.compile(r"""attachment; filename="([^"\\]*)"; filename\*=UTF-8''[A-Za-z0-9%._~-]+""")


@pytest.mark.parametrize(
    ("file_name", "fallback"),
    [
        ("a\uff02; filename=\uff02evil.exe\uff02 x.mp4", "a__ filename__evil.exe_ x.mp3"),  # fullwidth quotes
        ("a\uff3c.mp4", "a_.mp3"),  # fullwidth reverse solidus
        ("a\ufe68b.mp4", "a_b.mp3"),  # small reverse solidus
        ("\u00e9\uff0f..\uff0fz.mp4", "e_.._z.mp3"),  # fullwidth solidus
    ],
)
def test_ascii_fallback_cannot_smuggle_header_syntax(
    client: TestClient, sound_video: bytes, file_name: str, fallback: str
) -> None:
    """NFKD maps fullwidth and small forms back to `"`, `\\` and `/`: the fallback is sanitized again."""
    job_id = _upload(client, sound_video, file_name=file_name)
    _wait_state(client, job_id, JobState.DONE)
    disposition = client.get(_url(job_id)).headers["content-disposition"]
    match = _DISPOSITION.fullmatch(disposition)
    assert match is not None, disposition
    assert match.group(1) == fallback
    assert "/" not in match.group(1)
    assert disposition.count("filename=") == 1
    assert disposition.count("filename*=") == 1


# --- download errors -------------------------------------------------------------------------------------------


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_download_of_an_unknown_job_or_another_tools_job_is_not_found(client: TestClient, method: str) -> None:
    other = _create(client, 100, tool_id="youtube_prep")
    assert _error(client, method, "no-such-job")[0] == 404
    assert _error(client, method, other)[0] == 404
    if method == "GET":
        assert _error(client, method, "no-such-job")[1] == "job_not_found"
        assert _error(client, method, other)[1] == "job_not_found"


def _seed_done(store: JobStore, data: bytes) -> str:
    """A finished Audio Extract job written straight to the store, with its MP3."""
    job = store.create("audio_extract", 10, "trip.mp4")
    store.set_upload_offset(job.id, 10)
    for state in (JobState.QUEUED, JobState.EXTRACTING, JobState.DONE):
        store.set_state(job.id, state)
    store.job_dir(job.id).mkdir(parents=True)
    (store.job_dir(job.id) / "audio.mp3").write_bytes(data)
    return job.id


def test_download_of_an_expired_job_is_not_found(
    tmp_path: Path, settings: Settings, providers: Providers, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def no_purge(store: JobStore) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr("videokit.main.run_purge_loop", no_purge)
    settings.data_dir.mkdir(parents=True)
    past = time.time() - (settings.job_ttl_hours + 1) * 3600
    old = JobStore(settings.data_dir, settings.job_ttl_hours, settings.max_upload_bytes, clock=lambda: past)
    job_id = _seed_done(old, b"ID3 not really an mp3")

    with TestClient(_app(tmp_path, settings, providers)) as client:
        assert _error(client, "GET", job_id) == (404, "job_not_found")
        assert _error(client, "HEAD", job_id)[0] == 404
        assert (_store(client).job_dir(job_id) / "audio.mp3").exists()  # not purged: expiry alone hides it


def test_download_of_a_done_job_whose_mp3_is_gone_is_not_found(client: TestClient) -> None:
    store = _store(client)
    job_id = _seed_done(store, b"ID3 not really an mp3")
    assert client.get(_url(job_id)).status_code == 200
    (store.job_dir(job_id) / "audio.mp3").unlink()
    assert _error(client, "GET", job_id) == (404, "job_not_found")
    assert _error(client, "HEAD", job_id)[0] == 404


def test_download_before_done_is_rejected(client: TestClient, gate: threading.Event, sound_video: bytes) -> None:
    gate.clear()
    running = _upload(client, sound_video)
    _wait_state(client, running, JobState.EXTRACTING)
    queued = _upload(client, sound_video)
    assert _job(client, queued).state is JobState.QUEUED
    uploading = _create(client, len(sound_video))
    for job_id in (uploading, queued, running):
        assert _error(client, "GET", job_id) == (409, "job_not_done")
        assert _error(client, "HEAD", job_id)[0] == 409

    gate.set()
    for job_id in (running, queued):
        _wait_state(client, job_id, JobState.DONE)
        assert client.get(_url(job_id)).status_code == 200


def test_video_without_audio_fails_with_no_audio(client: TestClient, make_video: MakeVideo) -> None:
    job_id = _upload(client, make_video().read_bytes(), file_name="silent.mp4")
    job = _wait_state(client, job_id, JobState.FAILED)
    assert job.failure_code == "no_audio"
    assert not (_store(client).job_dir(job_id) / "source.bin").exists()
    assert _error(client, "GET", job_id) == (409, "job_not_done")
    assert _error(client, "HEAD", job_id)[0] == 409


def test_text_file_fails_with_not_a_video(client: TestClient) -> None:
    job_id = _upload(client, b"just some text, not a video\n" * 20, file_name="notes.mp4")
    job = _wait_state(client, job_id, JobState.FAILED)
    assert job.failure_code == "not_a_video"
    assert not (_store(client).job_dir(job_id) / "source.bin").exists()
    assert _error(client, "GET", job_id) == (409, "job_not_done")


def test_multi_track_video_uses_the_default_track(client: TestClient, make_video: MakeVideo) -> None:
    """The first track is mono, the default (second) one stereo: the MP3 follows the default track."""
    job_id = _upload(client, make_video(two_audio_tracks=True).read_bytes(), file_name="two.mp4")
    _wait_state(client, job_id, JobState.DONE)
    mp3 = _store(client).job_dir(job_id) / "audio.mp3"
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=channels", "-of", "csv=p=0", str(mp3)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert out.split() == ["2"]


# --- cancellation ----------------------------------------------------------------------------------------------


def test_cancel_during_extracting_cancels_and_removes_the_job_directory(
    client: TestClient, gate: threading.Event, sound_video: bytes
) -> None:
    gate.clear()
    job_id = _upload(client, sound_video, file_name="trip.mp4")
    _wait_state(client, job_id, JobState.EXTRACTING)
    r = client.post(f"/api/jobs/{job_id}/cancel")
    assert (r.status_code, r.json()["state"]) == (202, "extracting")

    gate.set()
    _wait_state(client, job_id, JobState.CANCELLED)
    assert not _store(client).job_dir(job_id).exists()
    assert _error(client, "GET", job_id) == (409, "job_not_done")


# --- lanes and providers ---------------------------------------------------------------------------------------


class BlockingTool:
    """A stand-in YouTube Prep: holds every job in `extracting` until `gate` is set."""

    id = "youtube_prep"
    name = "YouTube Prep"
    description = "Blocked by the test."
    router = APIRouter()

    def __init__(self) -> None:
        self.gate = threading.Event()

    async def run(self, job: JobHandle, ctx: CoreContext) -> None:
        job.set_state("extracting")
        await asyncio.to_thread(self.gate.wait, 10)
        (job.dir / "result.json").write_text("{}", encoding="utf-8")


def test_audio_extract_finishes_while_a_youtube_prep_job_is_blocked(
    tmp_path: Path, settings: Settings, providers: Providers, sound_video: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-018: the real Audio Extract tool runs in its own lane next to a blocked YouTube Prep lane."""
    monkeypatch.setattr(worker_module, "POLL_INTERVAL_S", 60.0)
    blocking = BlockingTool()
    app = _app(tmp_path, settings, providers)
    app.state.tools = {TOOL.id: TOOL, blocking.id: blocking}
    with TestClient(app) as client:
        try:
            blocked = _upload(client, sound_video, tool_id="youtube_prep")
            _wait_state(client, blocked, JobState.EXTRACTING)
            audio = _upload(client, sound_video, file_name="trip.mp4")
            _wait_state(client, audio, JobState.DONE)
            assert _job(client, blocked).state is JobState.EXTRACTING
            assert client.get(_url(audio)).status_code == 200
        finally:
            blocking.gate.set()
        _wait_state(client, blocked, JobState.DONE)


class SwitchableTranscriber(FakeTranscriber):
    def __init__(self) -> None:
        super().__init__(error=ProviderError("unavailable"))
        self.ready = True

    def check(self) -> ProviderStatus:
        return ProviderStatus(ready=self.ready, detail="fake")


class SwitchableLLM(FakeLLM):
    def __init__(self) -> None:
        super().__init__([])
        self.ready = True

    def check(self) -> ProviderStatus:
        return ProviderStatus(ready=self.ready, detail=self.model)


def test_audio_extract_works_while_every_provider_is_not_ready(
    tmp_path: Path, settings: Settings, sound_video: bytes
) -> None:
    """FR-007 / US1-AS5: startup validation is unchanged (providers are ready then); afterwards every provider
    reports not-ready and fails every call, and Audio Extract still works without calling any of them."""
    transcriber, llm = SwitchableTranscriber(), SwitchableLLM()
    providers = Providers(transcriber=TranscriberChain([transcriber]), llm=LLMChain([llm]))
    with TestClient(_app(tmp_path, settings, providers)) as client:
        transcriber.ready = llm.ready = False
        health = client.get("/api/health").json()["providers"]
        assert [e["ready"] for kind in ("transcription", "llm") for e in health[kind]] == [False, False]

        job_id = _upload(client, sound_video, file_name="trip.mp4")
        _wait_state(client, job_id, JobState.DONE)
        body = client.get(f"/api/jobs/{job_id}").json()
        assert body["result"]["download_name"] == "trip.mp3"
        assert (body["usage"]["cost_usd"], body["providers"]) == (0, {})
        assert client.get(_url(job_id)).status_code == 200
    assert transcriber.calls == []
    assert llm.requests == []


def test_download_route_has_distinct_openapi_operations(client: TestClient) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # e.g. FastAPI's duplicate operation id warning
        paths = client.get("/api/openapi.json").json()["paths"]
    operations = paths["/api/tools/audio_extract/jobs/{job_id}/audio"]
    assert set(operations) == {"get", "head"}
    assert operations["get"]["operationId"] != operations["head"]["operationId"]
