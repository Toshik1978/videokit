"""Cost estimation (research R11, FR-032).

Token prices are $ per million tokens (``input``, ``output``, ``cache_read``, ``cache_write``, ``video_output``);
transcription is priced per audio minute (``per_minute``). ``PRICING_OVERRIDES`` is merged per model and per field.
"""

from collections.abc import Mapping

from videokit.core.llm.base import Usage
from videokit.core.transcribe.base import Transcript

PriceTable = dict[str, dict[str, float]]

DEFAULT_PRICES: PriceTable = {
    "claude-opus-5-5": {"input": 4.0, "output": 20.0, "cache_read": 0.20, "cache_write": 5.00},
    "claude-sonnet-5-5": {"input": 2.0, "output": 10.0, "cache_read": 0.10, "cache_write": 2.50},
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50, "cache_read": 0.01, "cache_write": 0.125},
    # Fallback models that `fallbacks: "default"` may route a refused request to (reported in message.model).
    "claude-opus-5": {"input": 5.0, "output": 25.0, "cache_read": 0.50, "cache_write": 6.25},
    "claude-opus-4-8": {"input": 5.0, "output": 25.0, "cache_read": 0.50, "cache_write": 6.25},
    "whisper-1": {"per_minute": 0.006},
    # Verified against the official Gemini API pricing page (research V8): video output is billed per token too.
    "gemini-omni-1.1-flash": {"input": 1.50, "output": 9.00, "video_output": 17.50},
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
        + usage.video_output_tokens * prices.get("video_output", 0.0)
    ) / 1_000_000
    return round(tokens + usage.audio_minutes * prices.get("per_minute", 0.0), 6)


def transcription_cost(
    transcript: Transcript, overrides: Mapping[str, Mapping[str, float]] | None = None
) -> float | None:
    """Cost of producing ``transcript``: 0 when self-hosted, else its model's per-minute price (or ``None``).

    The address decides who pays (research T8), not the model id: a self-hosted server may call its model ``whisper-1``.
    """
    if transcript.self_hosted:
        return 0.0
    return estimate(Usage(audio_minutes=transcript.audio_minutes), transcript.model, overrides)
