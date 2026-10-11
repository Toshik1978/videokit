"""Tool registry: the seam between core and tools (see contracts/internal-interfaces.md).

`core` never imports concrete tools; tools are discovered by importing the subpackages of a
package (default `videokit.tools`) that expose a module-level `TOOL`.
"""

import importlib
import pkgutil
import re
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from fastapi import APIRouter

if TYPE_CHECKING:
    # Tightened by later tasks once these types exist.
    Settings = Any
    MediaService = Any
    Transcriber = Any
    LLMClient = Any

TOOL_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


class JobCancelled(Exception):
    """Raised by `JobHandle.check_cancelled()` when cancellation was requested for the job."""


class RegistryError(Exception):
    """A tool failed validation during discovery."""


class JobHandle(Protocol):
    """What a tool's `run` may do to the job it is processing."""

    def set_state(self, state: str, **fields: Any) -> None: ...

    def check_cancelled(self) -> None:
        """Raise `JobCancelled` if cancellation was requested; call at each stage boundary."""
        ...


class CoreContext(Protocol):
    """What tools may use from core."""

    media: "MediaService"
    transcriber: "Transcriber"
    llm: "LLMClient"
    settings: "Settings"


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
