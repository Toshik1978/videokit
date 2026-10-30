import asyncio
import textwrap
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from fakes_videogen import FakeVideoGenerator
from videokit.core import providers as providers_module
from videokit.core.config import VIDEO_PROVIDER_NAMES, Settings
from videokit.core.jobs import JobState, JobStore
from videokit.core.videogen import ProviderStatus
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
            from fastapi import APIRouter, Request

            from videokit.core import uploads

            router = APIRouter()

            @router.post("/jobs", status_code=201)
            async def create(body: uploads.CreateUploadRequest, request: Request) -> dict[str, str]:
                return await uploads.create_upload_job(request, "demo_tool", body)

            @router.get("/ping")
            def ping() -> dict[str, bool]:
                return {"pong": True}

            class T:
                id = "demo_tool"
                name = "Demo Tool"
                description = "Does demos."
                concurrency = 1
                resume_after_restart = False

                def enabled(self, settings):
                    return True

                def accept(self, job, ctx):
                    return None

                async def run(self, job, ctx):
                    return None

            T.router = router
            TOOL = T()
            """
        )
    )
    (pkg / "off").mkdir()
    (pkg / "off" / "__init__.py").write_text(
        textwrap.dedent(
            """
            from fastapi import APIRouter

            router = APIRouter()

            @router.get("/ping")
            def ping() -> dict[str, bool]:
                return {"pong": True}

            class T:
                id = "off_tool"
                name = "Off Tool"
                description = "Disabled by configuration."
                concurrency = 1
                resume_after_restart = True

                def enabled(self, settings):
                    return False

                def accept(self, job, ctx):
                    raise AssertionError("a disabled tool must never accept")

                async def run(self, job, ctx):
                    raise AssertionError("a disabled tool must never run")

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
        transcribe_providers=["openai"],
        openai_transcribe_api_key=SecretStr("stt-test-secret"),
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


def test_health(client: TestClient) -> None:
    response = client.get("/api/health")
    body = response.json()
    assert body["status"] == "ok"
    assert body["queue_length"] == 0
    openai = {"name": "openai", "ready": True, "detail": "model=whisper-1"}
    assert body["providers"]["transcription"] == [openai]
    assert body["providers"]["llm"] == [{"name": "anthropic", "ready": True, "detail": "model=claude-opus-5-5"}]
    assert body["providers"]["video"] == []
    assert "sk-test-secret" not in response.text


def test_health_shows_a_self_hosted_transcriber_without_its_address_or_key(
    settings: Settings, tools_pkg: str, static_dir: Path
) -> None:
    hosted = settings.model_copy(
        update={
            "transcribe_providers": ["openai"],
            "openai_api_key": None,
            "openai_transcribe_url": "http://stt.example.com/v1",
            "openai_transcribe_api_key": SecretStr("stt-test-secret"),
        }
    )
    app = create_app(hosted, tools_package=tools_pkg, static_dir=static_dir)
    with TestClient(app) as client:
        response = client.get("/api/health")
    expected = {"name": "openai", "ready": True, "detail": "model=whisper-1, self-hosted"}
    assert response.json()["providers"]["transcription"] == [expected]
    assert "example.com" not in response.text
    assert "stt-test-secret" not in response.text


def _fake_video_provider(monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> None:
    monkeypatch.setitem(providers_module.VIDEOS, "omni", lambda _settings: FakeVideoGenerator(name="omni", **kwargs))


def _video_settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"video_providers": ["omni"], "gemini_api_key": SecretStr("gm-test-secret")})


def test_every_allowed_video_provider_name_has_an_adapter() -> None:
    assert set(providers_module.VIDEOS) == set(VIDEO_PROVIDER_NAMES)


def test_health_lists_the_configured_video_provider(settings: Settings, tools_pkg: str, static_dir: Path) -> None:
    """The real Omni adapter: its check is local, so health needs no network and no fake."""
    app = create_app(_video_settings(settings), tools_package=tools_pkg, static_dir=static_dir)
    with TestClient(app) as client:
        response = client.get("/api/health")
    expected = {"name": "omni", "ready": True, "detail": "model=gemini-omni-1.1-flash"}
    assert response.json()["providers"]["video"] == [expected]
    assert "gm-test-secret" not in response.text
    assert app.state.providers.video is not None


def test_empty_video_providers_start_without_a_video_chain(
    settings: Settings, tools_pkg: str, static_dir: Path
) -> None:
    app = create_app(settings, tools_package=tools_pkg, static_dir=static_dir)
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        assert app.state.providers.video is None
        assert app.state.job_access.ctx.video is None


def test_video_provider_reaches_the_tool_context(
    settings: Settings, tools_pkg: str, static_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_video_provider(monkeypatch)
    app = create_app(_video_settings(settings), tools_package=tools_pkg, static_dir=static_dir)
    with TestClient(app):
        assert app.state.job_access.ctx.video is app.state.providers.video


class _NotReadyVideo(FakeVideoGenerator):
    def check(self) -> ProviderStatus:
        return ProviderStatus(ready=False, detail="rejected")


def test_unready_video_provider_stops_startup_with_one_line(
    settings: Settings,
    tools_pkg: str,
    static_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setitem(providers_module.VIDEOS, "omni", lambda _settings: _NotReadyVideo(name="omni"))
    app = create_app(_video_settings(settings), tools_package=tools_pkg, static_dir=static_dir)
    with pytest.raises(StartupError) as exc, TestClient(app):
        pass
    line = "video provider 'omni' (VIDEO_PROVIDERS) is not ready: rejected"
    assert exc.value.problems == [line]
    assert capsys.readouterr().err == f"Configuration error: {line}\n"


def test_video_provider_without_an_adapter_stops_startup_with_one_line(
    settings: Settings,
    tools_pkg: str,
    static_dir: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delitem(providers_module.VIDEOS, "omni")  # config accepts the name; the registry lost its adapter
    app = create_app(_video_settings(settings), tools_package=tools_pkg, static_dir=static_dir)
    with pytest.raises(StartupError) as exc, TestClient(app):
        pass
    assert exc.value.problems == ["VIDEO_PROVIDERS lists a provider that is not available"]
    err = capsys.readouterr().err
    assert err == "Configuration error: VIDEO_PROVIDERS lists a provider that is not available\n"
    assert "gm-test-secret" not in err


def test_entry_point_unknown_video_provider_exits_cleanly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TRANSCRIBE_PROVIDERS", "openai")
    monkeypatch.setenv("OPENAI_TRANSCRIBE_URL", "http://stt.example.com/v1")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("VIDEO_PROVIDERS", "bogus")
    with pytest.raises(SystemExit) as exc:
        app_from_env()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert err == "Configuration error: VIDEO_PROVIDERS contains an unknown provider (allowed: omni)\n"


def test_entry_point_unready_video_provider_exits_cleanly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setitem(providers_module.VIDEOS, "omni", lambda _settings: _NotReadyVideo(name="omni"))
    monkeypatch.setenv("TRANSCRIBE_PROVIDERS", "openai")
    monkeypatch.setenv("OPENAI_TRANSCRIBE_URL", "http://stt.example.com/v1")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("VIDEO_PROVIDERS", "omni")
    monkeypatch.setenv("GEMINI_API_KEY", "gm-test-secret")
    with pytest.raises(SystemExit) as exc:
        app_from_env()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert err == "Configuration error: video provider 'omni' (VIDEO_PROVIDERS) is not ready: rejected\n"


def test_config(client: TestClient) -> None:
    assert client.get("/api/config").json() == {
        "max_upload_bytes": 1024**3,
        "upload_chunk_bytes": 50_000_000,
        "job_ttl_hours": 24,
    }


def test_tools_listed_and_router_mounted(client: TestClient) -> None:
    assert client.get("/api/tools").json() == [{"id": "demo_tool", "name": "Demo Tool", "description": "Does demos."}]
    assert client.get("/api/tools/demo_tool/ping").json() == {"pong": True}


def test_disabled_tool_is_not_mounted_listed_or_run(client: TestClient) -> None:
    assert [t["id"] for t in client.get("/api/tools").json()] == ["demo_tool"]
    assert set(client.app.state.tools) == {"demo_tool"}  # type: ignore[attr-defined]
    assert client.get("/api/tools/off_tool/ping").status_code == 404
    lanes = client.app.state.worker._tasks  # type: ignore[attr-defined]
    assert len(lanes) == 1  # demo_tool's single slot only


def test_disabled_tool_job_fails_like_a_removed_tool(client: TestClient) -> None:
    store: JobStore = client.app.state.store  # type: ignore[attr-defined]
    job = store.create("off_tool", 5)
    store.set_upload_offset(job.id, 5)
    asyncio.run(client.app.state.complete_inputs(job.id))  # type: ignore[attr-defined]
    body = client.get(f"/api/jobs/{job.id}").json()
    assert body["state"] == "failed"
    assert body["failure_code"] == "internal_error"


def test_disabled_tool_running_job_is_not_stranded_by_a_restart(settings: Settings, tools_pkg: str) -> None:
    store = JobStore(settings.data_dir, 24, settings.max_upload_bytes)
    job = store.create("off_tool", 5)  # off_tool is resumable, but disabled: it must not count as resumable
    store.set_upload_offset(job.id, 5)
    store.set_state(job.id, JobState.QUEUED)
    store.set_state(job.id, JobState.EXTRACTING)
    with TestClient(create_app(settings, tools_package=tools_pkg)) as c:
        body = c.get(f"/api/jobs/{job.id}").json()
    assert body["state"] == "failed"
    assert body["failure_code"] == "server_restarted"


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
        "failure_code",
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
    assert body["failure_code"] is None
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
    assert body["failure_code"] == "server_restarted"


def test_spa_serving(client: TestClient) -> None:
    assert client.get("/").text == INDEX_HTML
    assert client.get("/tools/demo-tool/jobs/abc").text == INDEX_HTML
    assert client.get("/assets/app.js").text == "console.log(1)"
    r = client.get("/api/unknown")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


@pytest.mark.parametrize(
    ("method", "path"),
    [("POST", "/api/health"), ("DELETE", "/api/jobs/abc"), ("HEAD", "/api/jobs/abc"), ("POST", "/api/tools")],
)
def test_wrong_method_on_a_mounted_api_path_is_405(client: TestClient, method: str, path: str) -> None:
    assert client.request(method, path).status_code == 405


@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
def test_every_method_on_an_unmounted_api_path_is_404(client: TestClient, method: str) -> None:
    r = client.request(method, "/api/tools/not_a_tool/jobs")
    assert r.status_code == 404
    if method != "HEAD":
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


def _expired_store(settings: Settings) -> JobStore:
    """A store on the same data whose clock is one TTL and an hour in the past: its new jobs are already expired."""
    past = time.time() - (settings.job_ttl_hours + 1) * 3600
    return JobStore(settings.data_dir, settings.job_ttl_hours, settings.max_upload_bytes, clock=lambda: past)


@pytest.fixture
def unpurged_client(
    settings: Settings, tools_pkg: str, static_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    """An app whose purge loop never runs, so expired rows stay in the database."""

    async def no_purge(store: JobStore) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr("videokit.main.run_purge_loop", no_purge)
    with TestClient(create_app(settings, tools_package=tools_pkg, static_dir=static_dir)) as c:
        yield c


def test_expired_job_is_not_found_before_the_purge_runs(unpurged_client: TestClient, settings: Settings) -> None:
    """US2-AS4 / FR-015: a job past `expires_at` is gone for the API even while its row still exists."""
    old = _expired_store(settings)
    job = old.create("demo_tool", 5)
    old.set_upload_offset(job.id, 5)
    for state in (JobState.QUEUED, JobState.EXTRACTING, JobState.TRANSCRIBING, JobState.GENERATING, JobState.DONE):
        old.set_state(job.id, state)

    r = unpurged_client.get(f"/api/jobs/{job.id}")

    assert r.status_code == 404
    assert r.json()["error"]["code"] == "job_not_found"
    assert old.get(job.id) is not None  # not purged


def test_expired_running_job_stays_visible_like_the_purge_skips_it(
    unpurged_client: TestClient, settings: Settings
) -> None:
    old = _expired_store(settings)
    job = old.create("demo_tool", 5)
    old.set_upload_offset(job.id, 5)
    old.set_state(job.id, JobState.QUEUED)
    old.set_state(job.id, JobState.EXTRACTING)  # past `queued`: the worker will not pick it up

    r = unpurged_client.get(f"/api/jobs/{job.id}")

    assert r.status_code == 200
    assert r.json()["state"] == "extracting"


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
    monkeypatch.setenv("TRANSCRIBE_PROVIDERS", "openai")
    monkeypatch.setenv("OPENAI_TRANSCRIBE_URL", "http://stt.example.com/v1")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("MAX_FRAMES", "500")
    with pytest.raises(SystemExit) as exc:
        app_from_env()
    assert exc.value.code == 1
    lines = [ln for ln in capsys.readouterr().err.splitlines() if ln]
    assert len(lines) == 2
    assert all(ln.startswith("Configuration error: ") for ln in lines)


def test_entry_point_valid_returns_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRANSCRIBE_PROVIDERS", "openai")
    monkeypatch.setenv("OPENAI_TRANSCRIBE_URL", "http://stt.example.com/v1")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert app_from_env().state.settings.data_dir == tmp_path / "d"


@pytest.mark.parametrize(
    ("sent", "stored"),
    [
        ("trip.mp4", "trip.mp4"),
        ("C:\\Users\\x\\trip.mp4", "trip.mp4"),
        ("/home/x/clips/trip.mp4", "trip.mp4"),
        ("..\\..//etc/trip.mp4", "trip.mp4"),
        ("  trip.mp4 \t", "trip.mp4"),
        ("tr\x00i\x07p\n.mp4", "trip.mp4"),
        ("поездка 旅行.mp4", "поездка 旅行.mp4"),
        ("a" * 300 + ".mp4", ("a" * 300 + ".mp4")[:255]),
        ("", None),
        ("   ", None),
        ("dir/", None),
        ("\x01\x02", None),
        (None, None),
    ],
)
def test_create_job_sanitizes_the_file_name(client: TestClient, sent: str | None, stored: str | None) -> None:
    body: dict[str, object] = {"size": 10}
    if sent is not None:
        body["file_name"] = sent
    created = client.post("/api/tools/demo_tool/jobs", json=body)
    assert created.status_code == 201
    job_id = created.json()["job_id"]
    job = client.app.state.store.get(job_id)  # type: ignore[attr-defined]
    assert job.source_name == stored
    assert "source_name" not in client.get(f"/api/jobs/{job_id}").text
    assert "file_name" not in client.get(f"/api/jobs/{job_id}").text


def test_create_job_file_name_limit(client: TestClient) -> None:
    at_limit = client.post("/api/tools/demo_tool/jobs", json={"size": 10, "file_name": "a" * 1024})
    assert at_limit.status_code == 201
    too_long = client.post("/api/tools/demo_tool/jobs", json={"size": 10, "file_name": "a" * 1025})
    assert too_long.status_code == 422
    assert too_long.json()["error"]["code"] == "invalid_request"


def test_platform_job_creation_route_is_gone(client: TestClient) -> None:
    """Research V1: creation belongs to each tool; the platform route no longer exists."""
    assert client.post("/api/jobs", json={"tool_id": "demo_tool", "size": 10}).status_code in (404, 405)


def test_tool_create_returns_the_upload_url(client: TestClient) -> None:
    r = client.post("/api/tools/demo_tool/jobs", json={"size": 10})
    assert r.status_code == 201
    body = r.json()
    assert body == {"job_id": body["job_id"], "upload_url": f"/api/uploads/{body['job_id']}"}
    job = client.app.state.store.get(body["job_id"])  # type: ignore[attr-defined]
    assert (job.tool_id, job.state, job.upload_length) == ("demo_tool", JobState.UPLOADING, 10)


def test_tool_create_at_the_size_limit_and_beyond(client: TestClient) -> None:
    assert client.post("/api/tools/demo_tool/jobs", json={"size": 1024**3}).status_code == 201
    r = client.post("/api/tools/demo_tool/jobs", json={"size": 1024**3 + 1})
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "file_too_large"


@pytest.mark.parametrize("payload", [{}, {"size": 0}, {"size": -5}, {"size": "big"}, {"size": 1.5}, {"size": True}])
def test_tool_create_rejects_bad_sizes(client: TestClient, payload: dict[str, object]) -> None:
    r = client.post("/api/tools/demo_tool/jobs", json=payload)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_request"
