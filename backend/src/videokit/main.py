"""App factory: wires configuration, storage, tools and the SPA together."""

import asyncio
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from videokit.core import api
from videokit.core.config import ConfigurationError, Settings, load_settings, validate
from videokit.core.errors import configure_logging, install_error_handlers, logger
from videokit.core.jobs import JobStore
from videokit.core.registry import discover_tools

STATIC_DIR = Path(__file__).parent / "static"


class StartupError(Exception):
    """Configuration is invalid: startup aborts (non-zero exit) after one stderr line per problem."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


def _fail(problems: list[str]) -> StartupError:
    for problem in problems:
        print(f"Configuration error: {problem}", file=sys.stderr)
    return StartupError(problems)


def _mount_spa(app: FastAPI, static_dir: Path) -> None:
    root = static_dir.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> Response:
        if path == "api" or path.startswith("api/"):
            raise StarletteHTTPException(404)
        try:
            candidate = (root / path).resolve()
            servable = candidate.is_file() and candidate.is_relative_to(root)
        except (ValueError, OSError):  # e.g. embedded NUL in the path
            servable = False
        if servable:
            return FileResponse(candidate)
        index = root / "index.html"
        if index.is_file():
            return FileResponse(index)
        raise StarletteHTTPException(404)


def create_app(
    settings: Settings,
    providers: Any = None,
    *,
    tools_package: str = "videokit.tools",
    static_dir: Path = STATIC_DIR,
) -> FastAPI:
    """Build the application. `providers` is reserved for injecting provider chains (built in later tasks)."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(settings.log_level)
        problems = await asyncio.to_thread(validate, settings)
        if problems:
            raise _fail(problems)

        def open_store() -> JobStore:
            store = JobStore(settings.data_dir, settings.job_ttl_hours, settings.max_upload_bytes)
            recovered = store.recover_after_restart()
            if recovered:
                logger.info("Marked %d interrupted job(s) as failed after restart", recovered)
            return store

        app.state.store = await asyncio.to_thread(open_store)
        app.state.provider_status = api.provider_status(settings, ready=True)
        yield

    app = FastAPI(title="videokit", lifespan=lifespan, openapi_url="/api/openapi.json", docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.providers = providers
    install_error_handlers(app)

    # Tools are mounted at creation (not in the lifespan) so their routes precede the SPA catch-all.
    app.state.tools = discover_tools(tools_package)
    app.include_router(api.router)
    for tool in app.state.tools.values():
        app.include_router(tool.router, prefix=f"/api/tools/{tool.id}")

    _mount_spa(app, static_dir)
    return app


def app_from_env() -> FastAPI:
    """Uvicorn factory entry point (`uvicorn --factory videokit.main:app_from_env`)."""
    try:
        settings = load_settings()
    except ConfigurationError as exc:
        _fail(exc.problems)
        raise SystemExit(1) from None
    problems = validate(settings)
    if problems:
        _fail(problems)
        raise SystemExit(1)
    return create_app(settings)
