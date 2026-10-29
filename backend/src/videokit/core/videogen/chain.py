"""Ordered video-generation fallback that only ever falls back before a request was accepted (research V3, V10)."""

import logging
from collections.abc import Sequence
from pathlib import Path

from videokit.core.videogen.base import (
    Accepted,
    Capabilities,
    PollResult,
    ProviderStatus,
    VideoError,
    VideoGenerator,
    VideoRequest,
)

logger = logging.getLogger("videokit")

# The request itself is at fault, so another provider would fail the same way (or charge for it again).
_STOPS_THE_CHAIN = frozenset({"content_blocked", "invalid_picture"})


class VideoChain:
    """Tries providers in configured order, but only until one accepts the request.

    Generation is paid and asynchronous: once a provider accepted, a failure is final (a second provider would
    charge again), so ``poll``, ``cancel`` and ``forget`` go to the provider named in ``Accepted.provider`` and
    never to another.
    """

    def __init__(self, providers: Sequence[VideoGenerator]) -> None:
        if not providers:
            raise ValueError("at least one video provider is required")
        self.providers = list(providers)
        self.name = ",".join(p.name for p in self.providers)
        self.model = self.providers[0].model

    def check(self) -> ProviderStatus:
        statuses = [(p.name, p.check()) for p in self.providers]
        return ProviderStatus(
            ready=any(s.ready for _, s in statuses),
            detail="; ".join(f"{name}: {s.detail}" for name, s in statuses),
        )

    def capabilities(self) -> Capabilities:
        """The first provider's: it is the one tried first, and the form offers what it can do."""
        return self.providers[0].capabilities()

    def submit(self, req: VideoRequest) -> Accepted:
        last: VideoError | None = None
        for provider in self.providers:
            try:
                ref = provider.submit(req)
            except VideoError as exc:
                last = exc
                # Log the error type and code only: messages may echo request details.
                logger.warning("Video provider %s failed (%s: %s)", provider.name, type(exc).__name__, exc.code)
                if exc.code in _STOPS_THE_CHAIN:
                    raise
            except Exception as exc:  # an adapter bug before acceptance counts as the provider being unavailable
                last = VideoError("provider_unavailable")
                logger.warning("Video provider %s failed (%s)", provider.name, type(exc).__name__)
            else:
                return Accepted(provider=_label(provider), ref=ref)
        assert last is not None  # the chain is never empty
        raise last

    def poll(self, accepted: Accepted, out_dir: Path) -> PollResult:
        provider = self._accepting(accepted)
        if provider is None:
            logger.warning("Video provider %s is no longer configured", accepted.provider)
            raise VideoError("video_failed")
        return provider.poll(accepted.ref, out_dir)

    def cancel(self, accepted: Accepted) -> None:
        self._best_effort("cancel", accepted)

    def forget(self, accepted: Accepted) -> None:
        self._best_effort("forget", accepted)

    def _accepting(self, accepted: Accepted) -> VideoGenerator | None:
        return next((p for p in self.providers if _label(p) == accepted.provider), None)

    def _best_effort(self, operation: str, accepted: Accepted) -> None:
        provider = self._accepting(accepted)
        if provider is None:
            return
        try:
            getattr(provider, operation)(accepted.ref)
        except Exception as exc:  # cancelling or forgetting must never fail the job
            logger.warning("Video provider %s %s failed (%s)", provider.name, operation, type(exc).__name__)


def _label(provider: VideoGenerator) -> str:
    return f"{provider.name}:{provider.model}"
