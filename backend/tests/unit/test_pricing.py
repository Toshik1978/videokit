import pytest

from videokit.core import pricing
from videokit.core.llm import Usage
from videokit.core.pricing import DEFAULT_PRICES, estimate, price_table, transcription_cost
from videokit.core.transcribe import Transcript

USAGE = Usage(input_tokens=1_000_000, output_tokens=100_000, cache_read_tokens=2_000_000, cache_write_tokens=400_000)


def test_default_prices() -> None:
    assert DEFAULT_PRICES == {
        "claude-opus-5-5": {"input": 4.0, "output": 20.0, "cache_read": 0.20, "cache_write": 5.00},
        "claude-sonnet-5-5": {"input": 2.0, "output": 10.0, "cache_read": 0.10, "cache_write": 2.50},
        "claude-haiku-5-5": {"input": 0.10, "output": 0.50, "cache_read": 0.01, "cache_write": 0.125},
        # Fallback models that `fallbacks: "default"` may route a refused request to (reported in message.model).
        "claude-opus-5": {"input": 5.0, "output": 25.0, "cache_read": 0.50, "cache_write": 6.25},
        "claude-opus-4-8": {"input": 5.0, "output": 25.0, "cache_read": 0.50, "cache_write": 6.25},
        "whisper-1": {"per_minute": 0.006},
        "gemini-omni-1.1-flash": {"input": 1.50, "output": 9.00, "video_output": 17.50},
    }


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("claude-opus-5-5", 4.0 + 2.0 + 0.4 + 2.0),
        ("claude-sonnet-5-5", 2.0 + 1.0 + 0.2 + 1.0),
        ("claude-opus-5", 5.0 + 2.5 + 1.0 + 2.5),
        ("claude-opus-4-8", 5.0 + 2.5 + 1.0 + 2.5),
        ("claude-haiku-5-5", 0.10 + 0.05 + 0.02 + 0.05),
    ],
)
def test_token_cost(model: str, expected: float) -> None:
    assert estimate(USAGE, model) == pytest.approx(expected)


def test_audio_cost() -> None:
    assert estimate(Usage(audio_minutes=30.0), "whisper-1") == pytest.approx(0.18)


def test_unknown_model_has_no_cost() -> None:
    assert estimate(USAGE, "some-future-model") is None


def test_small_usage_is_rounded_to_micro_dollars() -> None:
    assert estimate(Usage(input_tokens=1, output_tokens=1), "claude-opus-5-5") == 0.000024


def test_overrides_merge_per_field_and_add_models() -> None:
    overrides = {
        "claude-opus-5-5": {"input": 5.0},
        "my-model": {"input": 1.0, "output": 2.0, "cache_read": 0.1, "cache_write": 1.25},
    }
    table = price_table(overrides)
    assert table["claude-opus-5-5"] == {"input": 5.0, "output": 20.0, "cache_read": 0.20, "cache_write": 5.00}
    assert table["claude-sonnet-5-5"] == DEFAULT_PRICES["claude-sonnet-5-5"]
    assert DEFAULT_PRICES["claude-opus-5-5"]["input"] == 4.0  # defaults are not mutated

    assert estimate(Usage(input_tokens=1_000_000), "claude-opus-5-5", overrides) == pytest.approx(5.0)
    assert estimate(USAGE, "my-model", overrides) == pytest.approx(1.0 + 0.2 + 0.2 + 0.5)


def test_partial_override_for_new_model_prices_missing_fields_at_zero() -> None:
    assert estimate(USAGE, "cheap", {"cheap": {"output": 10.0}}) == pytest.approx(1.0)


def test_no_overrides_uses_defaults() -> None:
    assert price_table() == pricing.DEFAULT_PRICES
    assert price_table({}) == pricing.DEFAULT_PRICES


def _transcript(provider: str, model: str, minutes: float = 2.0) -> Transcript:
    return Transcript(
        language="en",
        language_probability=1.0,
        segments=[],
        audio_minutes=minutes,
        no_speech=True,
        provider=provider,
        model=model,
    )


def test_only_self_hosted_transcription_is_free() -> None:
    assert not hasattr(pricing, "FREE_TRANSCRIPTION_PROVIDERS")
    assert transcription_cost(_transcript("local", "large-v3-turbo")) is None


def test_self_hosted_transcription_is_free_whatever_the_model_id() -> None:
    hosted = Transcript(
        language="en",
        language_probability=1.0,
        segments=[],
        audio_minutes=30.0,
        no_speech=True,
        provider="openai",
        model="whisper-1",
        self_hosted=True,
    )
    assert transcription_cost(hosted) == 0.0
    assert transcription_cost(hosted, {"whisper-1": {"per_minute": 0.5}}) == 0.0
    unknown = Transcript(
        language="en",
        language_probability=1.0,
        segments=[],
        audio_minutes=2.0,
        no_speech=True,
        provider="openai",
        model="some-server-model",
        self_hosted=True,
    )
    assert transcription_cost(unknown) == 0.0


def test_openai_transcription_is_still_priced_when_not_self_hosted() -> None:
    cloud = _transcript("openai", "whisper-1")
    assert cloud.self_hosted is False
    assert transcription_cost(cloud) == pytest.approx(2.0 * 0.006)


def test_cloud_transcription_is_priced_per_minute_of_its_model() -> None:
    assert transcription_cost(_transcript("openai", "whisper-1")) == pytest.approx(2.0 * 0.006)
    overrides = {"whisper-1": {"per_minute": 0.01}}
    assert transcription_cost(_transcript("openai", "whisper-1"), overrides) == pytest.approx(0.02)


@pytest.mark.parametrize("model", ["unpriced-model", ""])
def test_unpriced_cloud_transcription_has_unknown_cost(model: str) -> None:
    assert transcription_cost(_transcript("openai", model)) is None


def test_video_output_tokens_are_priced_at_the_video_rate() -> None:
    usage = Usage(input_tokens=2 * 1120, video_output_tokens=8 * 8688)  # 8 s at 1080p, two pictures
    expected = (2 * 1120 * 1.50 + 8 * 8688 * 17.50) / 1_000_000
    assert estimate(usage, "gemini-omni-1.1-flash") == pytest.approx(expected)
    assert estimate(usage, "gemini-omni-1.1-flash") == pytest.approx(1.21968)


def test_text_output_and_video_output_are_priced_separately() -> None:
    usage = Usage(output_tokens=1_000_000, video_output_tokens=1_000_000)
    assert estimate(usage, "gemini-omni-1.1-flash") == pytest.approx(9.0 + 17.5)


def test_video_output_price_can_be_overridden_per_field() -> None:
    overrides = {"gemini-omni-1.1-flash": {"video_output": 20.0}}
    table = price_table(overrides)
    assert table["gemini-omni-1.1-flash"] == {"input": 1.50, "output": 9.00, "video_output": 20.0}
    assert DEFAULT_PRICES["gemini-omni-1.1-flash"]["video_output"] == 17.50  # defaults are not mutated
    assert estimate(Usage(video_output_tokens=1_000_000), "gemini-omni-1.1-flash", overrides) == pytest.approx(20.0)


def test_models_without_a_video_price_charge_nothing_for_video_tokens() -> None:
    assert estimate(Usage(video_output_tokens=1_000_000), "claude-opus-5-5") == 0.0
