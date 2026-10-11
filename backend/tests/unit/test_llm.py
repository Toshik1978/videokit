import base64
from typing import Any
from unittest.mock import MagicMock

import anthropic
import httpx2
import pytest
from anthropic.types.beta import BetaMessage
from pydantic import SecretStr

from fakes_llm import FakeLLM
from videokit.core.config import Settings
from videokit.core.llm import (
    AllProvidersFailed,
    AnthropicProvider,
    GenerationRequest,
    GenerationResponse,
    LabeledFrame,
    LLMChain,
    LLMProvider,
    ProviderError,
    ProviderStatus,
    Turn,
    Usage,
    format_timestamp,
)
from videokit.core.llm import anthropic as anthropic_module

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"title": {"type": "string"}},
    "required": ["title"],
    "additionalProperties": False,
}


def _request(**overrides: Any) -> GenerationRequest:
    values: dict[str, Any] = {
        "system": "You write YouTube metadata.",
        "context_text": "[0:00] Welcome\n[0:04] The lobby",
        "frames": [LabeledFrame(0.0, b"\xff\xd8jpeg-0"), LabeledFrame(65.4, b"\xff\xd8jpeg-1")],
        "schema": SCHEMA,
        "turns": [Turn("user", "Generate the metadata.")],
    }
    values.update(overrides)
    return GenerationRequest(**values)


# --- base types --------------------------------------------------------------------------------


@pytest.mark.parametrize(("seconds", "text"), [(0, "0:00"), (5.9, "0:05"), (65.4, "1:05"), (3725, "1:02:05")])
def test_format_timestamp(seconds: float, text: str) -> None:
    assert format_timestamp(seconds) == text


def test_usage_adds_up() -> None:
    total = Usage(input_tokens=1, output_tokens=2, cache_read_tokens=3, cache_write_tokens=4) + Usage(
        input_tokens=10, output_tokens=20, cache_read_tokens=30, cache_write_tokens=40, audio_minutes=1.5
    )
    assert total == Usage(11, 22, 33, 44, 1.5)
    assert Usage() == Usage(0, 0, 0, 0, 0.0)


def test_response_model_comes_from_provider() -> None:
    assert GenerationResponse({}, Usage(), "anthropic:claude-opus-5-5").model == "claude-opus-5-5"


# --- FakeLLM -----------------------------------------------------------------------------------


def test_fake_llm_replays_script_and_records_requests() -> None:
    usage = Usage(input_tokens=100, output_tokens=10)
    fake = FakeLLM([{"title": "ok"}, {"title": ""}, ProviderError("rate limited")], usage=usage)
    assert isinstance(fake, LLMProvider)
    first, second = _request(), _request(turns=[Turn("user", "again")])

    assert fake.generate(first) == GenerationResponse({"title": "ok"}, usage, "fake:fake-model")
    assert fake.generate(second).data == {"title": ""}  # schema-invalid outputs are scripted like any other
    with pytest.raises(ProviderError):
        fake.generate(first)
    assert fake.requests == [first, second, first]
    with pytest.raises(AssertionError):
        fake.generate(first)  # script exhausted


def test_fake_llm_accepts_full_responses() -> None:
    response = GenerationResponse({"title": "x"}, Usage(cache_read_tokens=5), "openai:some-model")
    assert FakeLLM([response]).generate(_request()) is response
    assert FakeLLM([], name="openai", model="m").check() == ProviderStatus(ready=True, detail="m")


# --- chain -------------------------------------------------------------------------------------


def test_chain_falls_back_and_records_provider() -> None:
    first = FakeLLM([ProviderError("overloaded")], name="anthropic", model="claude-opus-5-5")
    second = FakeLLM([{"title": "ok"}], name="openai", model="m")
    chain = LLMChain([first, second])
    assert isinstance(chain, LLMProvider)
    assert chain.name == "anthropic,openai"

    response = chain.generate(_request())

    assert response.provider == "openai:m"
    assert len(first.requests) == 1


def test_chain_raises_all_providers_failed() -> None:
    chain = LLMChain([FakeLLM([RuntimeError("x")]), FakeLLM([ProviderError("y")])])
    with pytest.raises(AllProvidersFailed) as info:
        chain.generate(_request())
    assert info.value.stage == "generation"


def test_chain_requires_providers_and_reports_status() -> None:
    with pytest.raises(ValueError):
        LLMChain([])
    chain = LLMChain([FakeLLM([], name="anthropic", model="a")])
    assert chain.model == "a"
    assert chain.check() == ProviderStatus(ready=True, detail="anthropic: a")


# --- Anthropic ---------------------------------------------------------------------------------


def _message(
    text: str = '{"title": "Hidden Gem"}', stop_reason: str = "end_turn", model: str = "claude-opus-5-5"
) -> BetaMessage:
    return BetaMessage.model_validate(
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [{"type": "thinking", "thinking": "", "signature": "sig"}, {"type": "text", "text": text}],
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {
                "input_tokens": 120,
                "output_tokens": 45,
                "cache_read_input_tokens": 3000,
                "cache_creation_input_tokens": 700,
            },
        }
    )


def _client(message: BetaMessage | None = None, error: Exception | None = None) -> MagicMock:
    client = MagicMock()
    stream = client.beta.messages.stream
    if error is not None:
        stream.side_effect = error
    else:
        stream.return_value.__enter__.return_value.get_final_message.return_value = message or _message()
    return client


def _provider(client: MagicMock, **settings: Any) -> AnthropicProvider:
    values: dict[str, Any] = {"anthropic_api_key": "sk-test", "anthropic_model": "claude-opus-5-5"}
    values.update(settings)
    return AnthropicProvider(Settings(**values), client=client)


def _sent(client: MagicMock) -> dict[str, Any]:
    (call,) = client.beta.messages.stream.call_args_list
    assert call.args == ()
    return dict(call.kwargs)


def _image(data: bytes) -> dict[str, Any]:
    source = {"type": "base64", "media_type": "image/jpeg", "data": base64.standard_b64encode(data).decode("ascii")}
    return {"type": "image", "source": source}


def test_anthropic_request_shape() -> None:
    client = _client()
    _provider(client, anthropic_effort="high").generate(_request())

    sent = _sent(client)
    assert sent["model"] == "claude-opus-5-5"
    assert sent["max_tokens"] == anthropic_module.MAX_TOKENS
    assert sent["system"] == "You write YouTube metadata."
    assert sent["thinking"] == {"type": "adaptive"}
    assert sent["output_config"] == {"effort": "high", "format": {"type": "json_schema", "schema": SCHEMA}}
    assert sent["betas"] == ["server-side-fallback-2026-07-01"]
    assert sent["fallbacks"] == "default"
    last_frame = _image(b"\xff\xd8jpeg-1") | {"cache_control": {"type": "ephemeral"}}
    assert sent["messages"] == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "[0:00] Welcome\n[0:04] The lobby"},
                {"type": "text", "text": "[frame 0:00]"},
                _image(b"\xff\xd8jpeg-0"),
                {"type": "text", "text": "[frame 1:05]"},
                last_frame,
                {"type": "text", "text": "Generate the metadata."},
            ],
        }
    ]


def test_anthropic_follow_up_turns_keep_the_cached_prefix() -> None:
    client = _client()
    turns = [Turn("user", "Generate."), Turn("assistant", '{"title": ""}'), Turn("user", "Fix: title is empty.")]
    _provider(client).generate(_request(turns=turns))

    messages = _sent(client)["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    prefix = messages[0]["content"]
    assert [i for i, block in enumerate(prefix) if "cache_control" in block] == [len(prefix) - 2]
    assert prefix[-1] == {"type": "text", "text": "Generate."}
    assert messages[1]["content"] == [{"type": "text", "text": '{"title": ""}'}]
    assert messages[2]["content"] == [{"type": "text", "text": "Fix: title is empty."}]


def test_anthropic_without_frames_caches_the_transcript() -> None:
    client = _client()
    _provider(client).generate(_request(frames=[]))
    content = _sent(client)["messages"][0]["content"]
    assert content[0] == {
        "type": "text",
        "text": "[0:00] Welcome\n[0:04] The lobby",
        "cache_control": {"type": "ephemeral"},
    }


def test_anthropic_without_transcript_sends_only_frames() -> None:
    client = _client()
    _provider(client).generate(_request(context_text="", frames=[LabeledFrame(3.0, b"x")]))
    content = _sent(client)["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "[frame 0:03]"}
    assert content[1]["cache_control"] == {"type": "ephemeral"}


def test_anthropic_response_parsing() -> None:
    client = _client(_message(model="claude-opus-5-5"))
    response = _provider(client).generate(_request())
    assert response == GenerationResponse(
        data={"title": "Hidden Gem"},
        usage=Usage(input_tokens=120, output_tokens=45, cache_read_tokens=3000, cache_write_tokens=700),
        provider="anthropic:claude-opus-5-5",
    )


@pytest.mark.parametrize(
    "message",
    [
        _message(stop_reason="refusal"),
        _message(stop_reason="max_tokens"),
        _message(text="not json"),
        _message(text="[1, 2]"),
    ],
)
def test_anthropic_unusable_responses_are_provider_errors(message: BetaMessage) -> None:
    with pytest.raises(ProviderError):
        _provider(_client(message)).generate(_request())


def test_anthropic_api_errors_are_provider_errors() -> None:
    error = anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.example.com/v1/messages"))
    with pytest.raises(ProviderError) as info:
        _provider(_client(error=error)).generate(_request())
    assert info.value.__cause__ is error


def test_anthropic_check() -> None:
    assert _provider(_client()).check() == ProviderStatus(ready=True, detail="claude-opus-5-5")
    assert _provider(_client(), anthropic_api_key=None).check().ready is False


def test_anthropic_builds_sdk_client_lazily(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    factory = MagicMock(return_value=client)
    monkeypatch.setattr(anthropic, "Anthropic", factory)
    provider = AnthropicProvider(Settings(anthropic_api_key=SecretStr("sk-test")))
    assert provider.name == "anthropic"
    assert isinstance(provider, LLMProvider)
    factory.assert_not_called()

    provider.generate(_request())
    provider.generate(_request())

    factory.assert_called_once_with(api_key="sk-test")
