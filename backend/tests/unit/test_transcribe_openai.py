import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import httpx2
import openai
import pytest
from pydantic import SecretStr

from videokit.core.config import Settings
from videokit.core.transcribe import AudioFiles, ProviderError, ProviderStatus, Transcriber
from videokit.core.transcribe import openai as openai_module
from videokit.core.transcribe.openai import OpenAITranscriber

TIMEOUT = openai.Timeout(1800, connect=10)  # long reads for slow servers, but an unreachable host fails fast


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {"openai_api_key": SecretStr("test-key")}
    values.update(overrides)
    return Settings(**values)


def _audio(tmp_path: Path) -> AudioFiles:
    return AudioFiles(mp3=tmp_path / "audio.mp3")


def _seg(start: float, end: float, text: str) -> SimpleNamespace:
    return SimpleNamespace(start=start, end=end, text=text)


def _verbose(segments: list[SimpleNamespace] | None, duration: float = 60.0, language: str = "english") -> Any:
    return SimpleNamespace(segments=segments, duration=duration, language=language, text="")


def _client(*responses: Any) -> MagicMock:
    client = MagicMock()
    client.audio.transcriptions.create.side_effect = list(responses)
    return client


def _make_mp3(path: Path, seconds: int) -> None:
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    subprocess.run([*cmd, "-ac", "1", "-b:a", "32k", str(path)], check=True)


def test_is_a_transcriber() -> None:
    assert isinstance(OpenAITranscriber(_settings(), client=MagicMock()), Transcriber)


def test_check_requires_api_key() -> None:
    ok = OpenAITranscriber(_settings()).check()
    assert ok.ready is True
    assert ok.detail == "model=whisper-1"
    missing = ProviderStatus(ready=False, detail="missing OPENAI_API_KEY or OPENAI_TRANSCRIBE_API_KEY")
    assert OpenAITranscriber(_settings(openai_api_key=None)).check() == missing
    assert OpenAITranscriber(_settings(openai_api_key=SecretStr("  "))).check() == missing


def test_transcribe_single_request(tmp_path: Path) -> None:
    audio = _audio(tmp_path)
    audio.mp3.write_bytes(b"x" * 100)
    client = _client(_verbose([_seg(0.0, 2.0, " Hello there "), _seg(2.0, 3.0, "  ")], duration=90.0))
    t = OpenAITranscriber(_settings(openai_transcribe_model="whisper-x"), client=client).transcribe(audio)

    client.audio.transcriptions.create.assert_called_once()
    kwargs = client.audio.transcriptions.create.call_args.kwargs
    assert kwargs["model"] == "whisper-x"
    assert kwargs["response_format"] == "verbose_json"
    assert kwargs["timestamp_granularities"] == ["segment"]
    assert kwargs["file"].name.endswith("audio.mp3")
    assert [(s.start, s.end, s.text) for s in t.segments] == [(0.0, 2.0, "Hello there")]
    assert t.language == "en"
    assert t.audio_minutes == pytest.approx(1.5)
    assert t.no_speech is False
    assert t.provider == "openai"
    assert t.model == "whisper-x"  # reported so core can price the transcription


def test_no_speech_when_no_words(tmp_path: Path) -> None:
    audio = _audio(tmp_path)
    audio.mp3.write_bytes(b"x")
    for segments in (None, [], [_seg(0, 1, " ... ")]):
        t = OpenAITranscriber(_settings(), client=_client(_verbose(segments))).transcribe(audio)
        assert t.segments == []
        assert t.no_speech is True


@pytest.mark.parametrize(
    ("api_language", "code"),
    [("english", "en"), ("German", "de"), ("de", "de"), ("chinese", "zh"), ("klingon", None), ("", None)],
)
def test_language_mapping(tmp_path: Path, api_language: str, code: str | None) -> None:
    audio = _audio(tmp_path)
    audio.mp3.write_bytes(b"x")
    client = _client(_verbose([_seg(0, 1, "hi")], language=api_language))
    assert OpenAITranscriber(_settings(), client=client).transcribe(audio).language == code


def test_plan_chunks_covers_the_whole_duration() -> None:
    plan = openai_module.plan_chunks(100.0, 1000, max_bytes=300)
    assert plan[0][0] == 0.0
    assert all(b[0] == pytest.approx(a[0] + a[1]) for a, b in zip(plan, plan[1:], strict=False))
    assert sum(length for _, length in plan) == pytest.approx(100.0)
    assert all(length * 10 <= 300 for _, length in plan)  # 10 bytes per second


def test_chunking_restores_offsets_and_concatenates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    audio = _audio(tmp_path)
    _make_mp3(audio.mp3, 30)  # ~120 KB at 32 kbps
    limit = audio.mp3.stat().st_size // 3
    monkeypatch.setattr(openai_module, "MAX_UPLOAD_BYTES", limit)
    sizes: list[int] = []
    responses: list[Any] = []

    def fake_create(**kwargs: Any) -> Any:
        sizes.append(Path(kwargs["file"].name).stat().st_size)
        return responses.pop(0)

    plan = openai_module.plan_chunks(30.0, audio.mp3.stat().st_size, max_bytes=limit)
    assert len(plan) >= 4  # a safety margin below the limit means more than three chunks
    responses.extend(
        _verbose([_seg(0.5, 1.0, f"a{i}"), _seg(1.0, 2.0, f"b{i}")], duration=length, language="english")
        for i, (_, length) in enumerate(plan)
    )
    client = MagicMock()
    client.audio.transcriptions.create.side_effect = fake_create
    t = OpenAITranscriber(_settings(), client=client).transcribe(audio)

    assert client.audio.transcriptions.create.call_count == len(plan)
    assert all(size <= limit for size in sizes)
    assert [s.text for s in t.segments] == [x for i in range(len(plan)) for x in (f"a{i}", f"b{i}")]
    expected_starts = [start + off for start, _ in plan for off in (0.5, 1.0)]
    assert [s.start for s in t.segments] == pytest.approx(expected_starts)
    assert t.segments[3].end == pytest.approx(plan[1][0] + 2.0)
    assert t.audio_minutes == pytest.approx(sum(length for _, length in plan) / 60)
    assert t.language == "en"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["audio.mp3"]  # temporary chunks are removed


def test_api_errors_become_provider_errors(tmp_path: Path) -> None:
    audio = _audio(tmp_path)
    audio.mp3.write_bytes(b"x")
    client = MagicMock()
    client.audio.transcriptions.create.side_effect = openai.APIConnectionError(
        request=httpx2.Request("POST", "https://api.example.com/v1/audio/transcriptions")
    )
    with pytest.raises(ProviderError, match="APIConnectionError"):
        OpenAITranscriber(_settings(), client=client).transcribe(audio)


def test_ffmpeg_failure_becomes_provider_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    audio = _audio(tmp_path)
    audio.mp3.write_bytes(b"not an mp3" * 100)
    monkeypatch.setattr(openai_module, "MAX_UPLOAD_BYTES", 10)
    with pytest.raises(ProviderError, match="split"):
        OpenAITranscriber(_settings(), client=MagicMock()).transcribe(audio)


def test_lazy_client_uses_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[dict[str, Any]] = []

    class FakeOpenAI:
        def __init__(self, **kwargs: Any) -> None:
            created.append(kwargs)

    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    transcriber = OpenAITranscriber(_settings())
    assert created == []
    first = transcriber._sdk()
    assert transcriber._sdk() is first
    assert created == [{"api_key": "test-key", "base_url": "https://api.openai.com/v1", "timeout": TIMEOUT}]


def _created(monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> dict[str, Any]:
    created: list[dict[str, Any]] = []

    class FakeOpenAI:
        def __init__(self, **kwargs: Any) -> None:
            created.append(kwargs)

    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    OpenAITranscriber(_settings(**overrides))._sdk()
    return created[0]


def test_openai_base_url_env_never_redirects_the_openai_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "http://stt.example.com:8000/v1")
    transcriber = OpenAITranscriber(_settings())
    client = transcriber._sdk()
    assert str(client.base_url) == "https://api.openai.com/v1/"
    assert client.timeout == TIMEOUT
    assert transcriber.check().detail == "model=whisper-1"  # still OpenAI's API, still priced


def test_client_for_openai_prefers_transcribe_key(monkeypatch: pytest.MonkeyPatch) -> None:
    kwargs = _created(monkeypatch, openai_transcribe_api_key=SecretStr("t-key"))
    assert kwargs == {"api_key": "t-key", "base_url": "https://api.openai.com/v1", "timeout": TIMEOUT}


def test_client_for_openai_address_uses_openai_key(monkeypatch: pytest.MonkeyPatch) -> None:
    kwargs = _created(monkeypatch, openai_transcribe_url="https://api.openai.com/v1")
    assert kwargs["api_key"] == "test-key"
    assert kwargs["base_url"] == "https://api.openai.com/v1"


def test_client_for_self_hosted_never_gets_openai_key(monkeypatch: pytest.MonkeyPatch) -> None:
    kwargs = _created(monkeypatch, openai_transcribe_url="http://stt.example.com:8000/v1")
    assert kwargs["base_url"] == "http://stt.example.com:8000/v1"
    assert kwargs["timeout"] == TIMEOUT
    assert kwargs["api_key"] != "test-key"
    assert kwargs["api_key"]  # a fixed placeholder: the SDK refuses an empty key


def test_client_for_self_hosted_uses_transcribe_key(monkeypatch: pytest.MonkeyPatch) -> None:
    kwargs = _created(
        monkeypatch, openai_transcribe_url="http://stt.example.com/v1", openai_transcribe_api_key=SecretStr("t-key")
    )
    assert kwargs["api_key"] == "t-key"


def test_self_hosted_transcript_flag(tmp_path: Path) -> None:
    audio = _audio(tmp_path)
    audio.mp3.write_bytes(b"x" * 100)
    hosted = OpenAITranscriber(
        _settings(openai_transcribe_url="http://stt.example.com/v1"),
        client=_client(_verbose([_seg(0.0, 1.0, "hi")])),
    ).transcribe(audio)
    assert hosted.self_hosted is True
    assert (hosted.provider, hosted.model) == ("openai", "whisper-1")
    cloud = OpenAITranscriber(_settings(), client=_client(_verbose([_seg(0.0, 1.0, "hi")]))).transcribe(audio)
    assert cloud.self_hosted is False


def test_check_self_hosted_needs_no_key_and_hides_address() -> None:
    settings = _settings(openai_api_key=None, openai_transcribe_url="http://stt.example.com/v1")
    status = OpenAITranscriber(settings).check()
    assert status == ProviderStatus(ready=True, detail="model=whisper-1, self-hosted")
    assert "example.com" not in status.detail


def test_check_openai_address_without_key_is_missing() -> None:
    settings = _settings(openai_api_key=None, openai_transcribe_url="https://api.openai.com/v1")
    missing = ProviderStatus(ready=False, detail="missing OPENAI_API_KEY or OPENAI_TRANSCRIBE_API_KEY")
    assert OpenAITranscriber(settings).check() == missing


def test_check_openai_accepts_transcribe_key_only() -> None:
    settings = _settings(openai_api_key=None, openai_transcribe_api_key=SecretStr("t-key"))
    assert OpenAITranscriber(settings).check().ready is True
