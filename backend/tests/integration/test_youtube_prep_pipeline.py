"""YouTube Prep pipeline with real ffmpeg extraction and fake transcription/LLM providers (T025)."""

import contextlib
import copy
import json
import shutil
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from fakes_llm import FakeLLM
from fakes_transcribe import FakeTranscriber
from videokit.core import pricing
from videokit.core.config import Settings
from videokit.core.errors import AppError
from videokit.core.jobs import Job, JobState, JobStore
from videokit.core.llm import LLMChain, Usage
from videokit.core.media import MediaArtifacts, MediaService
from videokit.core.registry import JobCancelled, Tool
from videokit.core.transcribe import AudioFiles, Segment, TranscriberChain, Transcript
from videokit.core.worker import Context, Providers, WorkerJobHandle
from videokit.main import create_app
from videokit.tools.youtube_prep import TOOL, languages, pipeline
from videokit.tools.youtube_prep.languages import LanguageSetting
from videokit.tools.youtube_prep.prompt import NO_SPEECH_TEXT, SYSTEM_PROMPT
from videokit.tools.youtube_prep.schema import OUTPUT_SCHEMA, YouTubePrepResult

MakeVideo = Callable[..., Path]
pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")

MODEL = "claude-opus-5-5"
LONG = {"colors": ("red", "blue", "green"), "segment_s": 12.0}  # 36 s, long enough for chapters
VALID: dict[str, Any] = {
    "language": "en",
    "cover_titles": ["Hidden Gem Resort", "Worth The  Price?", "Best Pool Ever"],
    "title": "Hotel tour: lobby, pool and rooms",
    "description": "A tour of a small resort.\n\nWe look at the lobby, the pool and a room.",
    "tags": ["hotel review", "travel", "Travel"],
    "hashtags": ["travel", "#hotel", "#re view"],
    "chapters": [
        {"start_seconds": 0, "title": "Arrival"},
        {"start_seconds": 12, "title": "Lobby - and pool"},
        {"start_seconds": 24.5, "title": "Rooms"},
    ],
}
TOO_LONG_TITLE = "x" * 120


def _invalid() -> dict[str, Any]:
    data = copy.deepcopy(VALID)
    data["title"] = TOO_LONG_TITLE
    return data


class RecordingHandle(WorkerJobHandle):
    """The worker's handle, also remembering the stages the tool moved through."""

    def __init__(self, store: JobStore, job_id: str) -> None:
        super().__init__(store, job_id)
        self.states: list[str] = []

    def set_state(self, state: str, **fields: Any) -> None:
        self.states.append(state)
        super().set_state(state, **fields)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        transcribe_providers=["openai"],
        openai_transcribe_api_key=SecretStr("stt-test"),
        data_dir=tmp_path / "data",
        anthropic_api_key=SecretStr("sk-test"),
        max_upload_gb=1,
    )


@pytest.fixture
def store(settings: Settings) -> JobStore:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return JobStore(settings.data_dir, settings.job_ttl_hours, settings.max_upload_bytes)


def _queue(store: JobStore, video: Path, setting: LanguageSetting | None = None) -> str:
    data = video.read_bytes()
    job = store.create("youtube_prep", len(data))
    job_dir = store.job_dir(job.id)
    job_dir.mkdir(parents=True)
    (job_dir / "source.bin").write_bytes(data)
    if setting is not None:
        languages.write_setting(job_dir, setting)
    store.set_upload_offset(job.id, len(data))
    store.set_state(job.id, JobState.QUEUED)
    return job.id


async def _run(
    store: JobStore,
    job_id: str,
    settings: Settings,
    transcriber: Any,
    llm: FakeLLM,
) -> RecordingHandle:
    handle = RecordingHandle(store, job_id)
    ctx = Context(
        media=MediaService(settings),
        transcriber=TranscriberChain([transcriber]),
        llm=LLMChain([llm]),
        settings=settings,
    )
    await TOOL.run(handle, ctx)
    return handle


def _job(store: JobStore, job_id: str) -> Job:
    job = store.get(job_id)
    assert job is not None
    return job


def _result(store: JobStore, job_id: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((store.job_dir(job_id) / "result.json").read_text(encoding="utf-8"))
    return data


def _media(store: JobStore, job_id: str) -> MediaArtifacts:
    return MediaArtifacts.model_validate_json((store.job_dir(job_id) / "media.json").read_text(encoding="utf-8"))


# --- the tool ----------------------------------------------------------------------------------------------------


def test_tool_is_registered_as_youtube_prep() -> None:
    assert isinstance(TOOL, Tool)
    assert TOOL.id == "youtube_prep"
    assert TOOL.name == "YouTube Prep"
    assert TOOL.description


# --- happy path --------------------------------------------------------------------------------------------------


async def test_happy_path_writes_the_result_and_records_the_job(
    store: JobStore, settings: Settings, make_video: MakeVideo
) -> None:
    job_id = _queue(store, make_video(audio=True, **LONG))
    transcriber = FakeTranscriber(name="fake")
    llm = FakeLLM([VALID], model=MODEL)

    handle = await _run(store, job_id, settings, transcriber, llm)

    assert handle.states == ["extracting", "transcribing", "generating"]
    result = _result(store, job_id)
    YouTubePrepResult.model_validate(result)
    assert result == {
        "language": "en",
        "notices": [],
        "cover_titles": ["Hidden Gem Resort", "Worth The Price?", "Best Pool Ever"],
        "title": "Hotel tour: lobby, pool and rooms",
        "description": "A tour of a small resort.\n\nWe look at the lobby, the pool and a room.",
        "tags": ["hotel review", "travel"],
        "hashtags": ["#travel", "#hotel", "#review"],
        "chapters": [
            {"start_seconds": 0.0, "title": "Arrival"},
            {"start_seconds": 12.0, "title": "Lobby and pool"},
            {"start_seconds": 24.5, "title": "Rooms"},
        ],
        "rendered": {
            "cover_titles": "Hidden Gem Resort\nWorth The Price?\nBest Pool Ever",
            "title": "Hotel tour: lobby, pool and rooms",
            "description": "A tour of a small resort.\n\nWe look at the lobby, the pool and a room.",
            "tags": "hotel review, travel",
            "hashtags": "#travel #hotel #review",
            "chapters": '0:00 "Arrival"\n0:12 "Lobby and pool"\n0:24 "Rooms"',
        },
        "regenerations": {},
    }

    job = _job(store, job_id)
    assert job.state is JobState.GENERATING  # the worker sets `done` once `run` returns
    assert job.duration_s == pytest.approx(36.0, abs=0.3)
    assert job.language == "en"
    assert job.no_speech is False
    assert job.providers == {"transcription": "fake", "llm": f"fake:{MODEL}"}
    expected_cost = pricing.estimate(Usage(input_tokens=1000, output_tokens=200), MODEL)
    assert job.usage == {
        "input_tokens": 1000,
        "output_tokens": 200,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "audio_minutes": 0.5,
        "video_output_tokens": 0,
        "cost_usd": pytest.approx(expected_cost),
    }

    job_dir = store.job_dir(job_id)
    assert not (job_dir / "source.bin").exists()
    assert len(transcriber.calls) == 1
    assert transcriber.calls[0] == AudioFiles(mp3=job_dir / "audio.mp3")
    media = _media(store, job_id)
    assert media.has_audio is True
    assert media.language == "en"
    assert media.language_probability == pytest.approx(0.99)
    assert media.no_speech is False
    assert [(s.start, s.end, s.text) for s in media.segments] == [
        (0.0, 4.0, "Welcome to the hotel tour."),
        (4.0, 12.0, "This is the lobby and the pool."),
    ]
    assert media.frames


async def test_generation_request_has_a_stable_prefix_and_the_duration_in_the_turn(
    store: JobStore, settings: Settings, make_video: MakeVideo
) -> None:
    job_id = _queue(store, make_video(audio=True, **LONG))
    llm = FakeLLM([VALID], model=MODEL)

    await _run(store, job_id, settings, FakeTranscriber(name="fake"), llm)

    [req] = llm.requests
    media = pipeline.load_media(store.job_dir(job_id))  # what regeneration will read back
    assert req.system == SYSTEM_PROMPT
    assert req.schema == OUTPUT_SCHEMA
    assert req.context_text == "[0:00] Welcome to the hotel tour.\n[0:04] This is the lobby and the pool."
    assert [f.t_seconds for f in req.frames] == [f.t for f in media.frames]
    job_dir = store.job_dir(job_id)
    assert [f.jpeg for f in req.frames] == [(job_dir / f.file).read_bytes() for f in media.frames]
    assert all(f.jpeg.startswith(b"\xff\xd8") for f in req.frames)
    [turn] = req.turns
    assert turn.role == "user"
    assert languages.instruction("en") in turn.text  # the detected language is enforced in the turn
    assert "0:36" in turn.text  # the duration lives in the turn ...
    assert "0:36" not in req.context_text and "36" not in req.system  # ... not in the cached prefix
    assert pipeline.build_prefix(media, job_dir).request(OUTPUT_SCHEMA, req.turns) == req


async def test_transcript_segments_are_sanitized_before_media_json(
    store: JobStore, settings: Settings, make_video: MakeVideo
) -> None:
    job_id = _queue(store, make_video(audio=True, **LONG))
    segments = [
        Segment(20.0, 25.0, "second"),
        Segment(5.0, 5.0, "zero length"),
        Segment(-1.0, 4.0, "first"),
        Segment(30.0, 50.0, "runs past the end"),
        Segment(40.0, 45.0, "after the end"),
        Segment(8.0, 7.0, "backwards"),
    ]
    llm = FakeLLM([VALID], model=MODEL)

    await _run(store, job_id, settings, FakeTranscriber(name="fake", segments=segments), llm)

    media = _media(store, job_id)
    assert [(s.start, s.text) for s in media.segments] == [
        (0.0, "first"),
        (20.0, "second"),
        (30.0, "runs past the end"),
    ]
    assert media.segments[-1].end == pytest.approx(media.duration_s)
    assert all(0 <= s.start < s.end <= media.duration_s for s in media.segments)
    assert llm.requests[0].context_text == "[0:00] first\n[0:20] second\n[0:30] runs past the end"


# --- repair ------------------------------------------------------------------------------------------------------


async def test_invalid_output_is_repaired_once(store: JobStore, settings: Settings, make_video: MakeVideo) -> None:
    job_id = _queue(store, make_video(audio=True, **LONG))
    llm = FakeLLM([_invalid(), VALID], model=MODEL)

    await _run(store, job_id, settings, FakeTranscriber(name="fake"), llm)

    assert len(llm.requests) == 2
    assert _result(store, job_id)["title"] == VALID["title"]
    usage = _job(store, job_id).usage
    assert (usage["input_tokens"], usage["output_tokens"]) == (2000, 400)  # both calls are counted
    calls_cost = pricing.estimate(Usage(input_tokens=2000, output_tokens=400), MODEL)
    assert usage["cost_usd"] == pytest.approx(calls_cost)


async def test_repair_reuses_the_identical_request_prefix(
    store: JobStore, settings: Settings, make_video: MakeVideo
) -> None:
    job_id = _queue(store, make_video(audio=True, **LONG))
    invalid = _invalid()
    llm = FakeLLM([invalid, VALID], model=MODEL)

    await _run(store, job_id, settings, FakeTranscriber(name="fake"), llm)

    first, repair = llm.requests
    # Byte-identical stable prefix (system, transcript, frames) so the repair call reads the cache (SC-004).
    assert repair.system == first.system
    assert repair.context_text == first.context_text
    assert repair.frames == first.frames
    assert repair.schema == first.schema
    assert repair.turns[0] == first.turns[0]
    assert repair.turns[1].role == "assistant"
    assert json.loads(repair.turns[1].text) == invalid
    assert repair.turns[2].role == "user"
    assert "title is 120 characters, at most 100 allowed" in repair.turns[2].text
    assert "0:36" in repair.turns[2].text
    assert len(repair.turns) == 3


async def test_output_still_invalid_after_repair_fails_with_a_readable_reason(
    store: JobStore, settings: Settings, make_video: MakeVideo
) -> None:
    job_id = _queue(store, make_video(audio=True, **LONG))
    llm = FakeLLM([_invalid(), _invalid()], model=MODEL)

    with pytest.raises(AppError) as exc:
        await _run(store, job_id, settings, FakeTranscriber(name="fake"), llm)

    assert len(llm.requests) == 2  # exactly one repair attempt
    assert "title is 120 characters, at most 100 allowed" in exc.value.message
    assert "Traceback" not in exc.value.message
    assert not (store.job_dir(job_id) / "result.json").exists()
    job = _job(store, job_id)
    assert job.usage["input_tokens"] == 2000  # spent tokens are still reported
    assert job.providers["llm"] == f"fake:{MODEL}"


async def test_output_not_matching_the_schema_is_repaired(
    store: JobStore, settings: Settings, make_video: MakeVideo
) -> None:
    job_id = _queue(store, make_video(audio=True, **LONG))
    broken: dict[str, Any] = {k: v for k, v in VALID.items() if k != "hashtags"}
    broken["chapters"] = [{"start_seconds": "zero", "title": "Arrival"}]
    broken["extra"] = True
    llm = FakeLLM([broken, VALID], model=MODEL)

    await _run(store, job_id, settings, FakeTranscriber(name="fake"), llm)

    repair_text = llm.requests[1].turns[2].text
    assert "hashtags" in repair_text
    assert "chapters.0.start_seconds" in repair_text
    assert "extra" in repair_text
    assert "zero" not in repair_text  # locations and messages only, never the submitted values
    assert _result(store, job_id)["hashtags"] == ["#travel", "#hotel", "#review"]


async def test_schema_invalid_output_after_repair_fails(
    store: JobStore, settings: Settings, make_video: MakeVideo
) -> None:
    job_id = _queue(store, make_video(audio=True, **LONG))
    llm = FakeLLM([{"title": 1}, {"title": 2}], model=MODEL)

    with pytest.raises(AppError) as exc:
        await _run(store, job_id, settings, FakeTranscriber(name="fake"), llm)

    assert "title" in exc.value.message
    assert not (store.job_dir(job_id) / "result.json").exists()


# --- no speech / short videos ------------------------------------------------------------------------------------


async def test_video_without_audio_skips_transcription(
    store: JobStore, settings: Settings, make_video: MakeVideo
) -> None:
    job_id = _queue(store, make_video(audio=False, **LONG))
    transcriber = FakeTranscriber(name="fake")
    llm = FakeLLM([VALID], model=MODEL)

    handle = await _run(store, job_id, settings, transcriber, llm)

    assert transcriber.calls == []
    assert handle.states == ["extracting", "transcribing", "generating"]  # the state machine has no shortcut
    job = _job(store, job_id)
    assert job.no_speech is True
    assert job.language == "en"
    assert job.providers == {"llm": f"fake:{MODEL}"}
    assert job.usage["audio_minutes"] == 0
    assert job.usage["cost_usd"] == pytest.approx(pricing.estimate(llm.usage, MODEL))
    assert llm.requests[0].context_text == NO_SPEECH_TEXT
    assert languages.instruction("en") in llm.requests[0].turns[0].text
    result = _result(store, job_id)
    assert result["notices"] == ["no_speech"]
    assert result["language"] == "en"
    media = _media(store, job_id)
    assert (media.no_speech, media.language, media.segments) == (True, "en", [])


async def test_no_speech_gets_a_notice_and_english_as_the_language(
    store: JobStore, settings: Settings, make_video: MakeVideo
) -> None:
    job_id = _queue(store, make_video(audio=True, **LONG))
    transcriber = FakeTranscriber(name="fake", no_speech=True, language="fr", language_probability=0.2)
    german = {**VALID, "language": "de"}  # e.g. inferred from on-screen text
    llm = FakeLLM([{**_invalid(), "language": "de"}, german], model=MODEL)

    await _run(store, job_id, settings, transcriber, llm)

    assert len(transcriber.calls) == 1
    first, repair = llm.requests
    for turn in (first.turns[0], repair.turns[2]):  # the instruction rides in the generate and repair turns ...
        assert languages.instruction("en") in turn.text
    en = languages.instruction("en")
    assert en not in first.system and en not in first.context_text  # ... not the prefix
    job = _job(store, job_id)
    assert (job.no_speech, job.language) == (True, "en")
    assert job.providers == {"transcription": "fake", "llm": f"fake:{MODEL}"}
    result = _result(store, job_id)
    assert result["notices"] == ["no_speech"]
    assert result["language"] == "en"
    assert llm.requests[0].context_text == NO_SPEECH_TEXT
    assert (_media(store, job_id).no_speech, _media(store, job_id).language) == (True, "en")


async def test_output_language_is_the_language_detected_by_transcription(
    store: JobStore, settings: Settings, make_video: MakeVideo
) -> None:
    # Mixed-language speech: whatever the model claims, the detected language is used for all output (FR-020).
    job_id = _queue(store, make_video(audio=True, **LONG))
    french = {**VALID, "language": "fr"}
    llm = FakeLLM([{**_invalid(), "language": "fr"}, french], model=MODEL)

    await _run(store, job_id, settings, FakeTranscriber(name="fake", language="de"), llm)

    first, repair = llm.requests
    for turn in (first.turns[0], repair.turns[2]):  # the instruction rides in the generate and repair turns ...
        assert languages.instruction("de") in turn.text
    for req in (first, repair):  # ... never in the cached prefix
        assert "language code de" not in req.system and "language code de" not in req.context_text
    assert _job(store, job_id).language == "de"
    assert _result(store, job_id)["language"] == "de"


def _media_for(language: str, no_speech: bool) -> MediaArtifacts:
    return MediaArtifacts(
        duration_s=36.0, has_audio=True, language=language, no_speech=no_speech, segments=[], frames=[]
    )


@pytest.mark.parametrize(
    ("choice", "fallback", "detected", "no_speech", "expected"),
    [
        ("ja", "en", "de", False, "ja"),  # a picked code wins with speech ...
        ("ja", "pt", "de", True, "ja"),  # ... and without
        ("auto", "pt", "de", False, "de"),  # auto + speech: the detected language
        ("auto", "pt", "de", True, "pt"),  # auto + no speech: the fallback
        ("auto", "en", "", False, "en"),  # nothing detected: English
    ],
)
def test_output_language_resolution_table(
    choice: str, fallback: str, detected: str, no_speech: bool, expected: str
) -> None:
    media = _media_for(detected, no_speech)
    assert pipeline.output_language(LanguageSetting(choice, fallback), media) == expected


async def test_a_picked_language_is_used_whatever_the_speech(
    store: JobStore, settings: Settings, make_video: MakeVideo
) -> None:
    job_id = _queue(store, make_video(audio=True, **LONG), LanguageSetting("ja", "en"))
    llm = FakeLLM([_invalid(), VALID], model=MODEL)

    await _run(store, job_id, settings, FakeTranscriber(name="fake", language="de"), llm)

    first, repair = llm.requests
    ja, de = languages.instruction("ja"), languages.instruction("de")
    for turn in (first.turns[0], repair.turns[2]):  # the turns carry the picked language ...
        assert ja in turn.text
        assert de not in turn.text
    # ... and the prefix stays byte-identical to the one an unpicked job would send (SC-004)
    assert (repair.system, repair.context_text, repair.frames) == (first.system, first.context_text, first.frames)
    assert first.system == SYSTEM_PROMPT
    assert "language code ja" not in first.system and "language code ja" not in first.context_text
    assert first.schema == repair.schema == OUTPUT_SCHEMA
    assert _result(store, job_id)["language"] == "ja"
    assert _job(store, job_id).language == "de"  # the job row still records what transcription detected
    assert _media(store, job_id).language == "de"


async def test_prefix_does_not_depend_on_the_language(
    store: JobStore, settings: Settings, make_video: MakeVideo
) -> None:
    video = make_video(audio=True, **LONG)
    requests = []
    for setting in (None, LanguageSetting("ja", "en"), LanguageSetting("fr", "en")):
        job_id = _queue(store, video, setting)
        llm = FakeLLM([VALID], model=MODEL)
        await _run(store, job_id, settings, FakeTranscriber(name="fake"), llm)
        requests.append(llm.requests[0])
    first, *others = requests
    for other in others:
        assert (other.system, other.context_text, other.schema) == (first.system, first.context_text, first.schema)
        assert [f.jpeg for f in other.frames] == [f.jpeg for f in first.frames]
        assert other.turns != first.turns


async def test_auto_without_speech_uses_the_fallback_language(
    store: JobStore, settings: Settings, make_video: MakeVideo
) -> None:
    job_id = _queue(store, make_video(audio=False, **LONG), LanguageSetting("auto", "pt"))
    llm = FakeLLM([VALID], model=MODEL)

    await _run(store, job_id, settings, FakeTranscriber(name="fake"), llm)

    assert languages.instruction("pt") in llm.requests[0].turns[0].text
    assert _result(store, job_id)["language"] == "pt"
    assert (_job(store, job_id).language, _job(store, job_id).no_speech) == ("en", True)  # detection unchanged


async def test_missing_language_file_behaves_as_auto_with_english_fallback(
    store: JobStore, settings: Settings, make_video: MakeVideo
) -> None:
    job_id = _queue(store, make_video(audio=False, **LONG))
    assert not (store.job_dir(job_id) / languages.LANGUAGE_JSON).exists()
    llm = FakeLLM([VALID], model=MODEL)

    await _run(store, job_id, settings, FakeTranscriber(name="fake"), llm)

    assert languages.instruction("en") in llm.requests[0].turns[0].text
    assert _result(store, job_id)["language"] == "en"


async def test_result_language_falls_back_to_the_job_language(
    store: JobStore, settings: Settings, make_video: MakeVideo
) -> None:
    job_id = _queue(store, make_video(audio=True, **LONG))
    output = {**VALID, "language": "  "}
    await _run(store, job_id, settings, FakeTranscriber(name="fake", language="de"), FakeLLM([output], model=MODEL))
    assert _result(store, job_id)["language"] == "de"


async def test_too_short_for_chapters(store: JobStore, settings: Settings, make_video: MakeVideo) -> None:
    job_id = _queue(store, make_video(audio=True))  # 6 s
    llm = FakeLLM([VALID], model=MODEL)  # chapters that a short video must not have

    await _run(store, job_id, settings, FakeTranscriber(name="fake"), llm)

    assert len(llm.requests) == 1  # chapters are dropped by normalization, no repair needed
    assert "30 seconds" in llm.requests[0].turns[0].text
    result = _result(store, job_id)
    assert result["chapters"] == []
    assert result["rendered"]["chapters"] == ""
    assert result["notices"] == ["too_short_for_chapters"]


# --- cancellation ------------------------------------------------------------------------------------------------


class CancellingTranscriber(FakeTranscriber):
    """Requests cancellation of the job while transcribing (as `POST .../cancel` would)."""

    def __init__(self, store: JobStore, job_id: str) -> None:
        super().__init__(name="fake")
        self._store = store
        self._job_id = job_id

    def transcribe(self, audio: AudioFiles) -> Transcript:
        self._store.cancel(self._job_id, active=True)
        return super().transcribe(audio)


async def test_cancel_mid_pipeline_stops_at_the_next_stage_boundary(
    store: JobStore, settings: Settings, make_video: MakeVideo
) -> None:
    job_id = _queue(store, make_video(audio=True, **LONG))
    llm = FakeLLM([VALID], model=MODEL)

    with pytest.raises(JobCancelled):
        await _run(store, job_id, settings, CancellingTranscriber(store, job_id), llm)

    assert llm.requests == []
    assert _job(store, job_id).state is JobState.TRANSCRIBING
    assert not (store.job_dir(job_id) / "result.json").exists()


async def test_cancel_before_start_runs_nothing(store: JobStore, settings: Settings, make_video: MakeVideo) -> None:
    job_id = _queue(store, make_video(audio=True, **LONG))
    store.cancel(job_id, active=True)
    transcriber = FakeTranscriber(name="fake")

    with pytest.raises(JobCancelled):
        await _run(store, job_id, settings, transcriber, FakeLLM([], model=MODEL))

    assert _job(store, job_id).state is JobState.QUEUED
    assert (store.job_dir(job_id) / "source.bin").exists()  # extraction never started


# --- cost --------------------------------------------------------------------------------------------------------


async def test_unknown_llm_model_makes_the_cost_unknown(
    store: JobStore, settings: Settings, make_video: MakeVideo
) -> None:
    job_id = _queue(store, make_video(audio=True, **LONG))
    await _run(store, job_id, settings, FakeTranscriber(name="fake"), FakeLLM([VALID], model="unpriced-model"))
    usage = _job(store, job_id).usage
    assert usage["cost_usd"] is None
    assert usage["input_tokens"] == 1000  # tokens are still shown


async def test_paid_transcription_is_priced_per_audio_minute(
    store: JobStore, settings: Settings, make_video: MakeVideo
) -> None:
    job_id = _queue(store, make_video(audio=True, **LONG))
    llm = FakeLLM([VALID], model=MODEL)
    await _run(store, job_id, settings, FakeTranscriber(name="openai", model="whisper-1", audio_minutes=2.0), llm)
    expected = 2.0 * 0.006 + (pricing.estimate(llm.usage, MODEL) or 0.0)
    assert _job(store, job_id).usage["cost_usd"] == pytest.approx(expected)


async def test_pricing_overrides_apply(store: JobStore, settings: Settings, make_video: MakeVideo) -> None:
    settings = settings.model_copy(update={"pricing_overrides": {"custom-model": {"input": 1.0, "output": 1.0}}})
    job_id = _queue(store, make_video(audio=True, **LONG))
    await _run(store, job_id, settings, FakeTranscriber(name="fake"), FakeLLM([VALID], model="custom-model"))
    assert _job(store, job_id).usage["cost_usd"] == pytest.approx(1200 / 1_000_000)


async def test_unpriced_paid_transcription_makes_the_cost_unknown(
    store: JobStore, settings: Settings, make_video: MakeVideo
) -> None:
    job_id = _queue(store, make_video(audio=True, **LONG))
    transcriber = FakeTranscriber(name="openai", model="unpriced-stt")
    await _run(store, job_id, settings, transcriber, FakeLLM([VALID], model=MODEL))
    assert _job(store, job_id).usage["cost_usd"] is None


# --- end to end through the app and the worker -------------------------------------------------------------------

TUS_PATCH = {"Tus-Resumable": "1.0.0", "Content-Type": "application/offset+octet-stream", "Upload-Offset": "0"}


@contextlib.contextmanager
def _app_client(tmp_path: Path, settings: Settings, llm: FakeLLM) -> Iterator[TestClient]:
    providers = Providers(transcriber=TranscriberChain([FakeTranscriber(name="fake")]), llm=LLMChain([llm]))
    app = create_app(settings, providers, static_dir=tmp_path / "static")
    with TestClient(app) as client:
        yield client


def _upload_and_wait(client: TestClient, video: Path) -> dict[str, Any]:
    data = video.read_bytes()
    created = client.post("/api/tools/youtube_prep/jobs", json={"size": len(data)})
    assert created.status_code == 201
    job_id = created.json()["job_id"]
    assert client.patch(f"/api/uploads/{job_id}", content=data, headers=TUS_PATCH).status_code == 204
    deadline = time.monotonic() + 20
    while True:
        body: dict[str, Any] = client.get(f"/api/jobs/{job_id}").json()
        if body["state"] in ("done", "failed", "cancelled"):
            return body
        assert time.monotonic() < deadline, "job did not finish in time"
        time.sleep(0.05)


def test_upload_to_done_through_the_app(tmp_path: Path, settings: Settings, make_video: MakeVideo) -> None:
    with _app_client(tmp_path, settings, FakeLLM([VALID], model=MODEL)) as client:
        tools = {t["id"]: t for t in client.get("/api/tools").json()}
        assert tools["youtube_prep"]["name"] == "YouTube Prep"

        body = _upload_and_wait(client, make_video(audio=True, **LONG))

        assert body["state"] == "done"
        assert body["language"] == "en"
        assert body["providers"] == {"transcription": "fake", "llm": f"fake:{MODEL}"}
        assert body["usage"]["input_tokens"] == 1000
        assert body["usage"]["cost_usd"] is not None
        result = YouTubePrepResult.model_validate(body["result"])
        assert result.rendered.chapters == '0:00 "Arrival"\n0:12 "Lobby and pool"\n0:24 "Rooms"'


def test_invalid_output_fails_the_job_with_a_readable_reason(
    tmp_path: Path, settings: Settings, make_video: MakeVideo
) -> None:
    with _app_client(tmp_path, settings, FakeLLM([_invalid(), _invalid()], model=MODEL)) as client:
        body = _upload_and_wait(client, make_video(audio=True, **LONG))

        assert body["state"] == "failed"
        assert body["failure_code"] == "generation_invalid"
        reason = body["failure_reason"]
        assert reason.startswith("Generation failed: ")
        assert "title is 120 characters, at most 100 allowed" in reason
        assert body["result"] is None
        assert body["usage"]["input_tokens"] == 2000
