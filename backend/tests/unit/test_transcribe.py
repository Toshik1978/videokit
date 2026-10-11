import sys
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from fakes_transcribe import FakeTranscriber
from videokit.core.config import Settings
from videokit.core.errors import AppError
from videokit.core.transcribe import (
    AllProvidersFailed,
    AudioFiles,
    LocalWhisperTranscriber,
    ProviderError,
    ProviderStatus,
    Segment,
    Transcriber,
    TranscriberChain,
    Transcript,
    local_whisper,
)

AUDIO = AudioFiles(wav=Path("/data/jobs/x/audio.wav"), mp3=Path("/data/jobs/x/audio.mp3"))


# --- base types --------------------------------------------------------------------------------


def test_all_providers_failed_is_user_safe_app_error() -> None:
    err = AllProvidersFailed("transcription")
    assert isinstance(err, AppError)
    assert err.stage == "transcription"
    assert err.code == "providers_failed"
    assert "transcription" in err.message


def test_provider_status_defaults() -> None:
    assert ProviderStatus(ready=True) == ProviderStatus(ready=True, detail="")


# --- FakeTranscriber ---------------------------------------------------------------------------


def test_fake_transcriber_returns_configured_transcript() -> None:
    fake = FakeTranscriber(segments=[Segment(0.0, 2.5, "Hello there")], language="de", audio_minutes=1.5)
    assert isinstance(fake, Transcriber)
    t = fake.transcribe(AUDIO)
    assert t.language == "de"
    assert t.segments == [Segment(0.0, 2.5, "Hello there")]
    assert t.audio_minutes == 1.5
    assert t.no_speech is False
    assert t.provider == "fake"
    assert fake.calls == [AUDIO]


def test_fake_transcriber_no_speech_and_failure() -> None:
    assert FakeTranscriber(no_speech=True).transcribe(AUDIO).no_speech is True
    assert FakeTranscriber(no_speech=True).transcribe(AUDIO).segments == []
    failing = FakeTranscriber(name="broken", error=ProviderError("boom"))
    with pytest.raises(ProviderError):
        failing.transcribe(AUDIO)
    assert failing.check() == ProviderStatus(ready=True, detail="fake")


# --- chain -------------------------------------------------------------------------------------


def test_chain_uses_first_provider_that_succeeds() -> None:
    first = FakeTranscriber(name="local", error=ProviderError("GPU out of memory"))
    second = FakeTranscriber(name="openai", segments=[Segment(0.0, 1.0, "hi")])
    third = FakeTranscriber(name="never")
    chain = TranscriberChain([first, second, third])
    assert isinstance(chain, Transcriber)

    t = chain.transcribe(AUDIO)

    assert t.provider == "openai"
    assert len(first.calls) == 1
    assert len(second.calls) == 1
    assert third.calls == []


def test_chain_falls_back_on_any_exception() -> None:
    chain = TranscriberChain([FakeTranscriber(name="a", error=RuntimeError("x")), FakeTranscriber(name="b")])
    assert chain.transcribe(AUDIO).provider == "b"


def test_chain_raises_all_providers_failed() -> None:
    chain = TranscriberChain(
        [FakeTranscriber(name="a", error=ProviderError("x")), FakeTranscriber(name="b", error=OSError())]
    )
    with pytest.raises(AllProvidersFailed) as info:
        chain.transcribe(AUDIO)
    assert info.value.stage == "transcription"


def test_chain_requires_providers_and_reports_status() -> None:
    with pytest.raises(ValueError):
        TranscriberChain([])
    chain = TranscriberChain([FakeTranscriber(name="local"), FakeTranscriber(name="openai")])
    assert chain.name == "local,openai"
    assert chain.check().ready is True


# --- local whisper -----------------------------------------------------------------------------


@dataclass
class _Seg:
    start: float
    end: float
    text: str


@dataclass
class _Info:
    language: str
    language_probability: float
    duration: float


class _FakeWhisperModel:
    instances: list[_FakeWhisperModel] = []
    segments: list[_Seg] = []
    info = _Info("en", 0.97, 90.0)

    def __init__(self, model: str, **kwargs: Any) -> None:
        self.model = model
        self.kwargs = kwargs
        self.transcribe_calls: list[tuple[str, dict[str, Any]]] = []
        _FakeWhisperModel.instances.append(self)

    def transcribe(self, audio: str, **kwargs: Any) -> tuple[Any, _Info]:
        self.transcribe_calls.append((audio, kwargs))
        return iter(self.segments), self.info


@pytest.fixture
def whisper(monkeypatch: pytest.MonkeyPatch) -> type[_FakeWhisperModel]:
    module = types.ModuleType("faster_whisper")
    module.WhisperModel = _FakeWhisperModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "faster_whisper", module)
    monkeypatch.setattr(_FakeWhisperModel, "instances", [])
    monkeypatch.setattr(
        _FakeWhisperModel, "segments", [_Seg(0.0, 4.2, " Welcome to the tour. "), _Seg(4.2, 9.0, "Look.")]
    )
    monkeypatch.setattr(_FakeWhisperModel, "info", _Info("en", 0.97, 90.0))
    return _FakeWhisperModel


def _settings(tmp_path: Path, device: str = "auto") -> Settings:
    return Settings(data_dir=tmp_path, whisper_model="large-v3-turbo", whisper_device=device)


@pytest.mark.parametrize(
    ("device", "cuda", "expected"),
    [
        ("auto", True, ("cuda", "float16")),
        ("auto", False, ("cpu", "int8")),
        ("cuda", True, ("cuda", "float16")),
        ("cpu", True, ("cpu", "int8")),
    ],
)
def test_local_whisper_device_selection(
    whisper: type[_FakeWhisperModel],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    device: str,
    cuda: bool,
    expected: tuple[str, str],
) -> None:
    monkeypatch.setattr(local_whisper, "cuda_available", lambda: cuda)
    LocalWhisperTranscriber(_settings(tmp_path, device)).transcribe(AUDIO)
    (model,) = whisper.instances
    assert model.model == "large-v3-turbo"
    assert (model.kwargs["device"], model.kwargs["compute_type"]) == expected
    assert model.kwargs["download_root"] == str(tmp_path / "models")


def test_local_whisper_transcribes_with_vad(
    whisper: type[_FakeWhisperModel], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(local_whisper, "cuda_available", lambda: False)
    transcriber = LocalWhisperTranscriber(_settings(tmp_path))
    assert transcriber.name == "local"
    assert isinstance(transcriber, Transcriber)

    t = transcriber.transcribe(AUDIO)

    audio, kwargs = whisper.instances[0].transcribe_calls[0]
    assert audio == str(AUDIO.wav)
    assert kwargs["vad_filter"] is True
    assert t == Transcript(
        language="en",
        language_probability=0.97,
        segments=[Segment(0.0, 4.2, "Welcome to the tour."), Segment(4.2, 9.0, "Look.")],
        audio_minutes=1.5,
        no_speech=False,
        provider="local",
        model="large-v3-turbo",
    )


def test_local_whisper_caches_the_model(
    whisper: type[_FakeWhisperModel], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(local_whisper, "cuda_available", lambda: False)
    transcriber = LocalWhisperTranscriber(_settings(tmp_path))
    transcriber.transcribe(AUDIO)
    transcriber.transcribe(AUDIO)
    assert len(whisper.instances) == 1
    assert len(whisper.instances[0].transcribe_calls) == 2


@pytest.mark.parametrize("texts", [[], ["  "], ["...", " ♪ "]])
def test_local_whisper_empty_transcript_is_no_speech(
    whisper: type[_FakeWhisperModel], monkeypatch: pytest.MonkeyPatch, tmp_path: Path, texts: list[str]
) -> None:
    monkeypatch.setattr(local_whisper, "cuda_available", lambda: False)
    monkeypatch.setattr(whisper, "segments", [_Seg(float(i), float(i + 1), s) for i, s in enumerate(texts)])
    t = LocalWhisperTranscriber(_settings(tmp_path)).transcribe(AUDIO)
    assert t.no_speech is True
    assert t.segments == []
    assert t.audio_minutes == 1.5


def test_local_whisper_does_not_import_faster_whisper_eagerly(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setitem(sys.modules, "faster_whisper", None)  # any import attempt would raise
    transcriber = LocalWhisperTranscriber(_settings(tmp_path, "cpu"))
    assert transcriber.check().ready is True
    with pytest.raises(ImportError):
        transcriber.transcribe(AUDIO)


def test_local_whisper_check(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(local_whisper, "cuda_available", lambda: False)
    assert LocalWhisperTranscriber(_settings(tmp_path, "cpu")).check() == ProviderStatus(
        ready=True, detail="device=cpu, model=large-v3-turbo (not loaded yet)"
    )
    assert LocalWhisperTranscriber(_settings(tmp_path, "auto")).check().ready is True
    assert LocalWhisperTranscriber(_settings(tmp_path, "cuda")).check() == ProviderStatus(
        ready=False, detail="WHISPER_DEVICE is 'cuda' but no usable GPU was found"
    )
    monkeypatch.setattr(local_whisper, "cuda_available", lambda: True)
    assert LocalWhisperTranscriber(_settings(tmp_path, "auto")).check() == ProviderStatus(
        ready=True, detail="device=cuda, model=large-v3-turbo (not loaded yet)"
    )


def test_local_whisper_check_after_loading(
    whisper: type[_FakeWhisperModel], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(local_whisper, "cuda_available", lambda: False)
    transcriber = LocalWhisperTranscriber(_settings(tmp_path))
    transcriber.transcribe(AUDIO)
    assert transcriber.check() == ProviderStatus(ready=True, detail="device=cpu, model=large-v3-turbo")


def test_local_whisper_failed_load_is_not_stuck_loading(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(local_whisper, "cuda_available", lambda: False)
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    transcriber = LocalWhisperTranscriber(_settings(tmp_path))
    with pytest.raises(ImportError):
        transcriber.transcribe(AUDIO)
    assert transcriber.check() == ProviderStatus(ready=True, detail="device=cpu, model=large-v3-turbo (not loaded yet)")
