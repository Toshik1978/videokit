"""Log hygiene (FR-016): a full fake run never logs transcript text, frame data, request bodies or API keys.

The real app and worker run upload -> done -> regenerate with real ffmpeg extraction, a fake transcriber and a
scripted LLM, then an Audio Extract job and its download; the uploaded file name is never logged either. Each
chain starts with a provider whose error message echoes the secrets, so the fallback logging paths run too.
Every record from every logger is captured at DEBUG level and checked for distinctive markers. An unhandled route
error, a startup failure and a shutdown failure under a real uvicorn server log no traceback and no exception
message (Constitution VI).
"""

import base64
import io
import logging
import shutil
import socket
import threading
import time
from collections.abc import Callable, Iterator
from collections.abc import Set as AbstractSet
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from fakes_llm import FakeLLM
from fakes_transcribe import FakeTranscriber
from fakes_videogen import FakeVideoGenerator
from videokit.core.config import Settings
from videokit.core.jobs import JobStore
from videokit.core.llm import LLMChain, ProviderError
from videokit.core.transcribe import Segment, TranscriberChain
from videokit.core.videogen import PollResult, Usage, VideoChain, VideoError
from videokit.core.worker import Providers, Worker
from videokit.main import create_app
from videokit.tools.video_generation import pipeline as video_pipeline
from videokit.tools.video_generation.inputs import CreationInputs, store_inputs

MakeVideo = Callable[..., Path]
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")

TRANSCRIPT_MARKERS = ("Zebrafinch-transcript-7q2x", "Quokka-transcript-9w4v")
ANTHROPIC_KEY = "sk-ant-loghygiene-anthropic-key-5k8m"
OPENAI_KEY = "sk-loghygiene-openai-key-3j6n"
HINT_MARKER = "Pangolin-hint-2r5t"
FILE_NAME_MARKER = "Narwhal-file-name-4d8z"
TUS_PATCH = {"Tus-Resumable": "1.0.0", "Content-Type": "application/offset+octet-stream", "Upload-Offset": "0"}
RESULT: dict[str, Any] = {
    "language": "en",
    "cover_titles": ["Hidden Gem Resort", "Worth The Price", "Best Pool Ever"],
    "title": "Hotel tour: lobby, pool and rooms",
    "description": "A tour of a small resort.\n\nWe look at the lobby, the pool and a room.",
    "tags": ["hotel review", "travel"],
    "hashtags": ["#travel", "#hotel", "#review"],
    "chapters": [
        {"start_seconds": 0, "title": "Arrival"},
        {"start_seconds": 12, "title": "Lobby and pool"},
        {"start_seconds": 24, "title": "Rooms"},
    ],
}
LEAKY_ERROR = f"provider echoed {TRANSCRIPT_MARKERS[0]} with key {ANTHROPIC_KEY} and {OPENAI_KEY}"


class Capture(logging.Handler):
    """Collects every record it sees, fully formatted (message, arguments, exception and stack text)."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


@pytest.fixture
def captured() -> Iterator[Capture]:
    """Every logger at DEBUG, propagating to a root handler, for the duration of the test."""
    handler = Capture()
    root = logging.getLogger()
    loggers = [logging.getLogger(name) for name in list(logging.root.manager.loggerDict)]
    saved = [(lg, lg.level, lg.propagate, lg.disabled) for lg in loggers]
    root_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    for lg in loggers:
        lg.setLevel(logging.DEBUG)
        lg.propagate = True
        lg.disabled = False
    try:
        yield handler
    finally:
        root.removeHandler(handler)
        root.setLevel(root_level)
        for lg, level, propagate, disabled in saved:
            lg.setLevel(level)
            lg.propagate = propagate
            lg.disabled = disabled


def _wait_done(client: TestClient, job_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 20
    while True:
        body: dict[str, Any] = client.get(f"/api/jobs/{job_id}").json()
        if body["state"] in ("done", "failed", "cancelled"):
            return body
        assert time.monotonic() < deadline, "job did not finish in time"
        time.sleep(0.05)


@needs_ffmpeg
def test_full_run_logs_no_transcript_frames_hints_or_keys(
    tmp_path: Path, make_video: MakeVideo, captured: Capture
) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        anthropic_api_key=SecretStr(ANTHROPIC_KEY),
        openai_api_key=SecretStr(OPENAI_KEY),
        log_level="DEBUG",
        max_upload_gb=1,
    )
    segments = [Segment(0.0, 10.0, f"{TRANSCRIPT_MARKERS[0]} at the lobby"), Segment(10.0, 30.0, TRANSCRIPT_MARKERS[1])]
    llm = FakeLLM([RESULT, {**RESULT, "title": "A shorter hotel tour"}])
    providers = Providers(
        transcriber=TranscriberChain(
            [
                FakeTranscriber(name="openai", error=ProviderError(LEAKY_ERROR)),
                FakeTranscriber(name="local", segments=segments),
            ]
        ),
        llm=LLMChain([FakeLLM([ProviderError(LEAKY_ERROR)], name="anthropic"), llm]),
    )
    data = make_video(audio=True, colors=("red", "blue", "green"), segment_s=12.0).read_bytes()

    with TestClient(create_app(settings, providers, static_dir=tmp_path / "static")) as client:
        created = client.post(
            "/api/tools/youtube_prep/jobs",
            json={"size": len(data), "file_name": f"{FILE_NAME_MARKER}.mp4"},
        )
        job_id = created.json()["job_id"]
        assert client.patch(f"/api/uploads/{job_id}", content=data, headers=TUS_PATCH).status_code == 204
        assert _wait_done(client, job_id)["state"] == "done"

        url = f"/api/tools/youtube_prep/jobs/{job_id}/regenerate"
        rejected = client.post(url, json={"section": "title", "hint": HINT_MARKER * 40})  # too long: logged as 422
        regenerated = client.post(url, json={"section": "title", "hint": HINT_MARKER})

        audio_job = client.post(
            "/api/tools/audio_extract/jobs",
            json={"size": len(data), "file_name": f"{FILE_NAME_MARKER}.mov"},
        ).json()["job_id"]
        assert client.patch(f"/api/uploads/{audio_job}", content=data, headers=TUS_PATCH).status_code == 204
        audio_body = _wait_done(client, audio_job)
        download = client.get(f"/api/tools/audio_extract/jobs/{audio_job}/audio")

    assert rejected.status_code == 422
    assert regenerated.status_code == 200
    # The run really sent the markers and frames to the providers ...
    assert all(m in llm.requests[0].context_text for m in TRANSCRIPT_MARKERS)
    assert HINT_MARKER in llm.requests[1].turns[0].text
    assert audio_body["state"] == "done"
    assert audio_body["result"]["download_name"] == f"{FILE_NAME_MARKER}.mp3"
    assert download.status_code == 200
    assert FILE_NAME_MARKER in download.headers["content-disposition"]
    frames = [f.jpeg for f in llm.requests[0].frames]
    assert frames
    # ... and the fallback and validation paths logged something.
    log = "\n".join(captured.lines)
    assert "provider anthropic failed" in log and "provider openai failed" in log
    assert "Invalid request" in log

    forbidden = [
        *TRANSCRIPT_MARKERS,
        HINT_MARKER,
        FILE_NAME_MARKER,
        ANTHROPIC_KEY,
        OPENAI_KEY,
        "/9j/",
    ]  # "/9j/": base64 JPEG start
    for jpeg in frames:
        forbidden += [base64.b64encode(jpeg).decode(), base64.b64encode(jpeg[63:159]).decode(), repr(jpeg[:64])[2:-1]]
    leaks = [s[:40] for s in forbidden if s in log]
    assert leaks == [], f"secrets or private data in the logs: {leaks}"


def _quiet_app(tmp_path: Path) -> FastAPI:
    settings = Settings(data_dir=tmp_path / "data", anthropic_api_key=SecretStr(ANTHROPIC_KEY), log_level="DEBUG")
    providers = Providers(
        transcriber=TranscriberChain([FakeTranscriber(name="local", segments=[])]), llm=LLMChain([FakeLLM([])])
    )
    return create_app(settings, providers, static_dir=tmp_path / "static")


@contextmanager
def _uvicorn(app: FastAPI) -> Iterator[tuple[uvicorn.Server, int, list[BaseException]]]:
    """A real uvicorn server in a thread; yields once it has started (or given up starting), then stops it.

    Anything that ends the server thread (uvicorn exits via SystemExit when startup fails) is caught and recorded in
    the yielded list instead of escaping the thread, so pytest sees no unhandled thread exception.
    """
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_config=None, log_level="debug", lifespan="on"))
    exits: list[BaseException] = []

    def run() -> None:
        try:
            server.run(sockets=[sock])
        except BaseException as exc:  # noqa: BLE001 - incl. SystemExit; recorded for the test to assert on
            exits.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive():
            assert time.monotonic() < deadline, "uvicorn did not start"
            time.sleep(0.02)
        yield server, sock.getsockname()[1], exits
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
    assert not thread.is_alive(), "uvicorn did not stop"


def _assert_no_traceback_or_message(log: str, message: str) -> None:
    assert "uvicorn.error" in log, "uvicorn's own log records were not captured"  # e.g. "Waiting for application ..."
    assert "Traceback" not in log
    assert "Exception in " not in log  # uvicorn's "Exception in ASGI application" / "Exception in 'lifespan' protocol"
    assert message not in log
    assert TRANSCRIPT_MARKERS[0] not in log and ANTHROPIC_KEY not in log


def test_unhandled_route_error_under_uvicorn_logs_no_traceback_or_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, captured: Capture
) -> None:
    secret = f"store failed near {TRANSCRIPT_MARKERS[0]} with key {ANTHROPIC_KEY}"
    app = _quiet_app(tmp_path)
    with _uvicorn(app) as (server, port, _):
        assert server.started

        def broken_get(job_id: str) -> None:
            raise RuntimeError(secret)

        monkeypatch.setattr(app.state.store, "get", broken_get)
        response = httpx.get(f"http://127.0.0.1:{port}/api/jobs/abc123")

    assert response.status_code == 500
    assert response.json() == {"error": {"code": "internal_error", "message": "Something went wrong on the server."}}
    log = "\n".join(captured.lines)
    assert "videokit ERROR Unhandled error on GET /api/jobs/abc123 (RuntimeError)" in log
    _assert_no_traceback_or_message(log, secret)


def test_startup_failure_under_uvicorn_logs_no_traceback_or_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, captured: Capture
) -> None:
    secret = f"recovery failed near {TRANSCRIPT_MARKERS[0]} with key {ANTHROPIC_KEY}"

    def broken_recover(self: JobStore, resumable: AbstractSet[str]) -> int:
        raise RuntimeError(secret)

    monkeypatch.setattr(JobStore, "recover_after_restart", broken_recover)
    with _uvicorn(_quiet_app(tmp_path)) as (server, _, exits):
        pass

    assert not server.started  # what `uvicorn.run` / the CLI turn into a non-zero exit status
    assert [type(e) for e in exits] == [SystemExit]  # the thread's exit was recorded, not leaked
    log = "\n".join(captured.lines)
    assert "uvicorn.error ERROR Startup failed (RuntimeError)" in log
    assert "uvicorn.error ERROR Application startup failed. Exiting." in log
    _assert_no_traceback_or_message(log, secret)


def test_shutdown_failure_under_uvicorn_logs_no_traceback_or_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, captured: Capture
) -> None:
    secret = f"worker stop failed near {TRANSCRIPT_MARKERS[0]} with key {ANTHROPIC_KEY}"

    async def broken_stop(self: Worker) -> None:
        raise RuntimeError(secret)

    monkeypatch.setattr(Worker, "stop", broken_stop)
    with _uvicorn(_quiet_app(tmp_path)) as (server, _, exits):
        assert server.started

    log = "\n".join(captured.lines)
    assert exits == []
    assert "uvicorn.error ERROR Shutdown failed (RuntimeError)" in log
    assert "uvicorn.error ERROR Application shutdown failed. Exiting." in log
    _assert_no_traceback_or_message(log, secret)


# --- Video Generation -----------------------------------------------------------------------------------------

VIDEO_PROMPT_MARKER = "Axolotl-prompt-8h3q"
VIDEO_MESSAGE_MARKER = "Capybara-provider-message-6t1c"
GEMINI_KEY = "AIza-loghygiene-gemini-key-9p2b"
VIDEO_CODES = ["provider_out_of_credit", "content_blocked", "invalid_picture", "provider_unavailable", "video_failed"]


def _video_run(
    tmp_path: Path,
    make_picture: Callable[..., Path],
    monkeypatch: pytest.MonkeyPatch,
    fake: FakeVideoGenerator,
    *,
    cancel: bool = False,
) -> tuple[dict[str, Any], bytes]:
    """Run one Video Generation job (marker prompt, one picture) to its end; return its API body and the
    normalized picture bytes the provider received."""
    monkeypatch.setattr(video_pipeline, "POLL_INTERVAL_S", 0.01)
    monkeypatch.setattr(video_pipeline, "WAIT_CAP_S", 0.3)
    settings = Settings(
        data_dir=tmp_path / "data",
        anthropic_api_key=SecretStr(ANTHROPIC_KEY),
        gemini_api_key=SecretStr(GEMINI_KEY),
        video_providers=["omni"],
        log_level="DEBUG",
    )
    providers = Providers(
        transcriber=TranscriberChain([FakeTranscriber(name="local", segments=[])]),
        llm=LLMChain([FakeLLM([])]),
        video=VideoChain([fake]),
    )
    picture = make_picture("png", 80, 60).read_bytes()
    with TestClient(create_app(settings, providers, static_dir=tmp_path / "static")) as client:
        access = client.app.state.job_access  # type: ignore[attr-defined]
        job = access.create("video_generation")
        fields = CreationInputs(f"{VIDEO_PROMPT_MARKER} on a hill", 5, "9:16", "1080p")
        store_inputs(access.job_dir(job.id), access.ctx.media, fields, [io.BytesIO(picture)])
        client.portal.call(access.complete, job.id)  # type: ignore[union-attr]
        if cancel:
            deadline = time.monotonic() + 10
            while not fake.poll_refs:
                assert time.monotonic() < deadline, "the job never started polling"
                time.sleep(0.005)
            assert client.post(f"/api/jobs/{job.id}/cancel").status_code == 202
        body = _wait_done(client, job.id)
        # Try again reads the inputs back through the API; serving them must not log them either.
        base = f"/api/tools/video_generation/jobs/{job.id}"
        if cancel:  # a cancelled job's data is deleted, shortly after the state changes
            deadline = time.monotonic() + 10
            while access.job_dir(job.id).exists():
                assert time.monotonic() < deadline, "the cancelled job's data was never deleted"
                time.sleep(0.01)
        inputs, served = client.get(f"{base}/inputs"), client.get(f"{base}/pictures/1")
        if cancel:
            assert (inputs.status_code, served.status_code) == (404, 404)
        else:
            assert inputs.status_code == 200 and VIDEO_PROMPT_MARKER in inputs.json()["prompt"]
            assert served.status_code == 200 and served.content == fake.requests[0].pictures[0].jpeg
    assert len(fake.requests) == 1
    assert VIDEO_PROMPT_MARKER in fake.requests[0].prompt  # the provider really received the prompt
    return body, fake.requests[0].pictures[0].jpeg


def _assert_video_log_clean(log: str, jpeg: bytes) -> None:
    forbidden = [
        VIDEO_PROMPT_MARKER,
        VIDEO_MESSAGE_MARKER,
        GEMINI_KEY,
        ANTHROPIC_KEY,
        "/9j/",
        base64.b64encode(jpeg).decode(),
        base64.b64encode(jpeg[20:100]).decode(),
        repr(jpeg[:64])[2:-1],
    ]
    leaks = [s[:40] for s in forbidden if s in log]
    assert leaks == [], f"secrets or private data in the logs: {leaks}"


def _leaky(code: str) -> VideoError:
    message = f"provider echoed {VIDEO_PROMPT_MARKER} {VIDEO_MESSAGE_MARKER} key {GEMINI_KEY}"
    return VideoError(code, message)  # type: ignore[arg-type]


@needs_ffmpeg
def test_video_generation_success_logs_no_prompt_picture_or_key(
    tmp_path: Path, make_picture: Callable[..., Path], monkeypatch: pytest.MonkeyPatch, captured: Capture
) -> None:
    fake = FakeVideoGenerator(model="gemini-omni-1.1-flash", polls=["pending", "done"])
    body, jpeg = _video_run(tmp_path, make_picture, monkeypatch, fake)
    assert body["state"] == "done"
    _assert_video_log_clean("\n".join(captured.lines), jpeg)


@needs_ffmpeg
@pytest.mark.parametrize("code", VIDEO_CODES)
def test_video_generation_failures_log_no_prompt_picture_provider_message_or_key(
    tmp_path: Path,
    make_picture: Callable[..., Path],
    monkeypatch: pytest.MonkeyPatch,
    captured: Capture,
    code: str,
) -> None:
    scenarios = {
        "raised-at-submit": FakeVideoGenerator(submits=[_leaky(code)]),
        "returned-by-poll": FakeVideoGenerator(
            polls=[PollResult(done=True, video=None, usage=Usage(), error=_leaky(code))]
        ),
        "raised-by-poll": FakeVideoGenerator(polls=["pending", _leaky(code)] * 50),
    }
    for name, fake in scenarios.items():
        captured.lines.clear()
        body, jpeg = _video_run(tmp_path / name, make_picture, monkeypatch, fake)
        assert body["state"] == "failed"
        assert body["failure_code"] == (
            "provider_timeout" if code == "provider_unavailable" and name == "raised-by-poll" else code
        )
        _assert_video_log_clean("\n".join(captured.lines), jpeg)


@needs_ffmpeg
def test_video_generation_timeout_and_unexpected_errors_log_no_prompt_picture_or_message(
    tmp_path: Path, make_picture: Callable[..., Path], monkeypatch: pytest.MonkeyPatch, captured: Capture
) -> None:
    timeout = FakeVideoGenerator(polls=["pending"] * 1000)
    body, jpeg = _video_run(tmp_path / "timeout", make_picture, monkeypatch, timeout)
    assert (body["state"], body["failure_code"]) == ("failed", "provider_timeout")
    _assert_video_log_clean("\n".join(captured.lines), jpeg)

    captured.lines.clear()
    oserror = FakeVideoGenerator(polls=[OSError(f"{VIDEO_MESSAGE_MARKER} {VIDEO_PROMPT_MARKER}")])
    body, jpeg = _video_run(tmp_path / "oserror", make_picture, monkeypatch, oserror)
    assert (body["state"], body["failure_code"]) == ("failed", "video_failed")
    log = "\n".join(captured.lines)
    assert "OSError" in log  # the type is logged ...
    _assert_video_log_clean(log, jpeg)  # ... never the message

    captured.lines.clear()
    crashed = FakeVideoGenerator(submits=[RuntimeError(f"{VIDEO_MESSAGE_MARKER} {GEMINI_KEY}")])
    body, jpeg = _video_run(tmp_path / "crashed", make_picture, monkeypatch, crashed)
    assert body["state"] == "failed"
    _assert_video_log_clean("\n".join(captured.lines), jpeg)


@needs_ffmpeg
def test_video_generation_cancel_logs_no_prompt_picture_or_key(
    tmp_path: Path, make_picture: Callable[..., Path], monkeypatch: pytest.MonkeyPatch, captured: Capture
) -> None:
    def broken_cancel(self: FakeVideoGenerator, ref: str) -> None:
        raise OSError(VIDEO_MESSAGE_MARKER)

    monkeypatch.setattr(FakeVideoGenerator, "cancel", broken_cancel)
    fake = FakeVideoGenerator(polls=["pending"] * 1000)
    body, jpeg = _video_run(tmp_path, make_picture, monkeypatch, fake, cancel=True)
    assert body["state"] == "cancelled"
    log = "\n".join(captured.lines)
    assert "OSError" in log  # a failed best-effort cancel is logged by type only
    _assert_video_log_clean(log, jpeg)


class _ProcessDied(BaseException):
    """Passes every `except Exception`, so the job stays exactly as the dying process left it."""


def _video_app(tmp_path: Path, fake: FakeVideoGenerator) -> FastAPI:
    settings = Settings(
        data_dir=tmp_path / "data",
        anthropic_api_key=SecretStr(ANTHROPIC_KEY),
        gemini_api_key=SecretStr(GEMINI_KEY),
        video_providers=["omni"],
        log_level="DEBUG",
    )
    providers = Providers(
        transcriber=TranscriberChain([FakeTranscriber(name="local", segments=[])]),
        llm=LLMChain([FakeLLM([])]),
        video=VideoChain([fake]),
    )
    return create_app(settings, providers, static_dir=tmp_path / "static")


def _start_video_job(client: TestClient, picture: bytes, until: Callable[[Path], bool]) -> str:
    """Create a marker-prompt job and wait until `until(job_dir)`, i.e. the point where the test stops the app."""
    access = client.app.state.job_access  # type: ignore[attr-defined]
    job = access.create("video_generation")
    fields = CreationInputs(f"{VIDEO_PROMPT_MARKER} on a hill", 5, "9:16", "1080p")
    store_inputs(access.job_dir(job.id), access.ctx.media, fields, [io.BytesIO(picture)])
    client.portal.call(access.complete, job.id)  # type: ignore[union-attr]
    deadline = time.monotonic() + 10
    while not until(access.job_dir(job.id)):
        assert time.monotonic() < deadline, "the job never reached the point to stop at"
        time.sleep(0.005)
    return str(job.id)


@needs_ffmpeg
def test_video_generation_resume_logs_no_prompt_picture_or_message(
    tmp_path: Path, make_picture: Callable[..., Path], monkeypatch: pytest.MonkeyPatch, captured: Capture
) -> None:
    monkeypatch.setattr(video_pipeline, "POLL_INTERVAL_S", 0.01)
    monkeypatch.setattr(video_pipeline, "WAIT_CAP_S", 5.0)
    picture = make_picture("png", 80, 60).read_bytes()

    before = FakeVideoGenerator(submits=["op-A"], polls=["pending"] * 10_000)
    with TestClient(_video_app(tmp_path, before)) as client:
        job_id = _start_video_job(client, picture, lambda d: (d / "operation.json").exists() and bool(before.poll_refs))
    jpeg = before.requests[0].pictures[0].jpeg

    # The resumed poll fails once with a message that echoes the secrets, then the clip finishes.
    after = FakeVideoGenerator(polls=[_leaky("provider_unavailable"), "done"])
    with TestClient(_video_app(tmp_path, after)) as client:
        body = _wait_done(client, job_id)
    assert body["state"] == "done"
    assert after.requests == [] and after.poll_refs == ["op-A", "op-A"]  # resumed, not resubmitted
    _assert_video_log_clean("\n".join(captured.lines), jpeg)


@needs_ffmpeg
def test_video_generation_resume_impossible_logs_no_prompt_picture_or_message(
    tmp_path: Path, make_picture: Callable[..., Path], captured: Capture
) -> None:
    picture = make_picture("png", 80, 60).read_bytes()

    class Dying(FakeVideoGenerator):
        def submit(self, req: Any) -> str:
            self.requests.append(req)
            raise _ProcessDied(f"{VIDEO_MESSAGE_MARKER} {VIDEO_PROMPT_MARKER}")

    dying = Dying()

    with TestClient(_video_app(tmp_path, dying)) as client:
        job_id = _start_video_job(client, picture, lambda d: (d / "submit.json").exists())

    fresh = FakeVideoGenerator()
    with TestClient(_video_app(tmp_path, fresh)) as client:
        body = _wait_done(client, job_id)
    assert (body["state"], body["failure_code"]) == ("failed", "resume_impossible")
    assert fresh.requests == []
    _assert_video_log_clean("\n".join(captured.lines), dying.requests[0].pictures[0].jpeg)
