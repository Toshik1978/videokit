import base64
from typing import Any
from unittest.mock import MagicMock

import httpx2
import openai
import pytest
from openai.types.responses import Response
from pydantic import SecretStr

from videokit.core.config import Settings
from videokit.core.llm import (
    GenerationRequest,
    GenerationResponse,
    LabeledFrame,
    LLMProvider,
    OpenAIProvider,
    ProviderError,
    ProviderStatus,
    Turn,
    Usage,
)
from videokit.core.llm import openai as openai_module

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


def _response(
    text: str = '{"title": "Hidden Gem"}',
    status: str = "completed",
    incomplete_reason: str | None = None,
    content: list[dict[str, Any]] | None = None,
    model: str = "gpt-test-2026-01-01",
) -> Response:
    return Response.model_validate(
        {
            "id": "resp_1",
            "object": "response",
            "created_at": 0,
            "model": model,
            "status": status,
            "incomplete_details": {"reason": incomplete_reason} if incomplete_reason else None,
            "output": [
                {"type": "reasoning", "id": "rs_1", "summary": []},
                {
                    "type": "message",
                    "id": "msg_1",
                    "role": "assistant",
                    "status": "completed",
                    "content": content or [{"type": "output_text", "text": text, "annotations": []}],
                },
            ],
            "parallel_tool_calls": False,
            "tool_choice": "auto",
            "tools": [],
            "usage": {
                "input_tokens": 3120,
                "input_tokens_details": {"cached_tokens": 3000, "cache_write_tokens": 0},
                "output_tokens": 45,
                "output_tokens_details": {"reasoning_tokens": 10},
                "total_tokens": 3165,
            },
        }
    )


def _client(response: Response | None = None, error: Exception | None = None) -> MagicMock:
    client = MagicMock()
    if error is not None:
        client.responses.create.side_effect = error
    else:
        client.responses.create.return_value = response or _response()
    return client


def _provider(client: MagicMock, **settings: Any) -> OpenAIProvider:
    values: dict[str, Any] = {"openai_api_key": SecretStr("test-key"), "openai_model": "gpt-test"}
    values.update(settings)
    return OpenAIProvider(Settings(**values), client=client)


def _sent(client: MagicMock) -> dict[str, Any]:
    (call,) = client.responses.create.call_args_list
    assert call.args == ()
    return dict(call.kwargs)


def _image(data: bytes) -> dict[str, Any]:
    url = "data:image/jpeg;base64," + base64.standard_b64encode(data).decode("ascii")
    return {"type": "input_image", "image_url": url, "detail": "auto"}


def _text(text: str) -> dict[str, str]:
    return {"type": "input_text", "text": text}


def test_is_an_llm_provider() -> None:
    provider = _provider(MagicMock())
    assert isinstance(provider, LLMProvider)
    assert provider.name == "openai"
    assert provider.model == "gpt-test"


def test_check_requires_key_and_model() -> None:
    assert _provider(MagicMock()).check() == ProviderStatus(ready=True, detail="model=gpt-test")
    missing_key = ProviderStatus(ready=False, detail="missing OPENAI_API_KEY")
    assert _provider(MagicMock(), openai_api_key=None).check() == missing_key
    assert _provider(MagicMock(), openai_api_key=SecretStr(" ")).check() == missing_key
    missing_model = ProviderStatus(ready=False, detail="missing OPENAI_MODEL")
    assert _provider(MagicMock(), openai_model=None).check() == missing_model
    assert _provider(MagicMock(), openai_model="  ").check() == missing_model


def test_request_shape() -> None:
    client = _client()
    _provider(client).generate(_request())

    sent = _sent(client)
    assert sent["model"] == "gpt-test"
    assert sent["instructions"] == "You write YouTube metadata."
    assert sent["store"] is False
    assert sent["text"] == {
        "format": {"type": "json_schema", "name": "structured_output", "schema": SCHEMA, "strict": True}
    }
    assert sent["input"] == [
        {
            "role": "user",
            "content": [
                _text("[0:00] Welcome\n[0:04] The lobby"),
                _text("[frame 0:00]"),
                _image(b"\xff\xd8jpeg-0"),
                _text("[frame 1:05]"),
                _image(b"\xff\xd8jpeg-1"),
                _text("Generate the metadata."),
            ],
        }
    ]


def test_turns_follow_the_stable_prefix() -> None:
    client = _client()
    turns = [
        Turn("user", "Generate."),
        Turn("assistant", '{"title": "Old"}'),
        Turn("user", "Fix the title."),
        Turn("user", "Make it shorter."),
    ]
    _provider(client).generate(_request(frames=[], turns=turns))

    assert _sent(client)["input"] == [
        {"role": "user", "content": [_text("[0:00] Welcome\n[0:04] The lobby"), _text("Generate.")]},
        {"role": "assistant", "content": '{"title": "Old"}'},
        {"role": "user", "content": [_text("Fix the title."), _text("Make it shorter.")]},
    ]


def test_prefix_stays_its_own_message_when_first_turn_is_assistant() -> None:
    client = _client()
    _provider(client).generate(_request(frames=[], turns=[Turn("assistant", "prior"), Turn("user", "go")]))

    assert _sent(client)["input"] == [
        {"role": "user", "content": [_text("[0:00] Welcome\n[0:04] The lobby")]},
        {"role": "assistant", "content": "prior"},
        {"role": "user", "content": [_text("go")]},
    ]


def test_empty_prefix_is_omitted() -> None:
    client = _client()
    _provider(client).generate(_request(context_text="", frames=[], turns=[Turn("user", "go")]))
    assert _sent(client)["input"] == [{"role": "user", "content": [_text("go")]}]


def test_parses_response_and_usage() -> None:
    result = _provider(_client()).generate(_request())

    assert result == GenerationResponse(
        data={"title": "Hidden Gem"},
        # input_tokens excludes the cached part, like the Anthropic provider, so pricing can add both.
        usage=Usage(input_tokens=120, output_tokens=45, cache_read_tokens=3000, cache_write_tokens=0),
        provider="openai:gpt-test-2026-01-01",
    )


def test_missing_usage_is_zero() -> None:
    response = _response()
    response.usage = None
    assert _provider(_client(response)).generate(_request()).usage == Usage()


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (_response(status="incomplete", incomplete_reason="max_output_tokens"), "incomplete.*max_output_tokens"),
        (_response(status="incomplete"), "incomplete"),
        (_response(status="failed"), "failed"),
        (_response(content=[{"type": "refusal", "refusal": "I cannot help with that."}]), "refus"),
        (_response(text="not json"), "valid JSON"),
        (_response(text="[1, 2]"), "JSON object"),
        (_response(text=""), "valid JSON"),
    ],
)
def test_bad_responses_become_provider_errors(response: Response, message: str) -> None:
    with pytest.raises(ProviderError, match=message) as exc:
        _provider(_client(response)).generate(_request())
    assert "cannot help" not in str(exc.value)  # model text is not echoed into errors


def test_sdk_errors_become_provider_errors() -> None:
    request = httpx2.Request("POST", "https://api.example.com/v1/responses")
    error = openai.APIConnectionError(request=request)
    with pytest.raises(ProviderError, match="APIConnectionError"):
        _provider(_client(error=error)).generate(_request())


def test_lazy_client_uses_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[dict[str, Any]] = []

    class FakeOpenAI:
        def __init__(self, **kwargs: Any) -> None:
            created.append(kwargs)

    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    provider = OpenAIProvider(Settings(openai_api_key=SecretStr("test-key"), openai_model="m"))
    assert created == []
    assert provider._sdk() is provider._sdk()
    assert created == [{"api_key": "test-key", "base_url": "https://api.openai.com/v1"}]
    assert openai_module.SCHEMA_NAME == "structured_output"


def test_openai_base_url_env_never_redirects_the_llm_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "http://llm.example.com:8000/v1")
    provider = OpenAIProvider(Settings(openai_api_key=SecretStr("test-key"), openai_model="m"))
    assert str(provider._sdk().base_url) == "https://api.openai.com/v1/"
