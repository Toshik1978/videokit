"""User-safe error handling and logging setup.

Error responses use the envelope ``{"error": {"code": "<snake_case>", "message": "<user-safe text>"}}``.
Messages never contain stack traces, request bodies, secrets or transcript text (FR-016).
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("videokit")

_HTTP_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
}


class AppError(Exception):
    """An error whose code and message are safe to show to the user."""

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def error_response(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def configure_logging(level: str) -> None:
    """Configure the ``videokit`` logger at the given level name (e.g. ``"INFO"``)."""
    numeric = logging.getLevelName(level.upper())
    if not isinstance(numeric, int):
        raise ValueError(f"Unknown log level: {level!r}")
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logger.setLevel(numeric)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return error_response(exc.code, exc.message, exc.status)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Log only locations and error types: never the submitted values.
        fields = [".".join(str(p) for p in e.get("loc", ())) for e in exc.errors()]
        logger.info("Invalid request on %s %s: %s", request.method, request.url.path, fields)
        return error_response("invalid_request", "The request was invalid. Check the submitted values.", 422)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "http_error")
        message = "The requested resource was not found." if exc.status_code == 404 else "The request failed."
        return error_response(code, message, exc.status_code)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.error("Unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
        return error_response("internal_error", "Something went wrong on the server.", 500)
