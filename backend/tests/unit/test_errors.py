import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from videokit.core.errors import AppError, configure_logging, install_error_handlers


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


def test_unhandled_exception_logged_server_side(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR, logger="videokit"):
        client.get("/boom")
    assert any(rec.exc_info for rec in caplog.records)


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
