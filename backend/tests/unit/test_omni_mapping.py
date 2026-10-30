"""Omni adapter: request shape, polling, usage and the V10 failure mapping (research V2, V8, V9, V10).

The adapter runs against the real `google-genai` client whose HTTP transport is replaced by an in-memory
`httpx.MockTransport`, so request bodies and response parsing follow the installed SDK. No network, no key.
"""

import base64
import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types
from pydantic import SecretStr

from videokit.core import providers
from videokit.core.config import Settings
from videokit.core.videogen import (
    Capabilities,
    Picture,
    ProviderStatus,
    Usage,
    VideoError,
    VideoGenerator,
    VideoRequest,
)
from videokit.core.videogen.omni import OmniProvider

VIDEO_BYTES = b"\x00\x00\x00\x18ftypmp42-fake-video"
KEY = "gm-test-secret"
JPEG_1 = b"\xff\xd8first"
JPEG_2 = b"\xff\xd8second"
REQUEST = VideoRequest(
    prompt="MARKER-PROMPT a slow pan across a lake",
    pictures=[Picture(position=1, jpeg=JPEG_1), Picture(position=2, jpeg=JPEG_2)],
    duration_s=8,
    aspect_ratio="9:16",
    resolution="1080p",
)

Handler = Callable[[httpx.Request], httpx.Response]


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {"gemini_api_key": SecretStr(KEY), "video_providers": ["omni"]}
    return Settings.model_validate(values | overrides)


# What the SDK does by default (retry 408/409/429/5xx and lost connections), made fast for tests.
SDK_RETRIES = genai_types.HttpRetryOptions(attempts=3, initial_delay=0.001, max_delay=0.001)


def _options(handler: Handler, retries: genai_types.HttpRetryOptions = SDK_RETRIES) -> genai_types.HttpOptions:
    return genai_types.HttpOptions(
        httpx_client=httpx.Client(transport=httpx.MockTransport(handler)), retry_options=retries
    )


def _client(handler: Handler) -> genai.Client:
    return genai.Client(api_key=KEY, http_options=_options(handler))


def _provider(handler: Handler, **overrides: Any) -> OmniProvider:
    """The SDK calls and the clip download (``delivery: "uri"``) both go to ``handler``."""
    return OmniProvider(
        _settings(**overrides), http_options=_options(handler), download_transport=httpx.MockTransport(handler)
    )


def _error(status: int, code: str | None = None, message: str = "provider text") -> Handler:
    body: dict[str, Any] = {"error": {"message": message, "status": "UNSPECIFIED"}}
    if code is not None:
        body["error"]["code"] = code
    return lambda _request: httpx.Response(status, json=body)


def _video_step(data: bytes = VIDEO_BYTES) -> dict[str, Any]:
    content = {"type": "video", "mime_type": "video/mp4", "data": base64.b64encode(data).decode("ascii")}
    return {"type": "model_output", "content": [content]}


def _interaction(status: str = "completed", **fields: Any) -> Handler:
    body = {"id": "int-1", "status": status} | fields
    return lambda _request: httpx.Response(200, json=body)


def _poll(handler: Handler, out_dir: Path, **overrides: Any) -> Any:
    return _provider(handler, **overrides).poll("int-1", out_dir)


# --- check, capabilities, registration -----------------------------------------------------------------------


def test_satisfies_the_protocol_and_is_registered() -> None:
    provider = OmniProvider(_settings())
    assert isinstance(provider, VideoGenerator)
    assert provider.name == "omni"
    assert provider.model == "gemini-omni-1.1-flash"
    assert providers.VIDEOS["omni"] is OmniProvider


def test_model_follows_the_setting() -> None:
    assert OmniProvider(_settings(omni_model="gemini-omni-next")).model == "gemini-omni-next"


def test_check_is_local_and_reports_the_model() -> None:
    def never(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("check() must not call the network")

    provider = _provider(never)
    assert provider.check() == ProviderStatus(ready=True, detail="model=gemini-omni-1.1-flash")


@pytest.mark.parametrize("key", [None, SecretStr(""), SecretStr("  ")])
def test_check_needs_a_key(key: SecretStr | None) -> None:
    status = OmniProvider(_settings(gemini_api_key=key)).check()
    assert status == ProviderStatus(ready=False, detail="missing GEMINI_API_KEY")


def test_check_needs_a_model() -> None:
    status = OmniProvider(_settings(omni_model=" ")).check()
    assert status == ProviderStatus(ready=False, detail="missing OMNI_MODEL")


def test_check_does_not_create_the_sdk_client(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: Any, **_k: Any) -> None:
        raise AssertionError("no client may be created")

    monkeypatch.setattr(genai, "Client", boom)
    assert OmniProvider(_settings()).check().ready


def test_capabilities() -> None:
    assert OmniProvider(_settings()).capabilities() == Capabilities(
        durations_s=[5, 6, 7, 8, 9, 10],
        max_pictures=7,
        video_tokens_per_second={"720p": 5792, "1080p": 8688},
        tokens_per_image=1120,
    )


# --- submit ---------------------------------------------------------------------------------------------------


def test_submit_sends_text_then_pictures_in_position_order_and_returns_the_interaction_id() -> None:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={"id": "int-42", "status": "in_progress"})

    req = VideoRequest(
        prompt=REQUEST.prompt,
        pictures=[Picture(position=2, jpeg=JPEG_2), Picture(position=1, jpeg=JPEG_1)],  # given out of order
        duration_s=8,
        aspect_ratio="9:16",
        resolution="1080p",
    )
    assert _provider(handler).submit(req) == "int-42"

    (request,) = sent
    assert request.method == "POST"
    assert request.url.path.endswith("/interactions")
    assert request.headers["x-goog-api-key"] == KEY
    body = json.loads(request.content)
    assert body["model"] == "gemini-omni-1.1-flash"
    assert body["background"] is True
    assert body["store"] is True
    assert body["response_format"] == {
        "type": "video",
        "aspect_ratio": "9:16",
        "resolution": "1080p",
        "duration": "8s",
        "delivery": "uri",  # 1080p clips pass 4 MB: Google advises a URI above 720p
    }
    (turn,) = body["input"]
    parts = [(part["type"], part.get("text"), part.get("data"), part.get("mime_type")) for part in turn["content"]]
    assert parts == [
        ("text", REQUEST.prompt, None, None),
        ("image", None, _b64(JPEG_1), "image/jpeg"),
        ("image", None, _b64(JPEG_2), "image/jpeg"),
    ]


def test_submit_without_pictures_sends_only_the_prompt() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "int-1", "status": "queued"})

    req = VideoRequest(prompt="p", pictures=[], duration_s=5, aspect_ratio="16:9", resolution="720p")
    _provider(handler).submit(req)
    assert [part["type"] for part in bodies[0]["input"][0]["content"]] == ["text"]
    assert bodies[0]["response_format"] == {
        "type": "video",
        "aspect_ratio": "16:9",
        "resolution": "720p",
        "duration": "5s",
        "delivery": "inline",
    }


def test_submit_without_an_interaction_id_is_a_failure() -> None:
    with pytest.raises(VideoError) as exc:
        _provider(lambda _r: httpx.Response(200, json={"status": "in_progress"})).submit(REQUEST)
    assert exc.value.code == "video_failed"


def test_sdk_clients_are_created_lazily_one_for_create_and_one_for_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[genai_types.HttpOptions | None] = []  # the options each SDK client was built with
    real = _client(lambda _r: httpx.Response(200, json={"id": "int-1", "status": "in_progress"}))

    def factory(*, api_key: str, http_options: genai_types.HttpOptions | None) -> genai.Client:
        assert api_key == KEY
        created.append(http_options)
        return real

    monkeypatch.setattr(genai, "Client", factory)
    provider = OmniProvider(_settings())
    assert created == []
    provider.submit(REQUEST)
    provider.submit(REQUEST)
    assert len(created) == 1
    provider.poll("int-1", Path("."))
    provider.cancel("int-1")
    assert len(created) == 2


# --- a paid submit is never resent by the SDK ------------------------------------------------------------------


@pytest.mark.parametrize("failure", ["503", "504", "429", "409", "408", "connect", "timeout"])
def test_submit_is_sent_exactly_once_even_when_the_sdk_would_retry(failure: str) -> None:
    posts: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posts.append(request)
        if len(posts) > 1:  # a retry would be answered and accepted: a second paid interaction
            return httpx.Response(200, json={"id": "int-second", "status": "in_progress"})
        if failure == "connect":
            raise httpx.ConnectError("lost", request=request)
        if failure == "timeout":
            raise httpx.ReadTimeout("lost", request=request)
        return httpx.Response(int(failure), json={"error": {"message": "x"}})

    with pytest.raises(VideoError) as exc:
        _provider(handler).submit(REQUEST)

    assert len(posts) == 1
    assert exc.value.code in {"provider_unavailable", "video_failed"}


def test_a_503_on_submit_maps_to_provider_unavailable_after_a_single_post() -> None:
    posts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posts.append(request.method)
        status = 503 if len(posts) == 1 else 200
        return httpx.Response(status, json={"id": "int-second", "status": "in_progress", "error": {"message": "x"}})

    with pytest.raises(VideoError) as exc:
        _provider(handler).submit(REQUEST)
    assert exc.value.code == "provider_unavailable"
    assert posts == ["POST"]


def test_reads_keep_the_sdk_retries(tmp_path: Path) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        if len(calls) == 1:
            return httpx.Response(503, json={"error": {"message": "busy"}})
        return httpx.Response(200, json={"id": "int-1", "status": "in_progress"})

    result = _provider(handler).poll("int-1", tmp_path)
    assert result.done is False
    assert calls == ["GET", "GET"]


# --- poll: progress, success, usage ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["in_progress", "queued", "requires_continuation"])
def test_poll_running_is_not_done(status: str, tmp_path: Path) -> None:
    result = _poll(_interaction(status), tmp_path)
    assert (result.done, result.video, result.error, result.usage) == (False, None, None, Usage())
    assert list(tmp_path.iterdir()) == []


def test_poll_completed_writes_raw_mp4_and_reports_usage(tmp_path: Path) -> None:
    usage = {
        "output_tokens_by_modality": [{"modality": "video", "tokens": 69_500}, {"modality": "text", "tokens": 12}],
        "input_tokens_by_modality": [{"modality": "text", "tokens": 30}, {"modality": "image", "tokens": 2240}],
        "total_input_tokens": 2270,
    }
    result = _poll(_interaction(steps=[_video_step()], usage=usage), tmp_path)

    assert result.done is True
    assert result.error is None
    assert result.video == tmp_path / "raw.mp4"
    assert result.video.read_bytes() == VIDEO_BYTES
    assert result.usage == Usage(input_tokens=2270, output_tokens=12, video_output_tokens=69_500)
    assert [p.name for p in tmp_path.iterdir()] == ["raw.mp4"]  # no leftover temp file


def test_poll_input_tokens_fall_back_to_the_modality_breakdown(tmp_path: Path) -> None:
    usage = {
        "output_tokens_by_modality": [{"modality": "video", "tokens": 100}],
        "input_tokens_by_modality": [{"modality": "text", "tokens": 30}, {"modality": "image", "tokens": 20}],
    }
    result = _poll(_interaction(steps=[_video_step()], usage=usage), tmp_path)
    assert result.usage == Usage(input_tokens=50, video_output_tokens=100)


def test_poll_derives_usage_at_the_documented_rate_when_none_is_reported(tmp_path: Path) -> None:
    provider = _provider(_dispatch(submit=_interaction("in_progress"), get=_interaction(steps=[_video_step()])))
    ref = provider.submit(REQUEST)  # 8 s at 1080p, two pictures

    result = provider.poll(ref, tmp_path)

    assert result.usage == Usage(input_tokens=2 * 1120, video_output_tokens=8 * 8688)


def test_poll_derives_video_tokens_when_the_report_has_no_video_entry(tmp_path: Path) -> None:
    usage = {"output_tokens_by_modality": [{"modality": "text", "tokens": 3}]}
    provider = _provider(
        _dispatch(submit=_interaction("in_progress"), get=_interaction(steps=[_video_step()], usage=usage))
    )
    ref = provider.submit(REQUEST)

    assert provider.poll(ref, tmp_path).usage == Usage(
        input_tokens=2 * 1120, output_tokens=3, video_output_tokens=8 * 8688
    )


def test_poll_of_an_unknown_request_without_usage_reports_none(tmp_path: Path) -> None:
    """After a restart the adapter no longer knows the request; the tool then estimates from its own inputs."""
    result = _poll(_interaction(steps=[_video_step()]), tmp_path)
    assert result.done is True
    assert result.usage == Usage()


def test_poll_uses_the_last_video_of_a_completed_interaction(tmp_path: Path) -> None:
    text_step = {"type": "model_output", "content": [{"type": "text", "text": "done"}]}
    result = _poll(_interaction(steps=[text_step, _video_step(b"clip")]), tmp_path)
    assert result.video is not None
    assert result.video.read_bytes() == b"clip"


def _dispatch(*, submit: Handler, get: Handler) -> Handler:
    return lambda request: (submit if request.method == "POST" else get)(request)


# --- poll: a clip delivered as a URI (1080p) ------------------------------------------------------------------

CLIP_URI = "https://generativelanguage.googleapis.com/v1beta/files/clip-1:download?alt=media"


def _uri_step(uri: str = CLIP_URI) -> dict[str, Any]:
    return {"type": "model_output", "content": [{"type": "video", "mime_type": "video/mp4", "uri": uri}]}


def _served(clip: Handler, interaction: Handler) -> Handler:
    """Interaction calls go to ``interaction``; every other request is the clip download."""
    return lambda request: (interaction if "/interactions" in request.url.path else clip)(request)


def test_poll_downloads_a_clip_delivered_as_a_uri_into_raw_mp4_with_the_key(tmp_path: Path) -> None:
    downloads: list[httpx.Request] = []

    def clip(request: httpx.Request) -> httpx.Response:
        downloads.append(request)
        return httpx.Response(200, content=VIDEO_BYTES)

    usage = {"output_tokens_by_modality": [{"modality": "video", "tokens": 86_880}], "total_input_tokens": 40}
    result = _poll(_served(clip, _interaction(steps=[_uri_step()], usage=usage)), tmp_path)

    assert (result.done, result.error) == (True, None)
    assert result.video == tmp_path / "raw.mp4"
    assert result.video.read_bytes() == VIDEO_BYTES
    assert [p.name for p in tmp_path.iterdir()] == ["raw.mp4"]  # written as raw.mp4.part, then renamed
    assert result.usage == Usage(input_tokens=40, video_output_tokens=86_880)
    (request,) = downloads
    assert (request.method, str(request.url)) == ("GET", CLIP_URI)
    assert request.headers["x-goog-api-key"] == KEY


def test_poll_prefers_inline_data_and_downloads_nothing(tmp_path: Path) -> None:
    def clip(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("inline data needs no download")

    step = {"type": "model_output", "content": [{"type": "video", "data": _b64(VIDEO_BYTES), "uri": CLIP_URI}]}
    result = _poll(_served(clip, _interaction(steps=[step])), tmp_path)
    assert result.video is not None
    assert result.video.read_bytes() == VIDEO_BYTES


def test_the_key_is_not_sent_where_a_download_is_redirected_outside_google(tmp_path: Path) -> None:
    signed = "https://storage.example.com/clip-1?signature=abc"
    hops: list[httpx.Request] = []

    def clip(request: httpx.Request) -> httpx.Response:
        hops.append(request)
        if str(request.url) == CLIP_URI:
            return httpx.Response(302, headers={"location": signed})
        return httpx.Response(200, content=VIDEO_BYTES)

    result = _poll(_served(clip, _interaction(steps=[_uri_step()])), tmp_path)

    assert result.video is not None
    assert result.video.read_bytes() == VIDEO_BYTES
    assert [str(hop.url) for hop in hops] == [CLIP_URI, signed]
    assert hops[0].headers["x-goog-api-key"] == KEY
    assert "x-goog-api-key" not in hops[1].headers


def test_the_key_is_not_sent_to_a_uri_outside_google(tmp_path: Path) -> None:
    sent: list[httpx.Request] = []

    def clip(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, content=VIDEO_BYTES)

    _poll(_served(clip, _interaction(steps=[_uri_step("https://googleapis.com.example.com/files/clip-1")])), tmp_path)
    assert "x-goog-api-key" not in sent[0].headers


def _broken(failure: str) -> Handler:
    def clip(request: httpx.Request) -> httpx.Response:
        if failure == "connect":
            raise httpx.ConnectError("lost", request=request)
        if failure == "timeout":
            raise httpx.ReadTimeout("slow", request=request)
        if failure == "empty":
            return httpx.Response(200, content=b"")
        return httpx.Response(int(failure), text="SECRET-PROVIDER-TEXT")

    return clip


@pytest.mark.parametrize("failure", ["404", "403", "500", "connect", "timeout", "empty"])
def test_a_failed_download_is_provider_unavailable_and_leaves_no_file(
    failure: str, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    with pytest.raises(VideoError) as exc:
        _poll(_served(_broken(failure), _interaction(steps=[_uri_step()])), tmp_path)
    assert exc.value.code == "provider_unavailable"  # transient for the tool: the next poll downloads again
    assert list(tmp_path.iterdir()) == []
    assert "SECRET-PROVIDER-TEXT" not in caplog.text
    assert KEY not in caplog.text


def test_a_download_that_fails_once_still_derives_the_usage_of_the_request(tmp_path: Path) -> None:
    attempts: list[int] = []

    def clip(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return _broken("500")(request) if len(attempts) == 1 else httpx.Response(200, content=VIDEO_BYTES)

    interactions = _dispatch(submit=_interaction("in_progress"), get=_interaction(steps=[_uri_step()]))
    provider = _provider(_served(clip, interactions))
    ref = provider.submit(REQUEST)  # 8 s at 1080p, two pictures
    with pytest.raises(VideoError):
        provider.poll(ref, tmp_path)

    result = provider.poll(ref, tmp_path)

    assert result.video is not None
    assert result.usage == Usage(input_tokens=2 * 1120, video_output_tokens=8 * 8688)


def test_a_completed_interaction_with_neither_data_nor_uri_is_video_failed(tmp_path: Path) -> None:
    def clip(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("nothing to download")

    step = {"type": "model_output", "content": [{"type": "video", "mime_type": "video/mp4", "uri": ""}]}
    result = _poll(_served(clip, _interaction(steps=[step])), tmp_path)
    assert (result.done, result.video) == (True, None)
    assert result.error is not None
    assert result.error.code == "video_failed"
    assert list(tmp_path.iterdir()) == []


# --- poll: failures -------------------------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["failed", "incomplete", "cancelled", "budget_exceeded", "requires_action"])
def test_poll_terminal_status_without_a_reason_is_video_failed(status: str, tmp_path: Path) -> None:
    result = _poll(_interaction(status), tmp_path)
    assert result.done is True
    assert result.video is None
    assert result.error is not None
    assert result.error.code == "video_failed"


def test_poll_completed_without_a_video_is_video_failed(tmp_path: Path) -> None:
    text_step = {"type": "model_output", "content": [{"type": "text", "text": "I cannot do that"}]}
    result = _poll(_interaction(steps=[text_step]), tmp_path)
    assert (result.done, result.video) == (True, None)
    assert result.error is not None
    assert result.error.code == "video_failed"


def test_poll_completed_with_an_empty_video_is_video_failed(tmp_path: Path) -> None:
    step = {"type": "model_output", "content": [{"type": "video", "mime_type": "video/mp4"}]}
    result = _poll(_interaction(steps=[step]), tmp_path)
    assert result.error is not None
    assert result.error.code == "video_failed"
    assert list(tmp_path.iterdir()) == []


def test_poll_completed_with_undecodable_video_data_is_video_failed(tmp_path: Path) -> None:
    step = {"type": "model_output", "content": [{"type": "video", "mime_type": "video/mp4", "data": "not base64!"}]}
    result = _poll(_interaction(steps=[step]), tmp_path)
    assert result.error is not None
    assert result.error.code == "video_failed"
    assert list(tmp_path.iterdir()) == []


def test_poll_of_something_that_is_not_an_interaction_is_provider_unavailable(tmp_path: Path) -> None:
    class _Streams:
        def get(self, _id: str, **_kwargs: Any) -> object:
            return object()

    provider = OmniProvider(_settings(), client=_Client(_Streams()))  # type: ignore[arg-type]
    with pytest.raises(VideoError) as exc:
        provider.poll("int-1", tmp_path)
    assert exc.value.code == "provider_unavailable"


@pytest.mark.parametrize(
    "code", ["safety", "prohibited_content", "image_safety", "image_prohibited_content", "content_blocked"]
)
@pytest.mark.parametrize("status", ["failed", "incomplete", "completed"])
def test_poll_safety_codes_in_the_interaction_errors_are_content_blocked(
    code: str, status: str, tmp_path: Path
) -> None:
    result = _poll(_interaction(status, errors=[{"code": code, "message": "SECRET-PROVIDER-TEXT"}]), tmp_path)
    assert result.done is True
    assert result.error is not None
    assert result.error.code == "content_blocked"
    assert result.video is None


def test_poll_invalid_argument_after_acceptance_is_content_blocked(tmp_path: Path) -> None:
    # The shape Omni returned for a refused prompt in the live run: no safety code, only INVALID_ARGUMENT.
    errors = [{"code": "INVALID_ARGUMENT", "message": "There was a problem processing your request."}]
    result = _poll(_interaction("failed", errors=errors, output_text=""), tmp_path)
    assert result.done is True
    assert result.video is None
    assert result.error is not None
    assert result.error.code == "content_blocked"


def test_invalid_argument_at_submit_is_not_content_blocked() -> None:
    def reject(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"code": 400, "status": "INVALID_ARGUMENT", "message": "bad"}})

    with pytest.raises(VideoError) as exc:
        _provider(reject).submit(REQUEST)
    assert exc.value.code == "video_failed"


def test_poll_safety_code_given_as_a_uri_is_content_blocked(tmp_path: Path) -> None:
    errors = [{"code": "https://errors.example.com/type/IMAGE_SAFETY"}]
    result = _poll(_interaction("failed", errors=errors), tmp_path)
    assert result.error is not None
    assert result.error.code == "content_blocked"


def test_poll_safety_code_on_a_model_output_step_is_content_blocked(tmp_path: Path) -> None:
    step = {"type": "model_output", "error": {"code": 3, "details": [{"reason": "PROHIBITED_CONTENT"}]}}
    result = _poll(_interaction("failed", steps=[step]), tmp_path)
    assert result.error is not None
    assert result.error.code == "content_blocked"


@pytest.mark.parametrize("code", ["payment_required", "quota_exceeded", "failed_precondition"])
def test_poll_credit_codes_in_the_interaction_errors_are_out_of_credit(code: str, tmp_path: Path) -> None:
    result = _poll(_interaction("failed", errors=[{"code": code}]), tmp_path)
    assert result.error is not None
    assert result.error.code == "provider_out_of_credit"


def test_a_blocked_interaction_still_reports_the_usage_it_was_charged(tmp_path: Path) -> None:
    usage = {"total_input_tokens": 40}
    result = _poll(_interaction("failed", errors=[{"code": "safety"}], usage=usage), tmp_path)
    assert result.usage == Usage(input_tokens=40)


# --- V10 table: HTTP errors from the Interactions client (google.genai._interactions family) -----------------


HTTP_TABLE = [
    (402, "payment_required", "provider_out_of_credit"),
    (402, None, "provider_out_of_credit"),
    (429, "quota_exceeded", "provider_out_of_credit"),
    (400, "failed_precondition", "provider_out_of_credit"),
    (400, "safety", "content_blocked"),
    (400, "prohibited_content", "content_blocked"),
    (400, "image_safety", "content_blocked"),
    (400, "image_prohibited_content", "content_blocked"),
    (403, "content_blocked", "content_blocked"),
    (429, None, "provider_unavailable"),
    (429, "rate_limit_exceeded", "provider_unavailable"),
    (500, None, "provider_unavailable"),
    (503, None, "provider_unavailable"),
    (401, None, "provider_unavailable"),
    (403, None, "provider_unavailable"),
    (400, "invalid_request", "video_failed"),
    (404, None, "video_failed"),
]


@pytest.mark.parametrize(("status", "code", "expected"), HTTP_TABLE)
def test_submit_maps_http_errors(status: int, code: str | None, expected: str) -> None:
    with pytest.raises(VideoError) as exc:
        _provider(_error(status, code)).submit(REQUEST)
    assert exc.value.code == expected


@pytest.mark.parametrize(("status", "code", "expected"), HTTP_TABLE)
def test_poll_maps_http_errors(status: int, code: str | None, expected: str, tmp_path: Path) -> None:
    with pytest.raises(VideoError) as exc:
        _poll(_error(status, code), tmp_path)
    assert exc.value.code == expected


@pytest.mark.parametrize("word", ["image", "Image", "picture"])
def test_invalid_request_about_an_image_is_invalid_picture(word: str) -> None:
    with pytest.raises(VideoError) as exc:
        _provider(_error(400, "invalid_request", f"The {word} at input 2 could not be decoded")).submit(REQUEST)
    assert exc.value.code == "invalid_picture"


def test_a_safety_code_wins_over_the_image_wording() -> None:
    with pytest.raises(VideoError) as exc:
        _provider(_error(400, "image_safety", "The image was blocked")).submit(REQUEST)
    assert exc.value.code == "content_blocked"


def test_a_plain_text_error_body_is_still_mapped_by_status() -> None:
    with pytest.raises(VideoError) as exc:
        _provider(lambda _r: httpx.Response(402, text="Payment Required")).submit(REQUEST)
    assert exc.value.code == "provider_out_of_credit"


def test_network_errors_are_provider_unavailable() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    with pytest.raises(VideoError) as exc:
        _provider(refuse).submit(REQUEST)
    assert exc.value.code == "provider_unavailable"


def test_timeouts_are_provider_unavailable() -> None:
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(VideoError) as exc:
        _provider(slow).submit(REQUEST)
    assert exc.value.code == "provider_unavailable"


# --- V10 table: google.genai.errors.APIError family -----------------------------------------------------------


class _RaisingInteractions:
    """Stands in for ``client.interactions`` where the genai-level error family is raised."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    def create(self, **_kwargs: Any) -> Any:
        raise self.error

    def get(self, _id: str, **_kwargs: Any) -> Any:
        raise self.error

    def cancel(self, _id: str, **_kwargs: Any) -> Any:
        raise self.error

    def delete(self, _id: str, **_kwargs: Any) -> Any:
        raise self.error


class _Client:
    def __init__(self, interactions: Any) -> None:
        self.interactions = interactions


def _genai_error(status: int, code: str) -> genai_errors.ClientError:
    return genai_errors.ClientError(status, {"error": {"code": code, "message": "provider text", "status": code}})


GENAI_TABLE = [
    (402, "payment_required", "provider_out_of_credit"),
    (429, "quota_exceeded", "provider_out_of_credit"),
    (400, "failed_precondition", "provider_out_of_credit"),
    (400, "safety", "content_blocked"),
    (400, "image_prohibited_content", "content_blocked"),
    (429, "RESOURCE_EXHAUSTED", "provider_unavailable"),
    (401, "UNAUTHENTICATED", "provider_unavailable"),
    (403, "PERMISSION_DENIED", "provider_unavailable"),
    (400, "invalid_request", "video_failed"),
]


@pytest.mark.parametrize(("status", "code", "expected"), GENAI_TABLE)
def test_genai_api_errors_are_mapped_like_the_interactions_errors(status: int, code: str, expected: str) -> None:
    provider = OmniProvider(_settings(), client=_Client(_RaisingInteractions(_genai_error(status, code))))  # type: ignore[arg-type]
    with pytest.raises(VideoError) as exc:
        provider.submit(REQUEST)
    assert exc.value.code == expected


def test_genai_server_errors_and_image_errors() -> None:
    server = genai_errors.ServerError(503, {"error": {"message": "overloaded", "status": "UNAVAILABLE"}})
    image = genai_errors.ClientError(
        400, {"error": {"message": "Invalid image data", "status": "INVALID_ARGUMENT", "code": 400}}
    )
    for error, expected in ((server, "provider_unavailable"), (image, "invalid_picture")):
        provider = OmniProvider(_settings(), client=_Client(_RaisingInteractions(error)))  # type: ignore[arg-type]
        with pytest.raises(VideoError) as exc:
            provider.submit(REQUEST)
        assert exc.value.code == expected


# --- cancel, forget -------------------------------------------------------------------------------------------


def test_cancel_and_forget_call_the_interaction_endpoints() -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        return httpx.Response(200, json={"id": "int-1", "status": "cancelled"})

    provider = _provider(handler)
    provider.cancel("int-1")
    provider.forget("int-1")

    assert seen == [("POST", "/v1beta/interactions/int-1/cancel"), ("DELETE", "/v1beta/interactions/int-1")]


@pytest.mark.parametrize("operation", ["cancel", "forget"])
def test_cancel_and_forget_are_best_effort(operation: str, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    provider = _provider(_error(500, None, message="SECRET-PROVIDER-TEXT"))
    getattr(provider, operation)("int-1")  # does not raise
    provider = OmniProvider(_settings(), client=_Client(_RaisingInteractions(RuntimeError("SECRET-PROVIDER-TEXT"))))  # type: ignore[arg-type]
    getattr(provider, operation)("int-1")
    assert "SECRET-PROVIDER-TEXT" not in caplog.text


def test_cancel_and_forget_forget_the_remembered_request(tmp_path: Path) -> None:
    provider = _provider(_dispatch(submit=_interaction("in_progress"), get=_interaction(steps=[_video_step()])))
    ref = provider.submit(REQUEST)
    provider.forget(ref)
    assert provider.poll(ref, tmp_path).usage == Usage()


# --- log hygiene ----------------------------------------------------------------------------------------------


def test_logs_carry_exception_types_only(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    caplog.set_level(logging.DEBUG)
    provider = _provider(_error(402, "payment_required", message="SECRET-PROVIDER-TEXT"))
    for call in (lambda: provider.submit(REQUEST), lambda: provider.poll("int-1", tmp_path)):
        with pytest.raises(VideoError):
            call()
    with pytest.raises(VideoError) as exc:
        provider.submit(REQUEST)
    assert str(exc.value) == ""  # the provider message is never carried (research V10)
    for marker in ("SECRET-PROVIDER-TEXT", "MARKER-PROMPT", KEY, _b64(JPEG_1)):
        assert marker not in caplog.text
