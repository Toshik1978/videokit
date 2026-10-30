"""OpenAI Responses API provider (research R9)."""

import base64
import json
import threading
from typing import Any

import openai
from openai.types.responses import (
    EasyInputMessageParam,
    Response,
    ResponseInputImageParam,
    ResponseInputMessageContentListParam,
    ResponseInputTextParam,
)

from videokit.core.config import Settings
from videokit.core.llm.base import (
    GenerationRequest,
    GenerationResponse,
    ProviderError,
    ProviderStatus,
    Usage,
    format_timestamp,
)

OPENAI_API_URL = "https://api.openai.com/v1"  # always explicit: the SDK would otherwise read OPENAI_BASE_URL
SCHEMA_NAME = "structured_output"  # required by `text.format`; the schema itself is supplied by the caller


def _text(text: str) -> ResponseInputTextParam:
    return {"type": "input_text", "text": text}


def _jpeg(data: bytes) -> ResponseInputImageParam:
    encoded = base64.standard_b64encode(data).decode("ascii")
    return {"type": "input_image", "image_url": f"data:image/jpeg;base64,{encoded}", "detail": "auto"}


def build_input(req: GenerationRequest) -> list[EasyInputMessageParam]:
    """Stable prefix first (transcript, then each frame after its ``[frame m:ss]`` label) so OpenAI's automatic
    prefix caching applies, followed by the per-call turns. The prefix and the user turns that follow it share one
    message; an assistant turn is a message of its own."""
    blocks: ResponseInputMessageContentListParam = []
    if req.context_text:
        blocks.append(_text(req.context_text))
    for frame in req.frames:
        blocks.append(_text(f"[frame {format_timestamp(frame.t_seconds)}]"))
        blocks.append(_jpeg(frame.jpeg))

    messages: list[EasyInputMessageParam] = []
    for turn in req.turns:
        if turn.role == "user":
            blocks.append(_text(turn.text))
            continue
        if blocks:
            messages.append({"role": "user", "content": blocks})
            blocks = []
        messages.append({"role": "assistant", "content": turn.text})
    if blocks:
        messages.append({"role": "user", "content": blocks})
    return messages


def _usage(response: Response) -> Usage:
    u = response.usage
    if u is None:
        return Usage()
    cached = u.input_tokens_details.cached_tokens
    # OpenAI counts cached tokens inside input_tokens; Usage keeps them apart (like Anthropic) so pricing adds both.
    return Usage(
        input_tokens=max(u.input_tokens - cached, 0),
        output_tokens=u.output_tokens,
        cache_read_tokens=cached,
        cache_write_tokens=0,
    )


def _output_text(response: Response) -> str:
    """Concatenated output text; a refusal or an unfinished response is a ``ProviderError`` (no model text echoed)."""
    if response.status == "failed":
        raise ProviderError("OpenAI response failed")
    if response.status == "incomplete":
        reason = response.incomplete_details.reason if response.incomplete_details else None
        raise ProviderError(f"OpenAI response incomplete ({reason or 'unknown reason'})")
    parts: list[str] = []
    for item in response.output:
        if item.type != "message":
            continue
        for content in item.content:
            if content.type == "refusal":
                raise ProviderError("OpenAI refused the request")
            parts.append(content.text)
    return "".join(parts)


class OpenAIProvider:
    """One strict-JSON-schema Responses API request per call; the parsed JSON object is returned."""

    name = "openai"

    def __init__(self, settings: Settings, client: openai.OpenAI | None = None) -> None:
        self.model = (settings.openai_model or "").strip()
        self._api_key = settings.openai_api_key
        self._client = client
        self._lock = threading.Lock()

    def check(self) -> ProviderStatus:
        if self._api_key is None or not self._api_key.get_secret_value().strip():
            return ProviderStatus(ready=False, detail="missing OPENAI_API_KEY")
        if not self.model:
            return ProviderStatus(ready=False, detail="missing OPENAI_MODEL")
        return ProviderStatus(ready=True, detail=f"model={self.model}")

    def _sdk(self) -> openai.OpenAI:
        with self._lock:
            if self._client is None:
                key = self._api_key.get_secret_value() if self._api_key is not None else None
                self._client = openai.OpenAI(api_key=key, base_url=OPENAI_API_URL)  # SDK default retries handle 429/5xx
            return self._client

    def request_params(self, req: GenerationRequest) -> dict[str, Any]:
        return {
            "model": self.model,
            "instructions": req.system,
            "input": build_input(req),
            "text": {"format": {"type": "json_schema", "name": SCHEMA_NAME, "schema": req.schema, "strict": True}},
            "store": False,  # privacy: do not keep requests (frames, transcript) server-side
        }

    def generate(self, req: GenerationRequest) -> GenerationResponse:
        try:
            response = self._sdk().responses.create(**self.request_params(req))
        except openai.OpenAIError as exc:
            raise ProviderError(f"OpenAI request failed ({type(exc).__name__})") from exc

        try:
            data = json.loads(_output_text(response))
        except json.JSONDecodeError as exc:
            raise ProviderError("OpenAI response was not valid JSON") from exc
        if not isinstance(data, dict):
            raise ProviderError("OpenAI response was not a JSON object")
        return GenerationResponse(data=data, usage=_usage(response), provider=f"{self.name}:{response.model}")
