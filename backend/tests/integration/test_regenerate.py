"""Per-section regeneration: `POST /api/tools/youtube_prep/jobs/{job_id}/regenerate` (T028, US2, SC-004).

Jobs are seeded by running the real pipeline with a fake media service, fake transcription and a scripted
`FakeLLM`; the same `FakeLLM` then serves the app, so the original generation request and the regeneration
requests can be compared byte for byte.
"""

import asyncio
import contextlib
import copy
import dataclasses
import json
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from fakes_llm import FakeLLM
from fakes_transcribe import FakeTranscriber
from videokit.core import pricing
from videokit.core.config import Settings
from videokit.core.jobs import JobState, JobStore
from videokit.core.llm import GenerationRequest, GenerationResponse, LLMChain, ProviderError, Usage
from videokit.core.media import Frame, MediaArtifacts, MediaService
from videokit.core.purge import purge_expired
from videokit.core.transcribe import TranscriberChain
from videokit.core.worker import Context, Providers, WorkerJobHandle
from videokit.main import create_app
from videokit.tools.youtube_prep import pipeline, prompt
from videokit.tools.youtube_prep.schema import OUTPUT_SCHEMA, SECTIONS, YouTubePrepResult

MODEL = "claude-opus-5-5"
DURATION_S = 36.0
VALID: dict[str, Any] = {
    "language": "en",
    "cover_titles": ["Hidden Gem Resort", "Worth The Price?", "Best Pool Ever"],
    "title": "Hotel tour: lobby, pool and rooms",
    "description": "A tour of a small resort.\n\nWe look at the lobby, the pool and a room.",
    "tags": ["hotel review", "travel"],
    "hashtags": ["#travel", "#hotel", "#review"],
    "chapters": [
        {"start_seconds": 0, "title": "Arrival"},
        {"start_seconds": 12, "title": "Lobby and pool"},
        {"start_seconds": 24.5, "title": "Rooms"},
    ],
}
ENGLISH_INSTRUCTION = "No speech was detected; write all output in English (language code en)."
GEN_USAGE = Usage(input_tokens=1000, output_tokens=200)


def _only(section: str, value: Any, language: str = "en") -> dict[str, Any]:
    """A full model output (the generation schema) carrying only `section`; every other section is empty."""
    empty: dict[str, Any] = {s: "" if s in ("title", "description") else [] for s in SECTIONS}
    return {"language": language, **empty, section: value}


class FakeMedia:
    """Stands in for `MediaService`: writes a few frame files and reports a 36 s video."""

    def __init__(self, *, has_audio: bool = True) -> None:
        self.has_audio = has_audio

    def extract(self, job_dir: Path) -> MediaArtifacts:
        frames = []
        for t in (0.0, 12.0, 24.0):
            name = f"frames/{int(t * 1000)}.jpg"
            (job_dir / "frames").mkdir(exist_ok=True)
            (job_dir / name).write_bytes(b"\xff\xd8fake-jpeg-" + name.encode())
            frames.append(Frame(t=t, file=name))
        (job_dir / "source.bin").unlink(missing_ok=True)
        return MediaArtifacts(duration_s=DURATION_S, has_audio=self.has_audio, frames=frames)


class GatedLLM(FakeLLM):
    """A `FakeLLM` that, once `gated`, blocks inside `generate` until released."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.gated = False
        self.entered = threading.Event()
        self.release = threading.Event()

    def generate(self, req: GenerationRequest) -> GenerationResponse:
        if self.gated:
            self.entered.set()
            assert self.release.wait(10), "the test never released the LLM"
        return super().generate(req)


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


@pytest.fixture
def llm() -> FakeLLM:
    return FakeLLM([VALID], model=MODEL, usage=GEN_USAGE)


def _to_done(store: JobStore, tool_id: str = "youtube_prep") -> str:
    """A job that went through every state up to `generating` (directory created)."""
    job = store.create(tool_id, 100)
    store.job_dir(job.id).mkdir(parents=True)
    store.set_upload_offset(job.id, 100)
    for state in (JobState.QUEUED, JobState.EXTRACTING, JobState.TRANSCRIBING, JobState.GENERATING):
        store.set_state(job.id, state)
    return job.id


def _seed(
    store: JobStore,
    settings: Settings,
    llm: FakeLLM,
    *,
    transcriber: FakeTranscriber | None = None,
    has_audio: bool = True,
) -> str:
    """Run the real pipeline (fake media/providers) on a new job and mark it done, as the worker would."""
    job = store.create("youtube_prep", 100)
    store.job_dir(job.id).mkdir(parents=True)
    store.set_upload_offset(job.id, 100)
    store.set_state(job.id, JobState.QUEUED)
    ctx = Context(
        media=cast(MediaService, FakeMedia(has_audio=has_audio)),
        transcriber=TranscriberChain([transcriber or FakeTranscriber(name="fake")]),
        llm=LLMChain([llm]),
        settings=settings,
    )
    pipeline.run(WorkerJobHandle(store, job.id), ctx)
    store.set_state(job.id, JobState.DONE)
    return job.id


@contextlib.contextmanager
def _client(tmp_path: Path, settings: Settings, llm: FakeLLM) -> Iterator[TestClient]:
    providers = Providers(transcriber=TranscriberChain([FakeTranscriber(name="fake")]), llm=LLMChain([llm]))
    app = create_app(settings, providers, static_dir=tmp_path / "static")
    with TestClient(app) as client:
        yield client


def _url(job_id: str) -> str:
    return f"/api/tools/youtube_prep/jobs/{job_id}/regenerate"


def _result_file(store: JobStore, job_id: str) -> Path:
    return store.job_dir(job_id) / "result.json"


def _read_result(store: JobStore, job_id: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(_result_file(store, job_id).read_text(encoding="utf-8"))
    return data


def _error(response: Any) -> str:
    code: str = response.json()["error"]["code"]
    return code


# --- success -----------------------------------------------------------------------------------------------------


def test_regenerate_replaces_only_that_section(
    tmp_path: Path, settings: Settings, store: JobStore, llm: FakeLLM
) -> None:
    job_id = _seed(store, settings, llm)
    before = _read_result(store, job_id)
    llm.script.append(_only("title", "  A shorter hotel tour  "))

    with _client(tmp_path, settings, llm) as client:
        response = client.post(_url(job_id), json={"section": "title", "hint": "shorter"})
        job = client.get(f"/api/jobs/{job_id}").json()

    assert response.status_code == 200
    body = response.json()
    expected = copy.deepcopy(before)
    expected["title"] = "A shorter hotel tour"  # normalized like generated output
    expected["rendered"]["title"] = "A shorter hotel tour"
    expected["regenerations"] = {"title": 1}
    assert body["result"] == expected
    YouTubePrepResult.model_validate(body["result"])
    assert _read_result(store, job_id) == expected  # persisted ...
    assert job["result"] == expected  # ... and served by the job API
    assert body["usage"] == job["usage"]  # accumulated usage, exactly as in GET /api/jobs/{id}
    assert not list(store.job_dir(job_id).glob("*.tmp"))  # atomic write left no temp file behind


def test_regenerate_reuses_the_byte_identical_request_prefix(
    tmp_path: Path, settings: Settings, store: JobStore, llm: FakeLLM
) -> None:
    job_id = _seed(store, settings, llm)
    before = _read_result(store, job_id)
    llm.script.append(_only("title", "Shorter"))

    with _client(tmp_path, settings, llm) as client:
        assert client.post(_url(job_id), json={"section": "title", "hint": "shorter"}).status_code == 200

    original, regen = llm.requests
    # The stable prefix (system, transcript, frames) is byte-identical, so the provider serves it from cache.
    assert regen.system == original.system
    assert regen.context_text == original.context_text
    assert regen.frames == original.frames
    assert regen.frames  # frames were actually sent
    # The schema (`output_config.format` / `text.format`) is part of the cached prefix too: it must not change.
    assert regen.schema == original.schema == OUTPUT_SCHEMA
    assert dataclasses.replace(original, turns=regen.turns) == regen  # only the turns differ
    media = pipeline.load_media(store.job_dir(job_id))
    [turn] = regen.turns
    assert turn.role == "user"
    assert turn.text == f"{prompt.regenerate_turn('title', 'shorter', before)}\n{pipeline.turn_notes(media)}"
    assert "User hint: shorter" in turn.text
    assert "0:36" in turn.text  # duration facts ride in the turn, as for generation


def test_regenerate_enforces_the_detected_language(
    tmp_path: Path, settings: Settings, store: JobStore, llm: FakeLLM
) -> None:
    job_id = _seed(store, settings, llm, transcriber=FakeTranscriber(name="fake", language="de"))
    llm.script.append(_only("title", "Hotel tour", language="fr"))

    with _client(tmp_path, settings, llm) as client:
        result = client.post(_url(job_id), json={"section": "title"}).json()["result"]

    instruction = "Write all output in the language detected in the speech (language code de)."
    original, regen = llm.requests
    assert instruction in original.turns[0].text and instruction in regen.turns[0].text
    assert instruction not in regen.system and instruction not in regen.context_text
    assert result["language"] == "de"


def test_usage_accumulates_including_cache_tokens(
    tmp_path: Path, settings: Settings, store: JobStore, llm: FakeLLM
) -> None:
    job_id = _seed(store, settings, llm)
    regen_usage = Usage(input_tokens=50, output_tokens=30, cache_read_tokens=900)
    llm.script.append(GenerationResponse(data=_only("title", "Shorter"), usage=regen_usage, provider=f"fake:{MODEL}"))

    with _client(tmp_path, settings, llm) as client:
        usage = client.post(_url(job_id), json={"section": "title"}).json()["usage"]

    assert usage == {
        "input_tokens": 1050,
        "output_tokens": 230,
        "cache_read_tokens": 900,
        "cache_write_tokens": 0,
        "audio_minutes": 0.5,
        "video_output_tokens": 0,
        "cost_usd": pytest.approx(
            (pricing.estimate(GEN_USAGE, MODEL) or 0) + (pricing.estimate(regen_usage, MODEL) or 0)
        ),
    }


def test_regenerate_without_hint_and_counts_regenerations(
    tmp_path: Path, settings: Settings, store: JobStore, llm: FakeLLM
) -> None:
    job_id = _seed(store, settings, llm)
    llm.script.extend(
        [
            _only("title", "Second title"),
            _only("title", "Third title"),
            _only("tags", ["pool", "pool", " resort "]),
        ]
    )

    with _client(tmp_path, settings, llm) as client:
        assert client.post(_url(job_id), json={"section": "title"}).status_code == 200
        assert client.post(_url(job_id), json={"section": "title", "hint": None}).status_code == 200
        response = client.post(_url(job_id), json={"section": "tags", "hint": "   "})

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["title"] == "Third title"
    assert result["tags"] == ["pool", "resort"]
    assert result["rendered"]["tags"] == "pool, resort"
    assert result["regenerations"] == {"title": 2, "tags": 1}
    for req in llm.requests[1:]:
        assert "User hint" not in req.turns[0].text
    assert all(req.schema == OUTPUT_SCHEMA for req in llm.requests)


def test_regenerated_chapters_follow_the_chapter_rules(
    tmp_path: Path, settings: Settings, store: JobStore, llm: FakeLLM
) -> None:
    job_id = _seed(store, settings, llm)
    chapters = [
        {"start_seconds": 0, "title": "Welcome - arrival"},
        {"start_seconds": 15, "title": "“The pool”"},
        {"start_seconds": 26, "title": "Goodbye"},
    ]
    llm.script.append(_only("chapters", chapters))

    with _client(tmp_path, settings, llm) as client:
        result = client.post(_url(job_id), json={"section": "chapters"}).json()["result"]

    assert result["chapters"] == [
        {"start_seconds": 0.0, "title": "Welcome arrival"},
        {"start_seconds": 15.0, "title": "The pool"},
        {"start_seconds": 26.0, "title": "Goodbye"},
    ]
    assert result["rendered"]["chapters"] == '0:00 "Welcome arrival"\n0:15 "The pool"\n0:26 "Goodbye"'
    assert result["title"] == VALID["title"]


def test_no_speech_jobs_regenerate_in_english(
    tmp_path: Path, settings: Settings, store: JobStore, llm: FakeLLM
) -> None:
    job_id = _seed(store, settings, llm, has_audio=False)
    llm.script.append(_only("title", "Hoteltour", language="de"))

    with _client(tmp_path, settings, llm) as client:
        result = client.post(_url(job_id), json={"section": "title"}).json()["result"]

    regen = llm.requests[1]
    assert ENGLISH_INSTRUCTION in regen.turns[0].text
    assert ENGLISH_INSTRUCTION not in regen.system and ENGLISH_INSTRUCTION not in regen.context_text
    assert result["language"] == "en"  # the result language is kept
    assert result["notices"] == ["no_speech"]


# --- repair ------------------------------------------------------------------------------------------------------


def test_invalid_section_is_repaired_once_with_the_same_prefix(
    tmp_path: Path, settings: Settings, store: JobStore, llm: FakeLLM
) -> None:
    job_id = _seed(store, settings, llm)
    invalid = _only("title", "x" * 120)
    llm.script.extend([invalid, _only("title", "Fixed title")])

    with _client(tmp_path, settings, llm) as client:
        response = client.post(_url(job_id), json={"section": "title", "hint": "shorter"})

    assert response.status_code == 200
    assert response.json()["result"]["title"] == "Fixed title"
    assert response.json()["result"]["regenerations"] == {"title": 1}
    assert response.json()["usage"]["input_tokens"] == 3000  # generation + regeneration + repair
    original, regen, repair = llm.requests
    assert (repair.system, repair.context_text, repair.frames) == (
        original.system,
        original.context_text,
        original.frames,
    )
    assert repair.schema == original.schema == OUTPUT_SCHEMA
    assert repair.turns[0] == regen.turns[0]
    assert repair.turns[1].role == "assistant"
    assert json.loads(repair.turns[1].text) == invalid
    assert repair.turns[2].role == "user"
    assert "title is 120 characters, at most 100 allowed" in repair.turns[2].text
    assert "0:36" in repair.turns[2].text
    assert len(repair.turns) == 3


def test_output_missing_the_other_sections_is_repaired(
    tmp_path: Path, settings: Settings, store: JobStore, llm: FakeLLM
) -> None:
    job_id = _seed(store, settings, llm)
    llm.script.extend([{"language": "en", "title": "New", "extra": "sneaky"}, _only("title", "New")])

    with _client(tmp_path, settings, llm) as client:
        result = client.post(_url(job_id), json={"section": "title"}).json()["result"]

    repair_text = llm.requests[2].turns[2].text
    assert "tags" in repair_text and "extra" in repair_text  # the full schema is checked structurally
    assert "sneaky" not in repair_text  # locations and messages only, never values
    assert result["title"] == "New"
    assert result["tags"] == VALID["tags"]


def test_content_in_other_sections_is_ignored(
    tmp_path: Path, settings: Settings, store: JobStore, llm: FakeLLM
) -> None:
    job_id = _seed(store, settings, llm)
    before = _read_result(store, job_id)
    filled = {**VALID, "title": "Only this changes", "tags": [], "hashtags": ["#x"], "description": "x" * 6000}
    llm.script.append(filled)  # other sections (even invalid ones) are neither validated nor used

    with _client(tmp_path, settings, llm) as client:
        result = client.post(_url(job_id), json={"section": "title"}).json()["result"]

    assert len(llm.requests) == 2  # no repair
    assert result == {
        **before,
        "title": "Only this changes",
        "rendered": {**before["rendered"], "title": "Only this changes"},
        "regenerations": {"title": 1},
    }


# --- failures ----------------------------------------------------------------------------------------------------


def test_still_invalid_after_repair_keeps_the_previous_section(
    tmp_path: Path, settings: Settings, store: JobStore, llm: FakeLLM
) -> None:
    job_id = _seed(store, settings, llm)
    before = _result_file(store, job_id).read_bytes()
    invalid = _only("title", "x" * 120)
    llm.script.extend([invalid, invalid])

    with _client(tmp_path, settings, llm) as client:
        response = client.post(_url(job_id), json={"section": "title"})
        job = client.get(f"/api/jobs/{job_id}").json()

        assert response.status_code == 502
        assert _error(response) == "generation_failed"
        assert "Traceback" not in response.json()["error"]["message"]
        assert _result_file(store, job_id).read_bytes() == before  # previous section (and counter) kept
        assert job["state"] == "done"
        assert job["usage"]["input_tokens"] == 3000  # spent tokens are still reported

        llm.script.append(_only("title", "Next try"))  # the per-job lock was released
        retry = client.post(_url(job_id), json={"section": "title"})

    assert retry.status_code == 200
    assert retry.json()["result"]["regenerations"] == {"title": 1}


def test_all_providers_failing_keeps_the_previous_section(
    tmp_path: Path, settings: Settings, store: JobStore, llm: FakeLLM
) -> None:
    job_id = _seed(store, settings, llm)
    before = _result_file(store, job_id).read_bytes()
    llm.script.append(ProviderError("overloaded"))

    with _client(tmp_path, settings, llm) as client:
        response = client.post(_url(job_id), json={"section": "description"})

    assert response.status_code == 502
    assert _error(response) == "generation_failed"
    assert _result_file(store, job_id).read_bytes() == before


@pytest.mark.parametrize("section", ["language", "rendered", "bogus", ""])
def test_unknown_section_is_rejected(
    tmp_path: Path, settings: Settings, store: JobStore, llm: FakeLLM, section: str
) -> None:
    job_id = _seed(store, settings, llm)
    with _client(tmp_path, settings, llm) as client:
        response = client.post(_url(job_id), json={"section": section})
    assert response.status_code == 422
    assert _error(response) == "invalid_section"
    assert len(llm.requests) == 1  # no regeneration call


def test_hint_longer_than_500_characters_is_rejected(
    tmp_path: Path, settings: Settings, store: JobStore, llm: FakeLLM
) -> None:
    job_id = _seed(store, settings, llm)
    llm.script.append(_only("title", "Fine"))
    with _client(tmp_path, settings, llm) as client:
        too_long = client.post(_url(job_id), json={"section": "title", "hint": "h" * 501})
        at_limit = client.post(_url(job_id), json={"section": "title", "hint": "h" * 500})
    assert too_long.status_code == 422
    assert _error(too_long) == "invalid_request"
    assert at_limit.status_code == 200


def test_job_that_is_not_done_is_rejected(tmp_path: Path, settings: Settings, store: JobStore, llm: FakeLLM) -> None:
    uploading = store.create("youtube_prep", 100).id
    failed = store.create("youtube_prep", 100).id
    store.set_state(failed, JobState.FAILED, "Generation failed: something")
    running = _to_done(store)  # still `generating`

    with _client(tmp_path, settings, llm) as client:
        responses = [client.post(_url(job_id), json={"section": "title"}) for job_id in (uploading, failed, running)]

    for response in responses:
        assert response.status_code == 409
        assert _error(response) == "job_not_done"
    assert llm.requests == []


def test_unknown_job_is_not_found(tmp_path: Path, settings: Settings, llm: FakeLLM) -> None:
    with _client(tmp_path, settings, llm) as client:
        response = client.post(_url("no-such-job"), json={"section": "title"})
    assert response.status_code == 404
    assert _error(response) == "job_not_found"


def test_expired_and_purged_job_is_not_found(tmp_path: Path, settings: Settings, store: JobStore, llm: FakeLLM) -> None:
    job_id = _seed(store, settings, llm)
    assert purge_expired(store, time.time() + 10 * 365 * 24 * 3600) == 1

    with _client(tmp_path, settings, llm) as client:
        response = client.post(_url(job_id), json={"section": "title"})

    assert response.status_code == 404
    assert _error(response) == "job_not_found"
    assert len(llm.requests) == 1


def test_expired_job_is_not_found_before_the_purge_runs(
    tmp_path: Path, settings: Settings, store: JobStore, llm: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    """US2-AS4: past `expires_at` the job counts as expired even though its row and files still exist."""

    async def no_purge(store: JobStore) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr("videokit.main.run_purge_loop", no_purge)
    past = time.time() - (settings.job_ttl_hours + 1) * 3600
    old = JobStore(settings.data_dir, settings.job_ttl_hours, settings.max_upload_bytes, clock=lambda: past)
    job_id = _seed(old, settings, llm)
    before = store.get(job_id)
    assert before is not None
    result = _read_result(store, job_id)

    with _client(tmp_path, settings, llm) as client:
        response = client.post(_url(job_id), json={"section": "title", "hint": "shorter"})

    assert response.status_code == 404
    assert _error(response) == "job_not_found"
    assert len(llm.requests) == 1  # only the original generation: no provider call
    after = store.get(job_id)  # the row is still there (not purged) with its usage unchanged
    assert after is not None
    assert after.usage == before.usage
    assert _read_result(store, job_id) == result


def test_job_of_another_tool_is_not_found(tmp_path: Path, settings: Settings, store: JobStore, llm: FakeLLM) -> None:
    job_id = _to_done(store, tool_id="other_tool")
    store.set_state(job_id, JobState.DONE)
    with _client(tmp_path, settings, llm) as client:
        response = client.post(_url(job_id), json={"section": "title"})
    assert response.status_code == 404
    assert _error(response) == "job_not_found"


def test_done_job_with_missing_files_is_not_found(
    tmp_path: Path, settings: Settings, store: JobStore, llm: FakeLLM
) -> None:
    job_id = _seed(store, settings, llm)
    (store.job_dir(job_id) / "media.json").unlink()  # e.g. the directory is being purged
    with _client(tmp_path, settings, llm) as client:
        response = client.post(_url(job_id), json={"section": "title"})
    assert response.status_code == 404
    assert _error(response) == "job_not_found"


class PurgingLLM(FakeLLM):
    """Answers, but the TTL purge deletes the job (row and directory) while the call is in flight."""

    def __init__(self, store: JobStore, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._store = store

    def generate(self, req: GenerationRequest) -> GenerationResponse:
        assert purge_expired(self._store, time.time() + 10 * 365 * 24 * 3600) == 1
        return super().generate(req)


def test_job_purged_during_regeneration_is_not_found(
    tmp_path: Path, settings: Settings, store: JobStore, llm: FakeLLM
) -> None:
    job_id = _seed(store, settings, llm)
    purging = PurgingLLM(store, [_only("title", "Too late")], model=MODEL)

    with _client(tmp_path, settings, purging) as client:
        response = client.post(_url(job_id), json={"section": "title"})

    assert response.status_code == 404
    assert _error(response) == "job_not_found"
    assert not store.job_dir(job_id).exists()


# --- concurrency -------------------------------------------------------------------------------------------------


def test_concurrent_regeneration_of_the_same_job_is_rejected(
    tmp_path: Path, settings: Settings, store: JobStore
) -> None:
    llm = GatedLLM([VALID], model=MODEL, usage=GEN_USAGE)
    job_id = _seed(store, settings, llm)
    llm.script.extend([_only("title", "First"), _only("title", "Afterwards")])
    llm.gated = True

    with _client(tmp_path, settings, llm) as client, ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(client.post, _url(job_id), json={"section": "title"})
        assert llm.entered.wait(10)
        second = client.post(_url(job_id), json={"section": "description"})
        llm.release.set()
        assert first.result(timeout=10).status_code == 200
        again = client.post(_url(job_id), json={"section": "title"})  # the lock is free again afterwards

    assert second.status_code == 409
    assert _error(second) == "regeneration_in_progress"
    assert len(llm.requests) == 3  # the rejected request never reached the provider
    assert again.status_code == 200
    assert again.json()["result"]["title"] == "Afterwards"
    assert again.json()["result"]["regenerations"] == {"title": 2}
