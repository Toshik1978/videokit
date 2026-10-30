"""YouTube Prep job creation accepts an output language; GET /options lists the supported codes."""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

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
