"""YouTube Prep job creation accepts an output language; GET /options lists the supported codes."""

import sqlite3
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from fakes_llm import FakeLLM
from fakes_transcribe import FakeTranscriber
from videokit.core.config import Settings
from videokit.core.llm import LLMChain
from videokit.core.transcribe import TranscriberChain
from videokit.core.worker import Providers
from videokit.main import create_app
from videokit.tools.youtube_prep import languages
from videokit.tools.youtube_prep.languages import LANGUAGES, LanguageSetting, read_setting

CREATE = "/api/tools/youtube_prep/jobs"


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def client(tmp_path: Path, data_dir: Path) -> Iterator[TestClient]:
    settings = Settings(
        transcribe_providers=["openai"],
        openai_transcribe_api_key=SecretStr("stt-test"),
        data_dir=data_dir,
        anthropic_api_key=SecretStr("sk-test"),
    )
    providers = Providers(transcriber=TranscriberChain([FakeTranscriber()]), llm=LLMChain([FakeLLM([])]))
    app = create_app(settings, providers, tools_package="videokit.tools", static_dir=tmp_path / "static")
    with TestClient(app) as c:
        yield c


def _job_count(data_dir: Path) -> int:
    with sqlite3.connect(data_dir / "videokit.db") as conn:
        return int(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])


def _setting(client: TestClient, data_dir: Path, job_id: str) -> LanguageSetting:
    return read_setting(data_dir / "jobs" / job_id)


def test_no_language_defaults_to_auto(client: TestClient, data_dir: Path) -> None:
    res = client.post(CREATE, json={"size": 1000, "file_name": "trip.mp4"})
    assert res.status_code == 201
    assert _setting(client, data_dir, res.json()["job_id"]) == LanguageSetting(choice="auto")
    assert (data_dir / "jobs" / res.json()["job_id"] / languages.LANGUAGE_JSON).is_file()


def test_auto_and_code_are_stored(client: TestClient, data_dir: Path) -> None:
    for choice in ("auto", "de", "zh"):
        res = client.post(CREATE, json={"size": 1000, "language": choice})
        assert res.status_code == 201
        assert _setting(client, data_dir, res.json()["job_id"]).choice == choice


@pytest.mark.parametrize("bad", ["xx", "DE", "De", "", "auto ", "pt-BR"])
def test_invalid_language_is_refused_without_a_job(client: TestClient, data_dir: Path, bad: str) -> None:
    before = _job_count(data_dir)
    res = client.post(CREATE, json={"size": 1000, "language": bad})
    assert res.status_code == 422
    assert res.json()["error"] == {"code": "invalid_language", "message": "This language is not supported."}
    assert _job_count(data_dir) == before


def test_options_lists_sorted_codes(client: TestClient) -> None:
    res = client.get("/api/tools/youtube_prep/options")
    assert res.status_code == 200
    assert res.json() == {"languages": sorted(LANGUAGES)}


def test_write_failure_discards_the_job(client: TestClient, data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(job_dir: Path, setting: LanguageSetting) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("videokit.tools.youtube_prep.routes.write_setting", boom)
    before = _job_count(data_dir)
    res = client.post(CREATE, json={"size": 1000})
    assert res.status_code == 500
    assert _job_count(data_dir) == before


def test_audio_extract_create_is_unaffected(client: TestClient, data_dir: Path) -> None:
    res = client.post("/api/tools/audio_extract/jobs", json={"size": 1000, "language": "xx"})
    assert res.status_code == 201
    assert not (data_dir / "jobs" / res.json()["job_id"] / languages.LANGUAGE_JSON).exists()


@pytest.mark.parametrize(
    ("sent", "stored"),
    [("pt-BR", "pt"), ("PT_br", "pt"), (" de ", "de"), ("xx", "en"), ("", "en"), (None, "en"), ("ja", "ja")],
)
def test_fallback_language_is_normalized_and_stored(
    client: TestClient, data_dir: Path, sent: str | None, stored: str
) -> None:
    res = client.post(CREATE, json={"size": 1000, "language": "auto", "fallback_language": sent})
    assert res.status_code == 201
    assert _setting(client, data_dir, res.json()["job_id"]) == LanguageSetting(choice="auto", fallback=stored)


def test_no_fallback_language_stores_english(client: TestClient, data_dir: Path) -> None:
    res = client.post(CREATE, json={"size": 1000})
    assert _setting(client, data_dir, res.json()["job_id"]).fallback == "en"


def test_overlong_fallback_language_is_refused_without_a_job(client: TestClient, data_dir: Path) -> None:
    before = _job_count(data_dir)
    res = client.post(CREATE, json={"size": 1000, "fallback_language": "a" * 36})
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "invalid_request"
    assert _job_count(data_dir) == before
    assert client.post(CREATE, json={"size": 1000, "fallback_language": "a" * 35}).status_code == 201


TUS_PATCH = {"Tus-Resumable": "1.0.0", "Content-Type": "application/offset+octet-stream", "Upload-Offset": "0"}
OUTPUT: dict[str, Any] = {
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


def _run_job(tmp_path: Path, video: Path, fallback: str | None) -> tuple[dict[str, Any], FakeLLM]:
    llm = FakeLLM([OUTPUT], model="claude-opus-5-5")
    settings = Settings(
        transcribe_providers=["openai"],
        openai_transcribe_api_key=SecretStr("stt-test"),
        data_dir=tmp_path / "run-data",
        anthropic_api_key=SecretStr("sk-test"),
    )
    providers = Providers(transcriber=TranscriberChain([FakeTranscriber(name="fake")]), llm=LLMChain([llm]))
    app = create_app(settings, providers, tools_package="videokit.tools", static_dir=tmp_path / "static")
    with TestClient(app) as c:
        data = video.read_bytes()
        res = c.post(CREATE, json={"size": len(data), "language": "auto", "fallback_language": fallback})
        job_id = res.json()["job_id"]
        assert c.patch(f"/api/uploads/{job_id}", content=data, headers=TUS_PATCH).status_code == 204
        deadline = time.monotonic() + 20
        while True:
            body: dict[str, Any] = c.get(f"/api/jobs/{job_id}").json()
            if body["state"] in ("done", "failed", "cancelled"):
                return body, llm
            assert time.monotonic() < deadline, "job did not finish in time"
            time.sleep(0.05)


def test_silent_video_uses_the_stored_fallback(tmp_path: Path, make_video: Callable[..., Path]) -> None:
    video = make_video(audio=False, colors=("red", "blue", "green"), segment_s=12.0)
    body, llm = _run_job(tmp_path, video, "pt-BR")
    assert body["state"] == "done"
    assert languages.instruction("pt") in llm.requests[0].turns[0].text
    assert body["result"]["language"] == "pt"


def test_video_with_speech_ignores_the_fallback(tmp_path: Path, make_video: Callable[..., Path]) -> None:
    video = make_video(audio=True, colors=("red", "blue", "green"), segment_s=12.0)
    body, llm = _run_job(tmp_path, video, "pt")
    assert body["state"] == "done"
    assert languages.instruction("en") in llm.requests[0].turns[0].text
    assert languages.instruction("pt") not in llm.requests[0].turns[0].text


def _create_and_finish(c: TestClient, video: Path, choice: str) -> str:
    data = video.read_bytes()
    res = c.post(CREATE, json={"size": len(data), "language": choice, "fallback_language": "en"})
    job_id: str = res.json()["job_id"]
    assert c.patch(f"/api/uploads/{job_id}", content=data, headers=TUS_PATCH).status_code == 204
    deadline = time.monotonic() + 20
    while (state := c.get(f"/api/jobs/{job_id}").json()["state"]) not in ("done", "failed", "cancelled"):
        assert time.monotonic() < deadline, "job did not finish in time"
        time.sleep(0.05)
    assert state == "done"
    return job_id


def test_each_job_keeps_its_own_language_through_a_regenerate(tmp_path: Path, make_video: Callable[..., Path]) -> None:
    video = make_video(audio=True, colors=("red", "blue", "green"), segment_s=12.0)
    only_title = {k: ("" if k in ("title", "description") else []) for k in OUTPUT if k != "language"}
    regen = {"language": "en", **only_title, "title": "Shorter title"}
    llm = FakeLLM([OUTPUT, OUTPUT, regen, regen, regen], model="claude-opus-5-5")
    settings = Settings(
        transcribe_providers=["openai"],
        openai_transcribe_api_key=SecretStr("stt-test"),
        data_dir=tmp_path / "run-data",
        anthropic_api_key=SecretStr("sk-test"),
    )
    providers = Providers(transcriber=TranscriberChain([FakeTranscriber(name="fake")]), llm=LLMChain([llm]))
    app = create_app(settings, providers, tools_package="videokit.tools", static_dir=tmp_path / "static")
    with TestClient(app) as c:
        first = _create_and_finish(c, video, "de")
        second = _create_and_finish(c, video, "ja")
        # Interleaved regenerates: each uses its own stored language, never the other job's or a later choice.
        for job_id, code in ((first, "de"), (second, "ja"), (first, "de")):
            res = c.post(f"/api/tools/youtube_prep/jobs/{job_id}/regenerate", json={"section": "title"})
            assert res.status_code == 200
            assert languages.instruction(code) in llm.requests[-1].turns[0].text
            assert res.json()["result"]["language"] == code
        for job_id, code in ((first, "de"), (second, "ja")):
            assert c.get(f"/api/jobs/{job_id}").json()["result"]["language"] == code
