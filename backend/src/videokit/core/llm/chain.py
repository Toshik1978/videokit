"""Ordered text-generation fallback (FR-010)."""

import logging
from collections.abc import Sequence

from videokit.core.llm.base import (
    AllProvidersFailed,
    GenerationRequest,
    GenerationResponse,
    LLMProvider,
    ProviderStatus,
)

logger = logging.getLogger("videokit")


class LLMChain:
    """Tries providers in configured order.

    SDK clients retry 429/5xx themselves; any error that reaches the chain (retries exhausted or not retryable)
    moves on to the next provider. ``GenerationResponse.provider`` names the provider that answered.
    """

    def __init__(self, providers: Sequence[LLMProvider]) -> None:
        if not providers:
            raise ValueError("at least one LLM provider is required")
        self.providers = list(providers)
        self.name = ",".join(p.name for p in self.providers)
        self.model = self.providers[0].model

    def check(self) -> ProviderStatus:
        statuses = [(p.name, p.check()) for p in self.providers]
        return ProviderStatus(
            ready=any(s.ready for _, s in statuses),
            detail="; ".join(f"{name}: {s.detail}" for name, s in statuses),
        )

    def generate(self, req: GenerationRequest) -> GenerationResponse:
        for provider in self.providers:
            try:
                return provider.generate(req)
            except Exception as exc:  # any provider failure means: try the next one
                # Log the error type only: messages may echo request details.
                logger.warning("LLM provider %s failed (%s)", provider.name, type(exc).__name__)
        raise AllProvidersFailed("generation")
