"""Provider chains in configured order, their startup checks and readiness status (FR-010, FR-012, FR-013)."""

from collections.abc import Callable
from typing import Any

from videokit.core.config import ConfigurationError, Settings
from videokit.core.llm import AnthropicProvider, LLMChain, LLMProvider, OpenAIProvider
from videokit.core.transcribe import LocalWhisperTranscriber, OpenAITranscriber, Transcriber, TranscriberChain
from videokit.core.videogen import VideoChain, VideoGenerator
from videokit.core.worker import Providers

TRANSCRIBERS: dict[str, Callable[[Settings], Transcriber]] = {
    "local": LocalWhisperTranscriber,
    "openai": OpenAITranscriber,
}
LLMS: dict[str, Callable[[Settings], LLMProvider]] = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
}
VIDEOS: dict[str, Callable[[Settings], VideoGenerator]] = {}  # "omni" is registered with its adapter
_ORDER_VARS = {"transcription": "TRANSCRIBE_PROVIDERS", "llm": "LLM_PROVIDERS", "video": "VIDEO_PROVIDERS"}

ProviderStatusView = dict[str, list[dict[str, Any]]]


def build(settings: Settings) -> Providers:
    """Chains in `TRANSCRIBE_PROVIDERS` / `LLM_PROVIDERS` / `VIDEO_PROVIDERS` order. Expects validated settings.

    The video chain is `None` when `VIDEO_PROVIDERS` is empty (the Video Generation tool is then hidden). A listed
    video provider with no registered adapter raises `ConfigurationError` (one safe line, no value echoed).

    Constructors are cheap: no model is loaded, no SDK client is created and nothing goes over the network.
    """
    if any(name not in VIDEOS for name in settings.video_providers):
        raise ConfigurationError(["VIDEO_PROVIDERS lists a provider that is not available"])
    video = [VIDEOS[name](settings) for name in settings.video_providers]
    return Providers(
        transcriber=TranscriberChain([TRANSCRIBERS[name](settings) for name in settings.transcribe_providers]),
        llm=LLMChain([LLMS[name](settings) for name in settings.llm_providers]),
        video=VideoChain(video) if video else None,
    )


def status(providers: Providers) -> ProviderStatusView:
    """Each provider's current `check()`, in chain order: the `providers` object of `/api/health`.

    Checks make no paid calls; the local transcriber reports its model loading state, so this is live.
    """

    def entries(chain: TranscriberChain | LLMChain | VideoChain | None) -> list[dict[str, Any]]:
        out = []
        for provider in chain.providers if chain is not None else []:
            result = provider.check()
            out.append({"name": provider.name, "ready": result.ready, "detail": result.detail})
        return out

    return {
        "transcription": entries(providers.transcriber),
        "llm": entries(providers.llm),
        "video": entries(providers.video),
    }


def problems(view: ProviderStatusView) -> list[str]:
    """One line per provider that is not ready, naming it and its variable (FR-012)."""
    return [
        f"{kind} provider '{entry['name']}' ({_ORDER_VARS[kind]}) is not ready: {entry['detail']}"
        for kind, entries in view.items()
        for entry in entries
        if not entry["ready"]
    ]
