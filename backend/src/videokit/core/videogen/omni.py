"""Gemini Omni through the Interactions API of the Gemini SDK (research V2, V8, V9, V10).

A 720p clip is delivered inline (base64 in the interaction); a 1080p clip, which typically passes the 4 MB Google
advises for inline delivery, as a URI that is downloaded here with the API key (research V2). Both end up as
``raw.mp4`` in the job directory.

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

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types
from google.genai._interactions import APIError as InteractionsAPIError
from google.genai.interactions import Interaction, ModalityTokens, VideoContent

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

# always explicit: the SDK would otherwise read GOOGLE_GEMINI_BASE_URL
GEMINI_API_URL = "https://generativelanguage.googleapis.com/"
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
_INLINE_RESOLUTIONS = frozenset({"720p"})  # larger clips are delivered as a URI (Google: inline up to 4 MB)
_KEY_HEADER = "x-goog-api-key"
_KEY_DOMAIN = "googleapis.com"  # the key goes only to Google API hosts, never to a redirect target elsewhere
_DOWNLOAD_TIMEOUT = httpx.Timeout(30.0, read=120.0)
_DOWNLOAD_CHUNK = 1024 * 1024


def _duration(seconds: int) -> str:
    """The ``duration`` string of ``response_format``; its exact format ("8s" vs "8") is live-verified (V9)."""
    return f"{seconds}s"


class _EmptyDownload(Exception):
    """The delivery URI answered with no bytes."""


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


def _after_acceptance(codes: list[str]) -> VideoErrorCode:
    """Omni reports a refused prompt only as INVALID_ARGUMENT on an interaction it had already accepted (research V10).

    The request's shape was validated when it was accepted, so a later INVALID_ARGUMENT means the content.
    """
    return "content_blocked" if "invalid_argument" in codes else "video_failed"


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


def _delivery(resolution: str) -> str:
    return "inline" if resolution in _INLINE_RESOLUTIONS else "uri"


def _write(out_dir: Path, data: bytes) -> Path:
    target = out_dir / "raw.mp4"
    partial = out_dir / "raw.mp4.part"
    partial.write_bytes(data)
    partial.replace(target)
    return target


def _google_host(url: httpx.URL) -> bool:
    host = url.host.lower()
    return url.scheme == "https" and (host == _KEY_DOMAIN or host.endswith(f".{_KEY_DOMAIN}"))


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
        download_transport: httpx.BaseTransport | None = None,
    ) -> None:
        """``client`` replaces both SDK clients (tests); ``http_options`` feeds the ones built lazily (tests);
        ``download_transport`` carries the clip downloads (tests)."""
        self.model = settings.omni_model
        self._api_key = settings.gemini_api_key
        self._client = client
        self._create_client = client
        self._http_options = http_options
        self._download_transport = download_transport
        self._downloader: httpx.Client | None = None
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
        options = (http_options or genai_types.HttpOptions()).model_copy()
        options.base_url = options.base_url or GEMINI_API_URL
        return genai.Client(api_key=key, http_options=options)

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
                "delivery": _delivery(req.resolution),  # inline base64 at 720p, a URI to download above
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

        output = interaction.output_video if interaction.status == "completed" else None
        video = self._collect(output, out_dir)  # a failed download raises; the request is kept for the next poll
        with self._lock:
            requested = self._requested.pop(ref, None)
        if video is not None:
            return PollResult(done=True, video=video, usage=_usage(interaction, requested), error=None)

        codes = _interaction_codes(interaction)
        code = _from_codes(codes) or _after_acceptance(codes)
        logger.warning("Omni interaction ended without a clip (%s: %s)", interaction.status, code)
        return PollResult(done=True, video=None, usage=_usage(interaction, None), error=VideoError(code))

    def _collect(self, output: VideoContent | None, out_dir: Path) -> Path | None:
        """``raw.mp4`` from inline data or else the delivery URI; None when the interaction carries neither."""
        clip = _decode(output.data if output else None)
        if clip:
            return _write(out_dir, clip)
        if output is not None and output.uri:
            return self._download(output.uri, out_dir)
        return None

    def _http(self) -> httpx.Client:
        with self._lock:
            if self._downloader is None:
                self._downloader = httpx.Client(
                    transport=self._download_transport,
                    timeout=_DOWNLOAD_TIMEOUT,
                    follow_redirects=True,
                    event_hooks={"request": [self._authorize]},
                )
            return self._downloader

    def _authorize(self, request: httpx.Request) -> None:
        """Runs for every hop, redirects included: the key is attached for Google API hosts and removed elsewhere."""
        if self._api_key is not None and _google_host(request.url):
            request.headers[_KEY_HEADER] = self._api_key.get_secret_value()
        else:
            request.headers.pop(_KEY_HEADER, None)

    def _download(self, uri: str, out_dir: Path) -> Path:
        """Stream the clip into ``raw.mp4.part``, then rename. Any failure is ``provider_unavailable``: the
        interaction stays stored, so the next poll downloads it again (the tool's wait cap still applies)."""
        target = out_dir / "raw.mp4"
        partial = out_dir / "raw.mp4.part"
        try:
            with self._http().stream("GET", uri) as response, partial.open("wb") as file:
                response.raise_for_status()
                for chunk in response.iter_bytes(_DOWNLOAD_CHUNK):
                    file.write(chunk)
            if partial.stat().st_size == 0:
                raise _EmptyDownload
            partial.replace(target)
        except (httpx.HTTPError, httpx.InvalidURL, _EmptyDownload) as exc:
            logger.warning("Omni clip download failed (%s)", type(exc).__name__)
            raise VideoError("provider_unavailable") from None
        finally:
            partial.unlink(missing_ok=True)  # gone after the rename; a leftover of any failure otherwise
        return target

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
