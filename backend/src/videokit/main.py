"""App factory: wires configuration, storage, tools and the SPA together."""

import asyncio
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, Response
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.routing import Match
from starlette.types import Scope

from videokit.core import api, uploads
from videokit.core import providers as providers_module
from videokit.core.config import ConfigurationError, Settings, load_settings, validate
from videokit.core.errors import configure_logging, install_error_handlers, logger
from videokit.core.jobs import JobStore
from videokit.core.media import MediaService
from videokit.core.purge import run_purge_loop
from videokit.core.registry import discover_tools
from videokit.core.worker import Context, Providers, StoreJobAccess, Worker

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


def _checked(chains: Providers) -> list[str]:
    """Run every provider's `check()` (no paid calls, no model loading); one problem line per unready provider."""
    return providers_module.problems(providers_module.status(chains))


def _mount_spa(app: FastAPI, static_dir: Path) -> None:
    root = static_dir.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> Response:
        if path == "api" or path.startswith("api/"):
            raise StarletteHTTPException(404)
        try:
            candidate = (root / path).resolve()
            servable = candidate.is_file() and candidate.is_relative_to(root)
        except ValueError, OSError:  # e.g. embedded NUL in the path
            servable = False
        if servable:
            return FileResponse(candidate)
        index = root / "index.html"
        if index.is_file():
            return FileResponse(index)
        raise StarletteHTTPException(404)


class _UnknownApiRoute(APIRoute):
    """Matches only `/api/...` paths no other API route knows, so a mounted path keeps its 405 + `Allow`."""

    def matches(self, scope: Scope) -> tuple[Match, Scope]:
        match, child = super().matches(scope)
        if match is Match.FULL:
            for route in scope["app"].router.routes:
                # The SPA catch-all (`/{path:path}`) partially matches every path; it does not make one mounted.
                if route is self or getattr(route, "path", None) == "/{path:path}":
                    continue
                if route.matches(scope)[0] is not Match.NONE:
                    return Match.NONE, {}
        return match, child


def _mount_unknown_api(app: FastAPI) -> None:
    """An unmounted `/api/...` path (e.g. a disabled tool's) answers 404 for every method, not the SPA's 405.

    Added at startup so it is the last `/api` route, whatever was mounted before.
    """

    async def unknown_api(path: str) -> Response:
        raise StarletteHTTPException(404)

    app.router.routes.append(
        _UnknownApiRoute(
            "/api/{path:path}",
            unknown_api,
            methods=["POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
            include_in_schema=False,
        )
    )


def create_app(
    settings: Settings,
    providers: Providers | None = None,
    *,
    tools_package: str = "videokit.tools",
    static_dir: Path = STATIC_DIR,
) -> FastAPI:
    """Build the application. Without `providers`, the chains are built from settings at startup.

    Startup validates the settings, then checks every provider; any problem aborts it (`StartupError`, FR-012).
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(settings.log_level)
        _mount_unknown_api(app)
        problems = await asyncio.to_thread(validate, settings)
        if problems:
            raise _fail(problems)

        def open_store() -> JobStore:
            store = JobStore(settings.data_dir, settings.job_ttl_hours, settings.max_upload_bytes)
            resumable = {tool_id for tool_id, tool in app.state.tools.items() if tool.resume_after_restart}
            recovered = store.recover_after_restart(resumable)
            if recovered:
                logger.info("Marked %d interrupted job(s) as failed after restart", recovered)
            return store

        if app.state.providers is None:
            try:
                app.state.providers = providers_module.build(settings)
            except ConfigurationError as exc:
                raise _fail(exc.problems) from None
        chains: Providers = app.state.providers
        problems = await asyncio.to_thread(_checked, chains)
        if problems:
            raise _fail(problems)
        app.state.store = await asyncio.to_thread(open_store)
        ctx = Context(
            media=MediaService(settings),
            transcriber=chains.transcriber,
            llm=chains.llm,
            settings=settings,
            video=chains.video,
        )
        worker = app.state.worker = Worker(app.state.store, app.state.tools, ctx)
        app.state.job_access = StoreJobAccess(app.state.store, ctx, worker)
        worker.start()
        # Uploads whose last byte was recorded before a stop but never completed: tus clients see them as done.
        for job_id in await asyncio.to_thread(app.state.store.finished_uploads):
            try:
                await app.state.complete_inputs(job_id)
            except Exception as exc:  # never block startup on one upload
                logger.error("Upload completion failed at startup (%s)", type(exc).__name__)
        purge_task = asyncio.create_task(run_purge_loop(app.state.store))
        try:
            yield
        finally:
            purge_task.cancel()
            await asyncio.gather(purge_task, return_exceptions=True)
            await worker.stop()

    # The git tag is the version (docs/RELEASING.md); "0.0.0" replaces FastAPI's fake default 0.1.0.
    app = FastAPI(
        title="VideoKit",
        version="0.0.0",
        lifespan=lifespan,
        openapi_url="/api/openapi.json",
        docs_url=None,
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.providers = providers

    async def complete_inputs(job_id: str) -> None:
        await app.state.worker.complete_inputs(job_id)

    app.state.complete_inputs = complete_inputs
    app.add_middleware(uploads.TusResumableMiddleware)
    install_error_handlers(app)  # after all other middleware: its catch-all must wrap them

    # Tools are mounted at creation (not in the lifespan) so their routes precede the SPA catch-all. A tool that is
    # not enabled by the settings is absent everywhere: no routes, no listing, no lane, not resumable (research V11).
    app.state.tools = {
        tool_id: tool for tool_id, tool in discover_tools(tools_package).items() if tool.enabled(settings)
    }
    app.include_router(api.router)
    app.include_router(uploads.router, prefix="/api")
    for tool in app.state.tools.values():
        app.include_router(tool.router, prefix=f"/api/tools/{tool.id}")

    _mount_spa(app, static_dir)
    return app


def app_from_env() -> FastAPI:
    """Uvicorn factory entry point (`uvicorn --factory videokit.main:app_from_env`).

    Validates and checks providers eagerly so a bad configuration exits with one line per problem, no traceback.
    """
    try:
        settings = load_settings()
    except ConfigurationError as exc:
        _fail(exc.problems)
        raise SystemExit(1) from None
    problems = validate(settings)
    if problems:
        _fail(problems)
        raise SystemExit(1)
    try:
        chains = providers_module.build(settings)
    except ConfigurationError as exc:
        _fail(exc.problems)
        raise SystemExit(1) from None
    problems = _checked(chains)
    if problems:
        _fail(problems)
        raise SystemExit(1)
    return create_app(settings, chains)
