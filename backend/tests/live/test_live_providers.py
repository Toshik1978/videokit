"""Opt-in smoke tests against the real providers (`pytest -m live`).

Each test is skipped unless its requirement is present: an API key (and model) or server address in the
environment. Fixtures are tiny to keep cost minimal. Nothing here prints keys or model output.
"""

import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from videokit.core.config import Settings
from videokit.core.llm.anthropic import AnthropicProvider
from videokit.core.llm.base import GenerationRequest, LabeledFrame, LLMProvider, Turn
from videokit.core.llm.openai import OpenAIProvider
from videokit.core.transcribe.base import AudioFiles
from videokit.core.transcribe.openai import OpenAITranscriber
from videokit.tools.youtube_prep.prompt import SYSTEM_PROMPT, generate_turn
from videokit.tools.youtube_prep.schema import OUTPUT_SCHEMA, ModelOutput

pytestmark = pytest.mark.live

MakeVideo = Callable[..., Path]

requires_anthropic = pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY not set")
requires_openai_llm = pytest.mark.skipif(
    not (os.environ.get("OPENAI_API_KEY") and os.environ.get("OPENAI_MODEL")),
    reason="OPENAI_API_KEY and OPENAI_MODEL not set",
)
requires_openai_key = pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
requires_self_hosted = pytest.mark.skipif(
    not os.environ.get("OPENAI_TRANSCRIBE_URL"), reason="OPENAI_TRANSCRIBE_URL not set"
)

TRANSCRIPT = "[0:00] Welcome to a short demo of a tiny colorful clip.\n[0:03] Now the screen turns blue."


def _jpeg(video: Path, at_s: float, out: Path) -> bytes:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-ss", str(at_s), "-i", str(video), "-frames:v", "1", str(out)],
        check=True,
        capture_output=True,
    )
    return out.read_bytes()


def _request(make_video: MakeVideo, tmp_path: Path) -> GenerationRequest:
    video = make_video()
    frames = [LabeledFrame(t, _jpeg(video, t, tmp_path / f"f{i}.jpg")) for i, t in enumerate((0.0, 4.0))]
    return GenerationRequest(
        system=SYSTEM_PROMPT,
        context_text=TRANSCRIPT,
        frames=frames,
        schema=OUTPUT_SCHEMA,
        turns=[Turn("user", generate_turn())],
    )


def _assert_generates(provider: LLMProvider, req: GenerationRequest) -> None:
    response = provider.generate(req)
    ModelOutput.model_validate(response.data)  # schema-valid JSON (rules such as chapter count are not asserted)
    assert response.usage.input_tokens + response.usage.cache_read_tokens > 0
    assert response.usage.output_tokens > 0
    assert response.provider.startswith(f"{provider.name}:")


@requires_anthropic
def test_anthropic_generates_schema_valid_output(make_video: MakeVideo, tmp_path: Path) -> None:
    provider = AnthropicProvider(Settings(anthropic_effort="low"))
    _assert_generates(provider, _request(make_video, tmp_path))


@requires_openai_llm
def test_openai_generates_schema_valid_output(make_video: MakeVideo, tmp_path: Path) -> None:
    provider = OpenAIProvider(Settings())
    _assert_generates(provider, _request(make_video, tmp_path))


def _speech_free_audio(make_video: MakeVideo, tmp_path: Path) -> AudioFiles:
    video = make_video(colors=("red",), segment_s=2.0, audio=True)  # a sine tone, no speech
    mp3 = tmp_path / "audio.mp3"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(video), "-vn", "-ac", "1", "-b:a", "32k", str(mp3)],
        check=True,
        capture_output=True,
    )
    return AudioFiles(mp3=mp3)


@requires_openai_key
def test_openai_transcription_of_speech_free_clip_is_no_speech(make_video: MakeVideo, tmp_path: Path) -> None:
    transcript = OpenAITranscriber(Settings()).transcribe(_speech_free_audio(make_video, tmp_path))
    assert transcript.no_speech is True
    assert transcript.segments == []
    assert transcript.provider == "openai"
    assert transcript.audio_minutes > 0


def _spoken_audio(tmp_path: Path) -> AudioFiles:
    """A few seconds of synthesized English speech (needs espeak-ng on the host), as MP3."""
    espeak = shutil.which("espeak-ng") or shutil.which("espeak")
    if espeak is None:
        pytest.skip("espeak-ng not installed (needed to generate a spoken fixture)")
    speech = tmp_path / "speech.wav"
    text = "Welcome to a short demo. Today we look at how a small video tool works. Thank you for watching."
    subprocess.run([espeak, "-v", "en", "-s", "140", "-w", str(speech), text], check=True, capture_output=True)
    mp3 = tmp_path / "audio.mp3"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(speech), "-ac", "1", "-b:a", "32k", str(mp3)],
        check=True,
        capture_output=True,
    )
    return AudioFiles(mp3=mp3)


@requires_self_hosted
def test_self_hosted_server_transcribes_speech(tmp_path: Path) -> None:
    transcript = OpenAITranscriber(Settings()).transcribe(_spoken_audio(tmp_path))
    assert transcript.self_hosted is True
    assert transcript.provider == "openai"
    assert transcript.no_speech is False
    assert transcript.segments
    starts = [seg.start for seg in transcript.segments]
    assert starts == sorted(starts)
    assert all(seg.end >= seg.start for seg in transcript.segments)
    assert transcript.language == "en"
    assert transcript.audio_minutes > 0
