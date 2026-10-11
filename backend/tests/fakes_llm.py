"""Scripted `LLMProvider` for tests: no SDK, no network."""

from collections import deque
from collections.abc import Iterable
from typing import Any

from videokit.core.llm import GenerationRequest, GenerationResponse, ProviderStatus, Usage

ScriptItem = dict[str, Any] | GenerationResponse | Exception


class FakeLLM:
    """Answers each `generate` call with the next scripted item, in order.

    Script items:
    - a ``dict`` -> returned as ``data`` (it may deliberately break the output rules, to exercise repair);
    - a ``GenerationResponse`` -> returned as is;
    - an ``Exception`` -> raised (e.g. ``ProviderError("overloaded")`` to exercise fallback).

    ``FakeLLM([{"title": "..."}])`` reports provider ``"fake:fake-model"`` and ``usage`` per call.
    Every request is appended to ``requests``; calling past the end of the script fails the test.
    """

    def __init__(
        self,
        script: Iterable[ScriptItem],
        *,
        name: str = "fake",
        model: str = "fake-model",
        usage: Usage | None = None,
    ) -> None:
        self.name = name
        self.model = model
        self.usage = usage if usage is not None else Usage(input_tokens=1000, output_tokens=200)
        self.script: deque[ScriptItem] = deque(script)
        self.requests: list[GenerationRequest] = []

    def check(self) -> ProviderStatus:
        return ProviderStatus(ready=True, detail=self.model)

    def generate(self, req: GenerationRequest) -> GenerationResponse:
        self.requests.append(req)
        if not self.script:
            raise AssertionError("FakeLLM script exhausted")
        item = self.script.popleft()
        if isinstance(item, Exception):
            raise item
        if isinstance(item, GenerationResponse):
            return item
        return GenerationResponse(data=item, usage=self.usage, provider=f"{self.name}:{self.model}")
