"""Ordered transcription fallback (FR-010)."""

import logging
from collections.abc import Sequence

from videokit.core.transcribe.base import AllProvidersFailed, AudioFiles, ProviderStatus, Transcriber, Transcript

logger = logging.getLogger("videokit")


class TranscriberChain:
    """Tries providers in configured order; the returned transcript names the provider that produced it."""

    def __init__(self, providers: Sequence[Transcriber]) -> None:
        if not providers:
            raise ValueError("at least one transcription provider is required")
        self.providers = list(providers)
        self.name = ",".join(p.name for p in self.providers)

    def check(self) -> ProviderStatus:
        statuses = [(p.name, p.check()) for p in self.providers]
        return ProviderStatus(
            ready=any(s.ready for _, s in statuses),
            detail="; ".join(f"{name}: {s.detail}" for name, s in statuses),
        )

    def transcribe(self, audio: AudioFiles) -> Transcript:
        for provider in self.providers:
            try:
                return provider.transcribe(audio)
            except Exception as exc:  # any provider failure means: try the next one
                # Log error types only (the wrapper and its cause): messages may echo addresses, keys or text.
                cause = exc.__cause__
                kind = type(exc).__name__ + (f": {type(cause).__name__}" if cause is not None else "")
                logger.warning("Transcription provider %s failed (%s)", provider.name, kind)
        raise AllProvidersFailed("transcription")
