"""Gemini Omni through the Interactions API of the Gemini SDK (research V2, V8, V9, V10).

``interactions.*`` raises the ``google.genai._interactions`` error family (status-code subclasses that are *not*
``google.genai.errors.APIError``); other SDK paths raise the latter. Both are mapped to a user-safe ``VideoError``
code from the HTTP status and the error ``code``/``status``. Provider messages are inspected only to tell an
image problem from another bad request; they are never carried, stored or logged (only exception types are).
"""

import base64
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types
from google.genai._interactions import APIError as InteractionsAPIError
from google.genai.interactions import Interaction, ModalityTokens

from videokit.core.config import Settings
from videokit.core.videogen.base import (
    Capabilities,
    PollResult,
    ProviderStatus,
    Usage,
    VideoError,
    VideoErrorCode,
    VideoRequest,
)

logger = logging.getLogger("videokit")

CAPABILITIES = Capabilities(
    durations_s=[5, 6, 7, 8, 9, 10],
    max_pictures=7,
    video_tokens_per_second={"720p": 5792, "1080p": 8688},
    tokens_per_image=1120,
)

_API_ERRORS = (InteractionsAPIError, genai_errors.APIError)
_FAILED = frozenset({"failed", "incomplete", "cancelled", "budget_exceeded", "requires_action"})
_SAFETY_CODES = ("safety", "prohibited_content", "content_blocked")  # also matches the image_* variants
_CREDIT_CODES = ("payment_required", "quota_exceeded", "failed_precondition")
_PICTURE_WORDS = ("image", "picture")
_NESTED_KEYS = ("error", "errors", "details")
_CODE_KEYS = ("code", "status", "type", "reason")


def _duration(seconds: int) -> str:
    """The ``duration`` string of ``response_format``; its exact format ("8s" vs "8") is live-verified (V9)."""
    return f"{seconds}s"


@dataclass(frozen=True)
class _Requested:
    """What was asked for, to derive usage when the provider reports none (research V8)."""

    duration_s: int
    resolution: str
    pictures: int


def _error_strings(payload: object) -> list[str]:
    """Lower-cased ``code``/``status``/``type``/``reason`` strings found anywhere in an error body."""
    found: list[str] = []
    if isinstance(payload, dict):
        found += [value.lower() for key in _CODE_KEYS if isinstance(value := payload.get(key), str)]
        for key in _NESTED_KEYS:
            found += _error_strings(payload.get(key))
    elif isinstance(payload, list):
        for item in payload:
            found += _error_strings(item)
    return found


def _error_message(payload: object) -> str:
    if isinstance(payload, dict):
        message = payload.get("message")
        return message if isinstance(message, str) else _error_message(payload.get("error"))
    return ""


def _from_codes(codes: list[str]) -> VideoErrorCode | None:
    """The mapping every failure shares, whether it came as an HTTP error or inside an interaction."""
    if any(marker in code for code in codes for marker in _SAFETY_CODES):
        return "content_blocked"
    if any(marker in code for code in codes for marker in _CREDIT_CODES):
        return "provider_out_of_credit"
    return None


def _from_http(status: int | None, codes: list[str], message: str) -> VideoErrorCode:
    mapped = _from_codes(codes)
    if mapped is not None:
        return mapped
    if status == 402:
        return "provider_out_of_credit"
    if status == 400 and any(word in message.lower() for word in _PICTURE_WORDS):
        return "invalid_picture"
    if status is None or status < 400 or status >= 500 or status in (401, 403, 429):
        return "provider_unavailable"  # network, 5xx, rate limit, rejected key
    return "video_failed"


def _video_error(exc: Exception) -> VideoError:
    if isinstance(exc, genai_errors.APIError):
        status: int | None = exc.code
        payload: object = exc.details
    else:
        status = getattr(exc, "status_code", None)
        payload = getattr(exc, "body", None)
    code = _from_http(status, _error_strings(payload), _error_message(payload))
    logger.warning("Omni request failed (%s: %s)", type(exc).__name__, code)
    return VideoError(code)


def _interaction_codes(interaction: Interaction) -> list[str]:
    codes = [error.code.lower() for error in interaction.errors or [] if error.code]
    for step in interaction.steps or []:
        status = getattr(step, "error", None)
        if status is not None:
            codes += _error_strings({"details": status.details})
    return codes


def _sum(entries: list[ModalityTokens] | None, *, video: bool | None = None) -> int:
    """Tokens of a ``*_tokens_by_modality`` list: all of them, or only the video (or only the non-video) ones."""
    return sum(e.tokens or 0 for e in entries or [] if video is None or (e.modality == "video") == video)


def _decode(data: str | None) -> bytes | None:
    try:
        return base64.b64decode(data, validate=True) if data else None
    except ValueError:  # binascii.Error
        return None


def _write(out_dir: Path, data: bytes) -> Path:
    target = out_dir / "raw.mp4"
    partial = out_dir / "raw.mp4.part"
    partial.write_bytes(data)
    partial.replace(target)
    return target


def _usage(interaction: Interaction, derive_for: _Requested | None) -> Usage:
    """As reported; for a delivered clip (``derive_for``) the missing parts come from the documented rate (V8)."""
    reported = interaction.usage
    video = other = tokens_in = 0
    if reported is not None:
        video = _sum(reported.output_tokens_by_modality, video=True)
        other = _sum(reported.output_tokens_by_modality, video=False)
        tokens_in = reported.total_input_tokens or _sum(reported.input_tokens_by_modality)
    if derive_for is not None:
        rate = CAPABILITIES.video_tokens_per_second.get(derive_for.resolution, 0)
        video = video or derive_for.duration_s * rate
        tokens_in = tokens_in or derive_for.pictures * CAPABILITIES.tokens_per_image
    return Usage(input_tokens=tokens_in, output_tokens=other, video_output_tokens=video)


class OmniProvider:
    """Generates one silent clip per interaction: ``submit`` -> poll ``get`` -> delete the stored interaction."""

    name = "omni"

    def __init__(
        self,
        settings: Settings,
        client: genai.Client | None = None,
        *,
        http_options: genai_types.HttpOptions | None = None,
    ) -> None:
        """``client`` replaces both SDK clients (tests); ``http_options`` feeds the ones built lazily (tests)."""
        self.model = settings.omni_model
        self._api_key = settings.gemini_api_key
        self._client = client
        self._create_client = client
        self._http_options = http_options
        self._lock = threading.Lock()
        self._requested: dict[str, _Requested] = {}

    def check(self) -> ProviderStatus:
        if self._api_key is None or not self._api_key.get_secret_value().strip():
            return ProviderStatus(ready=False, detail="missing GEMINI_API_KEY")
        if not self.model.strip():
            return ProviderStatus(ready=False, detail="missing OMNI_MODEL")
        return ProviderStatus(ready=True, detail=f"model={self.model}")

    def capabilities(self) -> Capabilities:
        return CAPABILITIES

    def _build(self, http_options: genai_types.HttpOptions | None) -> genai.Client:
        key = self._api_key.get_secret_value() if self._api_key is not None else None
        return genai.Client(api_key=key, http_options=http_options)

    def _sdk(self) -> genai.Client:
        """For ``get``, ``cancel`` and ``delete``: idempotent, so the SDK's default retries on 429/5xx are welcome."""
        with self._lock:
            if self._client is None:
                self._client = self._build(self._http_options)
            return self._client

    def _creator(self) -> genai.Client:
        """For ``create`` only: the request is paid and has no idempotency key, so it is never resent.

        The SDK would otherwise retry 408/409/429/5xx and lost connections, and a retry after an accepted-but-
        unanswered request would start (and bill) a second interaction that nothing polls or deletes. A failed
        submit surfaces as ``VideoError`` and the chain decides what happens next.

        ``HttpRetryOptions`` cannot express "no retries" (``attempts=0`` is raised to 1, and the interactions client
        counts ``attempts`` as retries), so the interactions resource of this dedicated client gets ``retry_config
        = None``, which its request loop treats as "do not retry". ``test_omni_mapping`` fails if an SDK upgrade
        changes that.
        """
        with self._lock:
            if self._create_client is None:
                client = self._build(self._http_options)
                client.interactions.sdk_configuration.retry_config = None
                self._create_client = client
            return self._create_client

    def submit(self, req: VideoRequest) -> str:
        content: list[dict[str, str]] = [{"type": "text", "text": req.prompt}]
        for picture in sorted(req.pictures, key=lambda p: p.position):
            data = base64.standard_b64encode(picture.jpeg).decode("ascii")
            content.append({"type": "image", "data": data, "mime_type": "image/jpeg"})
        params: dict[str, Any] = {
            "model": self.model,
            "input": content,
            "response_format": {
                "type": "video",
                "aspect_ratio": req.aspect_ratio,
                "resolution": req.resolution,
                "duration": _duration(req.duration_s),
                "delivery": "inline",  # the clip arrives as base64 in the response, not as a URI to fetch
            },
            "background": True,
            "store": True,
        }
        try:
            interaction = self._creator().interactions.create(**params)
        except _API_ERRORS as exc:
            raise _video_error(exc) from None
        ref = getattr(interaction, "id", None)
        if not ref:
            raise VideoError("video_failed")
        with self._lock:
            self._requested[ref] = _Requested(req.duration_s, req.resolution, len(req.pictures))
        return str(ref)

    def poll(self, ref: str, out_dir: Path) -> PollResult:
        try:
            interaction = self._sdk().interactions.get(ref)
        except _API_ERRORS as exc:
            raise _video_error(exc) from None
        if not isinstance(interaction, Interaction):
            raise VideoError("provider_unavailable")
        if interaction.status not in _FAILED | {"completed"}:  # still running (or a status this adapter does not know)
            return PollResult(done=False, video=None, usage=Usage(), error=None)

        with self._lock:
            requested = self._requested.pop(ref, None)
        output = interaction.output_video if interaction.status == "completed" else None
        clip = _decode(output.data if output else None)
        if clip:
            return PollResult(done=True, video=_write(out_dir, clip), usage=_usage(interaction, requested), error=None)

        code = _from_codes(_interaction_codes(interaction)) or "video_failed"
        logger.warning("Omni interaction ended without a clip (%s: %s)", interaction.status, code)
        return PollResult(done=True, video=None, usage=_usage(interaction, None), error=VideoError(code))

    def cancel(self, ref: str) -> None:
        self._best_effort("cancel", ref)

    def forget(self, ref: str) -> None:
        self._best_effort("delete", ref)

    def _best_effort(self, operation: str, ref: str) -> None:
        with self._lock:
            self._requested.pop(ref, None)
        try:
            getattr(self._sdk().interactions, operation)(ref)
        except Exception as exc:  # cancelling or deleting must never fail the job
            logger.warning("Omni %s failed (%s)", operation, type(exc).__name__)
