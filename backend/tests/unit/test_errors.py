import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from pydantic import BaseModel
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from videokit.core.errors import AppError, UnhandledErrorMiddleware, configure_logging, install_error_handlers

SECRET = "secret-api-key-123 leaked in message"


class Body(BaseModel):
    n: int


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/app-error")
    def app_error() -> None:
        raise AppError("job_not_found", "That job does not exist.", 404)

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("secret-api-key-123 leaked in message")

    @app.post("/body")
    def body(b: Body) -> dict[str, int]:
        return {"n": b.n}

    return TestClient(app, raise_server_exceptions=False)


def test_app_error_envelope(client: TestClient) -> None:
    r = client.get("/app-error")
    assert r.status_code == 404
    assert r.json() == {"error": {"code": "job_not_found", "message": "That job does not exist."}}


def test_unhandled_exception_is_internal_error_without_details(client: TestClient) -> None:
    r = client.get("/boom")
    assert r.status_code == 500
    assert r.json() == {"error": {"code": "internal_error", "message": "Something went wrong on the server."}}
    assert "secret" not in r.text
    assert "Traceback" not in r.text


def test_unhandled_exception_logged_without_traceback_or_message(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """Constitution VI / FR-016: only method, path and exception type; no traceback, no exception message."""
    with caplog.at_level(logging.DEBUG):
        client.get("/boom")
    videokit_records = [rec for rec in caplog.records if rec.name == "videokit"]
    assert [rec.getMessage() for rec in videokit_records] == ["Unhandled error on GET /boom (RuntimeError)"]
    assert not any(rec.exc_info or rec.exc_text or rec.stack_info for rec in caplog.records)
    assert "Traceback" not in caplog.text
    assert "secret-api-key-123" not in caplog.text


def test_unhandled_exception_never_reaches_the_server() -> None:
    """The error is answered inside the app, so the server (uvicorn) has no traceback to log."""
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("secret-api-key-123 leaked in message")

    r = TestClient(app, raise_server_exceptions=True).get("/boom")  # would re-raise any escaping exception
    assert r.status_code == 500
    assert r.json() == {"error": {"code": "internal_error", "message": "Something went wrong on the server."}}


def test_error_after_response_started_is_swallowed(caplog: pytest.LogCaptureFixture) -> None:
    app = FastAPI()
    install_error_handlers(app)

    def chunks() -> Iterator[bytes]:
        yield b"partial"
        raise RuntimeError("secret-api-key-123 leaked in message")

    @app.get("/stream")
    def stream() -> StreamingResponse:
        return StreamingResponse(chunks())

    with caplog.at_level(logging.DEBUG):
        r = TestClient(app, raise_server_exceptions=True).get("/stream")
    assert r.status_code == 200
    assert "Unhandled error on GET /stream (RuntimeError)" in caplog.text
    assert "Traceback" not in caplog.text
    assert "secret-api-key-123" not in caplog.text


async def _run_lifespan(app: ASGIApp) -> tuple[list[Message], Exception | None]:
    """Drive one lifespan (startup, then shutdown) through the app; return what it sent and what it raised."""
    incoming: list[Message] = [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]
    sent: list[Message] = []

    async def receive() -> Message:
        return incoming.pop(0)

    async def send(message: Message) -> None:
        sent.append(message)

    try:
        await app({"type": "lifespan", "state": {}}, receive, send)
    except Exception as exc:
        return sent, exc
    return sent, None


def _app_failing_in(phase: str) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if phase == "startup":
            raise RuntimeError(SECRET)
        yield
        raise RuntimeError(SECRET)

    app = FastAPI(lifespan=lifespan)
    install_error_handlers(app)
    return app


@pytest.mark.parametrize(
    ("phase", "expected"),
    [
        ("startup", [{"type": "lifespan.startup.failed", "message": "Startup failed (RuntimeError)"}]),
        (
            "shutdown",
            [
                {"type": "lifespan.startup.complete"},
                {"type": "lifespan.shutdown.failed", "message": "Shutdown failed (RuntimeError)"},
            ],
        ),
    ],
)
async def test_lifespan_failure_is_reported_by_exception_type_only(phase: str, expected: list[Message]) -> None:
    """Starlette reports the full traceback text, which uvicorn logs; the catch-all reduces it to the type name."""
    sent, exc = await _run_lifespan(_app_failing_in(phase))
    assert sent == expected
    assert isinstance(exc, RuntimeError)  # in-process callers (TestClient) still see the real exception


@pytest.mark.parametrize(
    ("complete_startup", "expected"),
    [
        (False, {"type": "lifespan.startup.failed", "message": "Startup failed (ValueError)"}),
        (True, {"type": "lifespan.shutdown.failed", "message": "Shutdown failed (ValueError)"}),
    ],
)
async def test_unreported_lifespan_exception_is_reported_before_it_reaches_the_server(
    complete_startup: bool, expected: Message
) -> None:
    """A failure the server has not been told about would be logged by uvicorn with a traceback."""

    async def raw(scope: Scope, receive: Receive, send: Send) -> None:
        await receive()
        if complete_startup:
            await send({"type": "lifespan.startup.complete"})
            await receive()
        raise ValueError(SECRET)

    sent, exc = await _run_lifespan(UnhandledErrorMiddleware(raw))
    assert sent[-1] == expected
    assert isinstance(exc, ValueError)


def test_validation_error_is_invalid_request_and_does_not_echo_input(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        r = client.post("/body", json={"n": "private-transcript-text"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_request"
    assert "private-transcript-text" not in r.text
    assert "private-transcript-text" not in caplog.text


def test_http_exception_uses_envelope(client: TestClient) -> None:
    r = client.get("/missing-route")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_configure_logging_sets_level() -> None:
    configure_logging("debug")
    assert logging.getLogger("videokit").level == logging.DEBUG
    configure_logging("WARNING")
    assert logging.getLogger("videokit").level == logging.WARNING


def test_configure_logging_rejects_unknown_level() -> None:
    with pytest.raises(ValueError):
        configure_logging("loud")
