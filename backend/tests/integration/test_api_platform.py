import textwrap
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from videokit.core.config import Settings
from videokit.core.jobs import JobState, JobStore
from videokit.core.transcribe import local_whisper
from videokit.main import StartupError, app_from_env, create_app

INDEX_HTML = "<!doctype html><title>spa</title>"


@pytest.fixture
def tools_pkg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    pkg = tmp_path / "plat_tools"
    (pkg / "demo").mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "demo" / "__init__.py").write_text(
        textwrap.dedent(
            """
            from fastapi import APIRouter

            router = APIRouter()

            @router.get("/ping")
            def ping() -> dict[str, bool]:
                return {"pong": True}

            class T:
                id = "demo_tool"
                name = "Demo Tool"
                description = "Does demos."

                async def run(self, job, ctx):
                    return None

            T.router = router
            TOOL = T()
            """
        )
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    return "plat_tools"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        anthropic_api_key=SecretStr("sk-test-secret"),
        max_upload_gb=1,
        job_ttl_hours=24,
    )


@pytest.fixture
def static_dir(tmp_path: Path) -> Path:
    d = tmp_path / "static"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text(INDEX_HTML)
    (d / "assets" / "app.js").write_text("console.log(1)")
    return d


@pytest.fixture
def client(settings: Settings, tools_pkg: str, static_dir: Path) -> Iterator[TestClient]:
    app = create_app(settings, tools_package=tools_pkg, static_dir=static_dir)
    with TestClient(app) as c:
        yield c


def test_health(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local_whisper, "cuda_available", lambda: False)
    response = client.get("/api/health")
    body = response.json()
    assert body["status"] == "ok"
    assert body["queue_length"] == 0
    local = {"name": "local", "ready": True, "detail": "device=cpu, model=large-v3-turbo (not loaded yet)"}
    assert body["providers"]["transcription"] == [local]
    assert body["providers"]["llm"] == [{"name": "anthropic", "ready": True, "detail": "model=claude-opus-5-5"}]
    assert "sk-test-secret" not in response.text


def test_config(client: TestClient) -> None:
    assert client.get("/api/config").json() == {
        "max_upload_bytes": 1024**3,
        "upload_chunk_bytes": 50 * 1024 * 1024,
        "job_ttl_hours": 24,
    }


def test_tools_listed_and_router_mounted(client: TestClient) -> None:
    assert client.get("/api/tools").json() == [{"id": "demo_tool", "name": "Demo Tool", "description": "Does demos."}]
    assert client.get("/api/tools/demo_tool/ping").json() == {"pong": True}


def test_job_not_found(client: TestClient) -> None:
    r = client.get("/api/jobs/nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "job_not_found"


def test_job_shape_and_queue_position(client: TestClient) -> None:
    store: JobStore = client.app.state.store  # type: ignore[attr-defined]
    first = store.create("demo_tool", 100)
    second = store.create("demo_tool", 100)
    for j in (first, second):
        store.set_upload_offset(j.id, 100)
        store.set_state(j.id, JobState.QUEUED)

    body = client.get(f"/api/jobs/{second.id}").json()
    assert set(body) == {
        "job_id",
        "tool_id",
        "state",
        "upload",
        "queue_position",
        "failure_reason",
        "expires_at",
        "duration_s",
        "language",
        "providers",
        "usage",
        "result",
    }
    assert body["job_id"] == second.id
    assert body["state"] == "queued"
    assert body["upload"] == {"offset": 100, "length": 100}
    assert body["queue_position"] == 2
    assert body["expires_at"].endswith("Z")
    assert body["result"] is None
    assert body["usage"]["cost_usd"] == 0.0
    assert client.get("/api/health").json()["queue_length"] == 2

    uploading = store.create("demo_tool", 10)
    assert client.get(f"/api/jobs/{uploading.id}").json()["queue_position"] is None


def test_restart_recovery(settings: Settings, tools_pkg: str) -> None:
    store = JobStore(settings.data_dir, 24, settings.max_upload_bytes)
    job = store.create("demo_tool", 5)
    store.set_upload_offset(job.id, 5)
    store.set_state(job.id, JobState.QUEUED)
    with TestClient(create_app(settings, tools_package=tools_pkg)) as c:
        body = c.get(f"/api/jobs/{job.id}").json()
    assert body["state"] == "failed"
    assert body["failure_reason"] == "Server restarted, please upload again"


def test_spa_serving(client: TestClient) -> None:
    assert client.get("/").text == INDEX_HTML
    assert client.get("/tools/demo-tool/jobs/abc").text == INDEX_HTML
    assert client.get("/assets/app.js").text == "console.log(1)"
    r = client.get("/api/unknown")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_missing_static_dir_404(settings: Settings, tools_pkg: str, tmp_path: Path) -> None:
    app = create_app(settings, tools_package=tools_pkg, static_dir=tmp_path / "absent")
    with TestClient(app) as c:
        assert c.get("/").status_code == 404
        assert c.get("/api/health").status_code == 200


def test_invalid_config_exits_with_messages(
    settings: Settings, tools_pkg: str, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = settings.model_copy(update={"max_frames": 500, "anthropic_api_key": None})
    with pytest.raises(StartupError) as exc, TestClient(create_app(bad, tools_package=tools_pkg)):
        pass
    assert len(exc.value.problems) == 2
    err = capsys.readouterr().err
    assert "ANTHROPIC_API_KEY is required" in err
    assert "MAX_FRAMES must be between 1 and 100" in err


def test_spa_rejects_odd_and_traversal_paths(client: TestClient) -> None:
    for path in ("/%00", "/..%2f..%2fetc/passwd", "/../../etc/passwd", "/a%00b/c"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert r.text == INDEX_HTML, path


def test_done_job_returns_result_json(client: TestClient) -> None:
    store: JobStore = client.app.state.store  # type: ignore[attr-defined]
    job = store.create("demo_tool", 5)
    store.set_upload_offset(job.id, 5)
    for state in (JobState.QUEUED, JobState.EXTRACTING, JobState.TRANSCRIBING, JobState.GENERATING):
        store.set_state(job.id, state)
    assert client.get(f"/api/jobs/{job.id}").json()["result"] is None
    store.set_state(job.id, JobState.DONE)
    assert client.get(f"/api/jobs/{job.id}").json()["result"] is None  # no result.json yet
    jdir = store.job_dir(job.id)
    jdir.mkdir(parents=True)
    (jdir / "result.json").write_text('{"title": "Hello"}')
    assert client.get(f"/api/jobs/{job.id}").json()["result"] == {"title": "Hello"}


def test_entry_point_bad_values_exit_cleanly(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("MAX_FRAMES", "lots")
    with pytest.raises(SystemExit) as exc:
        app_from_env()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "MAX_FRAMES has an invalid value" in err
    assert "Traceback" not in err


def test_entry_point_validation_problems_exit_cleanly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("MAX_FRAMES", "500")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(SystemExit) as exc:
        app_from_env()
    assert exc.value.code == 1
    lines = [ln for ln in capsys.readouterr().err.splitlines() if ln]
    assert len(lines) == 2
    assert all(ln.startswith("Configuration error: ") for ln in lines)


def test_entry_point_valid_returns_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert app_from_env().state.settings.data_dir == tmp_path / "d"
