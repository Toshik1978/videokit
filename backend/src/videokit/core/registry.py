"""Tool registry: the seam between core and tools (see contracts/internal-interfaces.md).

`core` never imports concrete tools; tools are discovered by importing the subpackages of a
package (default `videokit.tools`) that expose a module-level `TOOL`.
"""

import importlib
import pkgutil
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from fastapi import APIRouter, Request

from videokit.core.jobs import Job
from videokit.core.jobs import JobNotFound as JobNotFound  # re-exported: part of the route seam

if TYPE_CHECKING:
    from videokit.core.config import Settings
    from videokit.core.llm.base import Usage
    from videokit.core.llm.chain import LLMChain
    from videokit.core.media import MediaService
    from videokit.core.transcribe.chain import TranscriberChain

TOOL_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


class JobCancelled(Exception):
    """Raised by `JobHandle.check_cancelled()` when cancellation was requested for the job."""


class RegistryError(Exception):
    """A tool failed validation during discovery."""


class JobHandle(Protocol):
    """What a tool's `run` may do to the job it is processing. Usable from worker threads."""

    @property
    def id(self) -> str: ...

    @property
    def dir(self) -> Path:
        """The job directory `DATA_DIR/jobs/<id>/`."""
        ...

    @property
    def source_name(self) -> str | None:
        """The original upload file name (sanitized by core), or None when the client sent none."""
        ...

    def set_state(self, state: str, **fields: Any) -> None:
        """Move to the next stage; `fields` (`duration_s`, `language`, `no_speech`, `providers`) are recorded too."""
        ...

    def record(self, **fields: Any) -> None:
        """Record job info without a state change (`duration_s`, `language`, `no_speech`, `providers`)."""
        ...

    def add_usage(self, usage: Usage, cost_usd: float | None) -> None:
        """Add one call's usage and estimated cost to the job's totals; `None` makes the total cost unknown."""
        ...

    def check_cancelled(self) -> None:
        """Raise `JobCancelled` if cancellation was requested; call at each stage boundary."""
        ...


class CoreContext(Protocol):
    """What tools may use from core."""

    media: MediaService
    transcriber: TranscriberChain  # the fallback chain, not a single provider
    llm: LLMChain  # the fallback chain
    settings: Settings


class JobAccess(Protocol):
    """What a tool's HTTP routes may use from core, outside the worker (reach it with `job_access(request)`)."""

    @property
    def ctx(self) -> CoreContext: ...

    def get(self, job_id: str) -> Job | None:
        """A read-only snapshot of the job (state, tool id, providers, usage), or None if unknown or expired."""
        ...

    def handle(self, job_id: str) -> JobHandle:
        """A handle to record usage/providers on an existing job (usable from worker threads).

        Its methods raise `JobNotFound` if the job was deleted meanwhile (e.g. purged at the end of its TTL).
        """
        ...


def job_access(request: Request) -> JobAccess:
    """The running app's `JobAccess` (available once the app has started)."""
    access: JobAccess = request.app.state.job_access
    return access


@runtime_checkable
class Tool(Protocol):
    id: str
    name: str
    description: str
    router: APIRouter

    async def run(self, job: JobHandle, ctx: CoreContext) -> None: ...


def discover_tools(package: str = "videokit.tools") -> dict[str, Tool]:
    """Import every subpackage of `package` exposing a module-level `TOOL`; return them by id."""
    root = importlib.import_module(package)
    tools: dict[str, Tool] = {}
    for info in pkgutil.iter_modules(root.__path__):
        if not info.ispkg:
            continue
        module = importlib.import_module(f"{package}.{info.name}")
        if not hasattr(module, "TOOL"):
            continue
        tool = module.TOOL
        if not isinstance(tool, Tool):
            raise RegistryError(f"{module.__name__}.TOOL is not a Tool (needs id, name, description, router, run)")
        if not isinstance(tool.id, str) or not TOOL_ID_PATTERN.fullmatch(tool.id):
            raise RegistryError(
                f"Invalid tool id {tool.id!r} in {module.__name__}; must match {TOOL_ID_PATTERN.pattern}"
            )
        if tool.id in tools:
            raise RegistryError(f"Duplicate tool id {tool.id!r} in {module.__name__}")
        tools[tool.id] = tool
    return tools
