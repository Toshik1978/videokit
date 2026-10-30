from pathlib import Path

import pytest

from fakes_transcribe import FakeTranscriber
from videokit.core.errors import AppError
from videokit.core.transcribe import (
    AllProvidersFailed,
    AudioFiles,
    ProviderError,
    ProviderStatus,
    Segment,
    Transcriber,
    TranscriberChain,
)

AUDIO = AudioFiles(mp3=Path("/data/jobs/x/audio.mp3"))


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
    first = FakeTranscriber(name="fake", error=ProviderError("GPU out of memory"))
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
    chain = TranscriberChain([FakeTranscriber(name="fake"), FakeTranscriber(name="openai")])
    assert chain.name == "fake,openai"
    assert chain.check().ready is True


def test_the_in_process_whisper_transcriber_is_gone() -> None:
    import videokit.core.transcribe as transcribe

    assert not hasattr(transcribe, "LocalWhisperTranscriber")
    assert not hasattr(transcribe, "local_whisper")
