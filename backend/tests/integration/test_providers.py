"""Provider wiring (T036): ordered chains, startup checks (FR-012), health (FR-013) and fallback on a job (SC-006)."""

import shutil
import sys
import threading
import time
import types
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from fakes_llm import FakeLLM
from fakes_transcribe import FakeTranscriber
from videokit.core import providers as providers_module
from videokit.core.config import Settings
from videokit.core.llm import AnthropicProvider, LLMChain, OpenAIProvider, ProviderError
from videokit.core.transcribe import (
    AudioFiles,
    LocalWhisperTranscriber,
    OpenAITranscriber,
    ProviderStatus,
    TranscriberChain,
    local_whisper,
)
from videokit.core.worker import Providers
from videokit.main import StartupError, app_from_env, create_app

MakeVideo = Callable[..., Path]
PLACEHOLDER = "placeholder-key-not-real"
TUS_PATCH = {"Tus-Resumable": "1.0.0", "Content-Type": "application/offset+octet-stream", "Upload-Offset": "0"}
VALID: dict[str, Any] = {
    "language": "en",
    "cover_titles": ["Hidden Gem Resort", "Worth The Price?", "Best Pool Ever"],
    "title": "Hotel tour: lobby, pool and rooms",
    "description": "A tour of a small resort.\n\nWe look at the lobby, the pool and a room.",
    "tags": ["hotel review", "travel"],
    "hashtags": ["#travel", "#hotel", "#resort"],
    "chapters": [
        {"start_seconds": 0, "title": "Arrival"},
        {"start_seconds": 12, "title": "Lobby and pool"},
        {"start_seconds": 24, "title": "Rooms"},
    ],
}


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> pytest.MonkeyPatch:
    """A writable DATA_DIR on top of the hermetic environment provided by conftest."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    return monkeypatch


@pytest.fixture
def no_gpu(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local_whisper, "cuda_available", lambda: False)


def _health(app: Any) -> tuple[dict[str, Any], str]:
    with TestClient(app) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    return body, response.text


# --- building the chains -----------------------------------------------------------------------------------------


def test_chains_follow_the_configured_order_and_load_nothing(tmp_path: Path, no_gpu: None) -> None:
    settings = Settings(
        data_dir=tmp_path,
        transcribe_providers=["openai", "local"],
        llm_providers=["openai", "anthropic"],
        anthropic_api_key=SecretStr(PLACEHOLDER),
        openai_api_key=SecretStr(PLACEHOLDER),
        openai_model="example-model",
    )
    built = providers_module.build(settings)

    openai_stt, local = built.transcriber.providers
    openai_llm, anthropic = built.llm.providers
    assert isinstance(openai_stt, OpenAITranscriber)
    assert isinstance(local, LocalWhisperTranscriber)
    assert isinstance(openai_llm, OpenAIProvider)
    assert isinstance(anthropic, AnthropicProvider)
    # No Whisper model and no SDK client (so no network) at startup.
    assert local._model is None
    assert openai_stt._client is None and openai_llm._client is None and anthropic._client is None


# --- health (FR-013) ---------------------------------------------------------------------------------------------


def test_health_reports_each_provider_in_order_without_secrets(env: pytest.MonkeyPatch, no_gpu: None) -> None:
    env.setenv("TRANSCRIBE_PROVIDERS", "local,openai")
    env.setenv("LLM_PROVIDERS", "anthropic,openai")
    env.setenv("ANTHROPIC_API_KEY", PLACEHOLDER)
    env.setenv("OPENAI_API_KEY", PLACEHOLDER)
    env.setenv("OPENAI_MODEL", "example-model")

    body, text = _health(app_from_env())

    assert set(body) == {"status", "providers", "queue_length"}
    assert body["status"] == "ok"
    assert body["queue_length"] == 0
    assert body["providers"] == {
        "transcription": [
            {"name": "local", "ready": True, "detail": "device=cpu, model=large-v3-turbo (not loaded yet)"},
            {"name": "openai", "ready": True, "detail": "model=whisper-1"},
        ],
        "llm": [
            {"name": "anthropic", "ready": True, "detail": "model=claude-opus-5-5"},
            {"name": "openai", "ready": True, "detail": "model=example-model"},
        ],
        "video": [],
    }
    assert PLACEHOLDER not in text


def test_health_shows_the_gpu_device(env: pytest.MonkeyPatch, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local_whisper, "cuda_available", lambda: True)
    env.setenv("ANTHROPIC_API_KEY", PLACEHOLDER)
    body, _ = _health(app_from_env())
    [local] = body["providers"]["transcription"]
    assert local["detail"] == "device=cuda, model=large-v3-turbo (not loaded yet)"


def test_cloud_only_with_placeholder_keys_starts(env: pytest.MonkeyPatch) -> None:
    """The container smoke test starts cloud-only with placeholder keys: no provider is called at startup."""
    env.setenv("TRANSCRIBE_PROVIDERS", "openai")
    env.setenv("LLM_PROVIDERS", "anthropic")
    env.setenv("OPENAI_API_KEY", PLACEHOLDER)
    env.setenv("ANTHROPIC_API_KEY", PLACEHOLDER)

    body, text = _health(app_from_env())

    assert body["providers"] == {
        "transcription": [{"name": "openai", "ready": True, "detail": "model=whisper-1"}],
        "llm": [{"name": "anthropic", "ready": True, "detail": "model=claude-opus-5-5"}],
        "video": [],
    }
    assert PLACEHOLDER not in text


class _BlockingWhisperModel:
    """Stands in for faster-whisper's model; construction (download + load) blocks until released."""

    entered = threading.Event()
    release = threading.Event()

    def __init__(self, model: str, **kwargs: Any) -> None:
        _BlockingWhisperModel.entered.set()
        assert _BlockingWhisperModel.release.wait(10)

    def transcribe(self, audio: str, **kwargs: Any) -> tuple[Any, Any]:
        return iter([]), types.SimpleNamespace(language="en", language_probability=0.9, duration=6.0)


def test_health_shows_the_local_model_loading_live(
    tmp_path: Path, no_gpu: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = types.ModuleType("faster_whisper")
    module.WhisperModel = _BlockingWhisperModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "faster_whisper", module)
    monkeypatch.setattr(_BlockingWhisperModel, "entered", threading.Event())
    monkeypatch.setattr(_BlockingWhisperModel, "release", threading.Event())

    settings = Settings(data_dir=tmp_path / "data", anthropic_api_key=SecretStr(PLACEHOLDER))
    local = LocalWhisperTranscriber(settings)
    chains = Providers(transcriber=TranscriberChain([local]), llm=LLMChain([FakeLLM([])]))
    audio = AudioFiles(wav=tmp_path / "audio.wav", mp3=tmp_path / "audio.mp3")

    def local_status(client: TestClient) -> dict[str, Any]:
        status: dict[str, Any] = client.get("/api/health").json()["providers"]["transcription"][0]
        return status

    with TestClient(create_app(settings, chains, static_dir=tmp_path / "static")) as client:
        worker = threading.Thread(target=local.transcribe, args=(audio,))
        worker.start()
        try:
            assert _BlockingWhisperModel.entered.wait(10)
            assert local_status(client) == {
                "name": "local",
                "ready": False,
                "detail": "device=cpu, model=large-v3-turbo (downloading or loading)",
            }
        finally:
            _BlockingWhisperModel.release.set()
            worker.join(10)
        assert local_status(client) == {"name": "local", "ready": True, "detail": "device=cpu, model=large-v3-turbo"}


# --- startup failures (FR-012) -----------------------------------------------------------------------------------


def _exit_lines(capsys: pytest.CaptureFixture[str]) -> list[str]:
    with pytest.raises(SystemExit) as exc:
        app_from_env()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "Traceback" not in err
    lines = [ln for ln in err.splitlines() if ln]
    assert all(ln.startswith("Configuration error: ") for ln in lines)
    return lines


def test_missing_key_for_a_listed_provider_stops_startup(
    env: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    env.setenv("TRANSCRIBE_PROVIDERS", "openai")
    env.setenv("ANTHROPIC_API_KEY", PLACEHOLDER)
    assert _exit_lines(capsys) == [
        "Configuration error: OPENAI_API_KEY is required when 'openai' is listed in a providers variable"
    ]


def test_empty_provider_list_stops_startup(env: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    env.setenv("TRANSCRIBE_PROVIDERS", " , ")
    env.setenv("ANTHROPIC_API_KEY", PLACEHOLDER)
    assert _exit_lines(capsys) == ["Configuration error: TRANSCRIBE_PROVIDERS must list at least one provider"]


def test_provider_check_failure_stops_startup_naming_the_provider(
    env: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    env.setenv("TRANSCRIBE_PROVIDERS", "openai,local")
    env.setenv("OPENAI_API_KEY", PLACEHOLDER)
    env.setenv("ANTHROPIC_API_KEY", PLACEHOLDER)
    env.setattr(LocalWhisperTranscriber, "check", lambda self: ProviderStatus(ready=False, detail="no usable GPU"))
    assert _exit_lines(capsys) == [
        "Configuration error: transcription provider 'local' (TRANSCRIBE_PROVIDERS) is not ready: no usable GPU"
    ]


def test_the_entry_point_hands_its_checked_chains_to_the_app(env: pytest.MonkeyPatch, no_gpu: None) -> None:
    env.setenv("ANTHROPIC_API_KEY", PLACEHOLDER)
    chains: Providers = app_from_env().state.providers
    assert [p.name for p in chains.transcriber.providers] == ["local"]
    assert [p.name for p in chains.llm.providers] == ["anthropic"]


class _NotReadyLLM(FakeLLM):
    def check(self) -> ProviderStatus:
        return ProviderStatus(ready=False, detail="missing OPENAI_MODEL")


def test_app_refuses_to_start_with_a_provider_that_is_not_ready(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = Settings(data_dir=tmp_path / "data", anthropic_api_key=SecretStr(PLACEHOLDER))
    chains = Providers(
        transcriber=TranscriberChain([FakeTranscriber(name="local")]),
        llm=LLMChain([FakeLLM([], name="anthropic"), _NotReadyLLM([], name="openai")]),
    )
    app = create_app(settings, chains, static_dir=tmp_path / "static")
    with pytest.raises(StartupError) as exc, TestClient(app):
        pass
    message = "llm provider 'openai' (LLM_PROVIDERS) is not ready: missing OPENAI_MODEL"
    assert exc.value.problems == [message]
    assert capsys.readouterr().err == f"Configuration error: {message}\n"


# --- fallback on a real job (SC-006) -----------------------------------------------------------------------------


@pytest.fixture
def fallback_client(tmp_path: Path) -> Iterator[tuple[TestClient, Providers]]:
    settings = Settings(data_dir=tmp_path / "data", anthropic_api_key=SecretStr(PLACEHOLDER), max_upload_gb=1)
    chains = Providers(
        transcriber=TranscriberChain(
            [
                FakeTranscriber(name="local", error=ProviderError("model failed")),
                FakeTranscriber(name="openai", model="whisper-1"),
            ]
        ),
        llm=LLMChain(
            [
                FakeLLM([ProviderError("overloaded")], name="anthropic", model="claude-opus-5-5"),
                FakeLLM([VALID], name="openai", model="example-model"),
            ]
        ),
    )
    with TestClient(create_app(settings, chains, static_dir=tmp_path / "static")) as client:
        yield client, chains


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_failing_primaries_fall_back_and_the_job_records_the_fallbacks(
    fallback_client: tuple[TestClient, Providers], make_video: MakeVideo
) -> None:
    client, chains = fallback_client
    data = make_video(audio=True, colors=("red", "blue", "green"), segment_s=12.0).read_bytes()
    created = client.post("/api/tools/youtube_prep/jobs", json={"size": len(data)})
    job_id = created.json()["job_id"]
    assert client.patch(f"/api/uploads/{job_id}", content=data, headers=TUS_PATCH).status_code == 204

    deadline = time.monotonic() + 20
    while (body := client.get(f"/api/jobs/{job_id}").json())["state"] not in ("done", "failed", "cancelled"):
        assert time.monotonic() < deadline, "job did not finish in time"
        time.sleep(0.05)

    assert body["state"] == "done", body["failure_reason"]
    assert body["providers"] == {"transcription": "openai", "llm": "openai:example-model"}
    primary_stt, _ = chains.transcriber.providers
    primary_llm, _ = chains.llm.providers
    assert isinstance(primary_stt, FakeTranscriber) and len(primary_stt.calls) == 1
    assert isinstance(primary_llm, FakeLLM) and len(primary_llm.requests) == 1
    assert body["usage"]["audio_minutes"] == 0.5
    assert body["usage"]["cost_usd"] is None  # the fallback LLM model has no known price
