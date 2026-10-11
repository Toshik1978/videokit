"""Provider chains in configured order, their startup checks and readiness status (FR-010, FR-012, FR-013)."""

from collections.abc import Callable
from typing import Any

from videokit.core.config import Settings
from videokit.core.llm import AnthropicProvider, LLMChain, LLMProvider, OpenAIProvider
from videokit.core.transcribe import LocalWhisperTranscriber, OpenAITranscriber, Transcriber, TranscriberChain
from videokit.core.worker import Providers

TRANSCRIBERS: dict[str, Callable[[Settings], Transcriber]] = {
    "local": LocalWhisperTranscriber,
    "openai": OpenAITranscriber,
}
LLMS: dict[str, Callable[[Settings], LLMProvider]] = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
}
_ORDER_VARS = {"transcription": "TRANSCRIBE_PROVIDERS", "llm": "LLM_PROVIDERS"}

ProviderStatusView = dict[str, list[dict[str, Any]]]


def build(settings: Settings) -> Providers:
    """Chains in `TRANSCRIBE_PROVIDERS` / `LLM_PROVIDERS` order. Expects validated settings (known names, non-empty).

    Constructors are cheap: no model is loaded, no SDK client is created and nothing goes over the network.
    """
    return Providers(
        transcriber=TranscriberChain([TRANSCRIBERS[name](settings) for name in settings.transcribe_providers]),
        llm=LLMChain([LLMS[name](settings) for name in settings.llm_providers]),
    )


def status(providers: Providers) -> ProviderStatusView:
    """Each provider's current `check()`, in chain order: the `providers` object of `/api/health`.

    Checks make no paid calls; the local transcriber reports its model loading state, so this is live.
    """

    def entries(chain: TranscriberChain | LLMChain) -> list[dict[str, Any]]:
        out = []
        for provider in chain.providers:
            result = provider.check()
            out.append({"name": provider.name, "ready": result.ready, "detail": result.detail})
        return out

    return {"transcription": entries(providers.transcriber), "llm": entries(providers.llm)}


def problems(view: ProviderStatusView) -> list[str]:
    """One line per provider that is not ready, naming it and its variable (FR-012)."""
    return [
        f"{kind} provider '{entry['name']}' ({_ORDER_VARS[kind]}) is not ready: {entry['detail']}"
        for kind, entries in view.items()
        for entry in entries
        if not entry["ready"]
    ]
