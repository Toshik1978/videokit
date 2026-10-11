"""Log hygiene (FR-016): a full fake run never logs transcript text, frame data, request bodies or API keys.

The real app and worker run upload -> done -> regenerate with real ffmpeg extraction, a fake transcriber and a
scripted LLM. Each chain starts with a provider whose error message echoes the secrets, so the fallback logging
paths run too. Every record from every logger is captured at DEBUG level and checked for distinctive markers.
An unhandled route error under a real uvicorn server logs no traceback and no exception message (Constitution VI).
"""

import base64
import logging
import shutil
import socket
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient
from pydantic import SecretStr

from fakes_llm import FakeLLM
from fakes_transcribe import FakeTranscriber
from videokit.core.config import Settings
from videokit.core.llm import LLMChain, ProviderError
from videokit.core.transcribe import Segment, TranscriberChain
from videokit.core.worker import Providers
from videokit.main import create_app

MakeVideo = Callable[..., Path]
pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")

TRANSCRIPT_MARKERS = ("Zebrafinch-transcript-7q2x", "Quokka-transcript-9w4v")
ANTHROPIC_KEY = "sk-ant-loghygiene-anthropic-key-5k8m"
OPENAI_KEY = "sk-loghygiene-openai-key-3j6n"
HINT_MARKER = "Pangolin-hint-2r5t"
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
        created = client.post("/api/jobs", json={"tool_id": "youtube_prep", "size": len(data)})
        job_id = created.json()["job_id"]
        assert client.patch(f"/api/uploads/{job_id}", content=data, headers=TUS_PATCH).status_code == 204
        assert _wait_done(client, job_id)["state"] == "done"

        url = f"/api/tools/youtube_prep/jobs/{job_id}/regenerate"
        rejected = client.post(url, json={"section": "title", "hint": HINT_MARKER * 40})  # too long: logged as 422
        regenerated = client.post(url, json={"section": "title", "hint": HINT_MARKER})

    assert rejected.status_code == 422
    assert regenerated.status_code == 200
    # The run really sent the markers and frames to the providers ...
    assert all(m in llm.requests[0].context_text for m in TRANSCRIPT_MARKERS)
    assert HINT_MARKER in llm.requests[1].turns[0].text
    frames = [f.jpeg for f in llm.requests[0].frames]
    assert frames
    # ... and the fallback and validation paths logged something.
    log = "\n".join(captured.lines)
    assert "provider anthropic failed" in log and "provider openai failed" in log
    assert "Invalid request" in log

    forbidden = [*TRANSCRIPT_MARKERS, HINT_MARKER, ANTHROPIC_KEY, OPENAI_KEY, "/9j/"]  # "/9j/": base64 JPEG start
    for jpeg in frames:
        forbidden += [base64.b64encode(jpeg).decode(), base64.b64encode(jpeg[63:159]).decode(), repr(jpeg[:64])[2:-1]]
    leaks = [s[:40] for s in forbidden if s in log]
    assert leaks == [], f"secrets or private data in the logs: {leaks}"


def test_unhandled_route_error_under_uvicorn_logs_no_traceback_or_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, captured: Capture
) -> None:
    secret = f"store failed near {TRANSCRIPT_MARKERS[0]} with key {ANTHROPIC_KEY}"
    settings = Settings(data_dir=tmp_path / "data", anthropic_api_key=SecretStr(ANTHROPIC_KEY), log_level="DEBUG")
    providers = Providers(
        transcriber=TranscriberChain([FakeTranscriber(name="local", segments=[])]), llm=LLMChain([FakeLLM([])])
    )
    app = create_app(settings, providers, static_dir=tmp_path / "static")
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_config=None, log_level="debug", lifespan="on"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            assert thread.is_alive() and time.monotonic() < deadline, "uvicorn did not start"
            time.sleep(0.02)

        def broken_get(job_id: str) -> None:
            raise RuntimeError(secret)

        monkeypatch.setattr(app.state.store, "get", broken_get)
        response = httpx.get(f"http://127.0.0.1:{port}/api/jobs/abc123")
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()

    assert response.status_code == 500
    assert response.json() == {"error": {"code": "internal_error", "message": "Something went wrong on the server."}}
    log = "\n".join(captured.lines)
    assert "uvicorn.error" in log, "uvicorn's own log records were not captured"  # e.g. "Application startup ..."
    assert "videokit ERROR Unhandled error on GET /api/jobs/abc123 (RuntimeError)" in log
    assert "Traceback" not in log
    assert "Exception in ASGI application" not in log
    assert "store failed near" not in log
    assert TRANSCRIPT_MARKERS[0] not in log and ANTHROPIC_KEY not in log
