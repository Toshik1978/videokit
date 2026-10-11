"""Cost estimation (research R11, FR-032).

Token prices are $ per million tokens (``input``, ``output``, ``cache_read``, ``cache_write``); transcription is
priced per audio minute (``per_minute``). ``PRICING_OVERRIDES`` is merged per model and per field.
"""

from collections.abc import Mapping

from videokit.core.llm.base import Usage
from videokit.core.transcribe.base import Transcript

PriceTable = dict[str, dict[str, float]]
FREE_TRANSCRIPTION_PROVIDERS = frozenset({"local"})  # run on this machine: cost 0 (cost ruling on T020)

DEFAULT_PRICES: PriceTable = {
    "claude-opus-5-5": {"input": 4.0, "output": 20.0, "cache_read": 0.20, "cache_write": 5.00},
    "claude-sonnet-5-5": {"input": 2.0, "output": 10.0, "cache_read": 0.20, "cache_write": 2.50},
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50, "cache_read": 0.01, "cache_write": 0.125},
    "whisper-1": {"per_minute": 0.006},
}


def price_table(overrides: Mapping[str, Mapping[str, float]] | None = None) -> PriceTable:
    """Defaults with ``overrides`` merged in; overrides may change single fields or add models."""
    table = {model: dict(prices) for model, prices in DEFAULT_PRICES.items()}
    for model, prices in (overrides or {}).items():
        table.setdefault(model, {}).update(prices)
    return table


def estimate(usage: Usage, model: str, overrides: Mapping[str, Mapping[str, float]] | None = None) -> float | None:
    """Estimated cost in USD of ``usage`` on ``model``, or ``None`` when the model has no known price."""
    prices = price_table(overrides).get(model)
    if prices is None:
        return None
    tokens = (
        usage.input_tokens * prices.get("input", 0.0)
        + usage.output_tokens * prices.get("output", 0.0)
        + usage.cache_read_tokens * prices.get("cache_read", 0.0)
        + usage.cache_write_tokens * prices.get("cache_write", 0.0)
    ) / 1_000_000
    return round(tokens + usage.audio_minutes * prices.get("per_minute", 0.0), 6)


def transcription_cost(
    transcript: Transcript, overrides: Mapping[str, Mapping[str, float]] | None = None
) -> float | None:
    """Cost of producing ``transcript``: 0 for local transcription, else its model's per-minute price (or ``None``)."""
    if transcript.provider in FREE_TRANSCRIPTION_PROVIDERS:
        return 0.0
    return estimate(Usage(audio_minutes=transcript.audio_minutes), transcript.model, overrides)
