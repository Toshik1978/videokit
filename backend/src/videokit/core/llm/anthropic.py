"""Anthropic Messages API provider (research R9)."""

import base64
import json
import threading
from typing import Any, Literal

import anthropic
from anthropic.types.beta import (
    BetaContentBlockParam,
    BetaImageBlockParam,
    BetaMessage,
    BetaMessageParam,
    BetaTextBlockParam,
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

ANTHROPIC_API_URL = "https://api.anthropic.com"  # always explicit: the SDK would otherwise read ANTHROPIC_BASE_URL
MAX_TOKENS = 64000  # streaming request: room for adaptive thinking plus the JSON output
REFUSAL_FALLBACK_BETA = "server-side-fallback-2026-07-01"  # pairs with fallbacks="default"


def _text(text: str) -> BetaTextBlockParam:
    return {"type": "text", "text": text}


def _jpeg(data: bytes) -> BetaImageBlockParam:
    encoded = base64.standard_b64encode(data).decode("ascii")
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": encoded}}


def build_messages(req: GenerationRequest) -> list[BetaMessageParam]:
    """Stable prefix (transcript, then each frame after its ``[frame m:ss]`` label) with a cache breakpoint on its
    last block, followed by the per-call turns. Consecutive turns of one role share a message."""
    prefix: list[BetaTextBlockParam | BetaImageBlockParam] = []
    if req.context_text:
        prefix.append(_text(req.context_text))
    for frame in req.frames:
        prefix.append(_text(f"[frame {format_timestamp(frame.t_seconds)}]"))
        prefix.append(_jpeg(frame.jpeg))
    if prefix:
        prefix[-1]["cache_control"] = {"type": "ephemeral"}

    messages: list[BetaMessageParam] = []
    blocks: list[BetaContentBlockParam] = list(prefix)
    role: Literal["user", "assistant"] = "user"
    for turn in req.turns:
        if turn.role != role and blocks:
            messages.append({"role": role, "content": blocks})
            blocks = []
        role = turn.role
        blocks.append(_text(turn.text))
    if blocks:
        messages.append({"role": role, "content": blocks})
    return messages


def _usage(message: BetaMessage) -> Usage:
    u = message.usage
    return Usage(
        input_tokens=u.input_tokens,
        output_tokens=u.output_tokens,
        cache_read_tokens=u.cache_read_input_tokens or 0,
        cache_write_tokens=u.cache_creation_input_tokens or 0,
    )


class AnthropicProvider:
    """Streams one structured-output request per call and returns the parsed JSON."""

    name = "anthropic"

    def __init__(self, settings: Settings, client: anthropic.Anthropic | None = None) -> None:
        self.model = settings.anthropic_model
        self.effort = settings.anthropic_effort
        self._api_key = settings.anthropic_api_key
        self._client = client
        self._lock = threading.Lock()

    def check(self) -> ProviderStatus:
        if self._api_key is None or not self._api_key.get_secret_value().strip():
            return ProviderStatus(ready=False, detail="missing ANTHROPIC_API_KEY")
        return ProviderStatus(ready=True, detail=f"model={self.model}")

    def _sdk(self) -> anthropic.Anthropic:
        with self._lock:
            if self._client is None:
                key = self._api_key.get_secret_value() if self._api_key is not None else None
                # SDK default retries handle 429/5xx
                self._client = anthropic.Anthropic(api_key=key, base_url=ANTHROPIC_API_URL)
            return self._client

    def request_params(self, req: GenerationRequest) -> dict[str, Any]:
        return {
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            "system": req.system,
            "messages": build_messages(req),
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": self.effort, "format": {"type": "json_schema", "schema": req.schema}},
            "betas": [REFUSAL_FALLBACK_BETA],
            "fallbacks": "default",
        }

    def generate(self, req: GenerationRequest) -> GenerationResponse:
        try:
            with self._sdk().beta.messages.stream(**self.request_params(req)) as stream:
                message = stream.get_final_message()
        except anthropic.AnthropicError as exc:
            raise ProviderError(f"Anthropic request failed ({type(exc).__name__})") from exc

        if message.stop_reason != "end_turn":
            raise ProviderError(f"Anthropic response stopped early ({message.stop_reason})")
        text = "".join(block.text for block in message.content if block.type == "text")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProviderError("Anthropic response was not valid JSON") from exc
        if not isinstance(data, dict):
            raise ProviderError("Anthropic response was not a JSON object")
        return GenerationResponse(data=data, usage=_usage(message), provider=f"{self.name}:{message.model}")
