import textwrap
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from videokit.core import uploads
from videokit.core.config import Settings
from videokit.core.jobs import Job, JobState, JobStore
from videokit.main import create_app

TUS = {"Tus-Resumable": "1.0.0"}
PATCH_HEADERS = {**TUS, "Content-Type": "application/offset+octet-stream"}


@pytest.fixture
def tools_pkg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    pkg = tmp_path / "upl_tools"
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

            class T:
                id = "demo_tool"
                name = "Demo"
                description = "Demo."
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
    monkeypatch.syspath_prepend(str(tmp_path))
    return "upl_tools"


@pytest.fixture
def completed() -> list[str]:
    return []


@pytest.fixture
def client(tmp_path: Path, tools_pkg: str, completed: list[str]) -> Iterator[TestClient]:
    settings = Settings(data_dir=tmp_path / "data", anthropic_api_key=SecretStr("sk-test"), max_upload_gb=1)
    app = create_app(settings, tools_package=tools_pkg, static_dir=tmp_path / "static")

    async def on_complete(job_id: str) -> None:
        completed.append(job_id)

    app.state.complete_inputs = on_complete
    with TestClient(app) as c:
        yield c


def _store(client: TestClient) -> JobStore:
    store: JobStore = client.app.state.store  # type: ignore[attr-defined]
    return store


def _create(client: TestClient, size: int = 10) -> str:
    r = client.post("/api/tools/demo_tool/jobs", json={"size": size})
    assert r.status_code == 201
    return str(r.json()["job_id"])


def test_options(client: TestClient) -> None:
    job_id = _create(client)
    r = client.options(f"/api/uploads/{job_id}")
    assert r.status_code == 204
    assert r.headers["Tus-Resumable"] == "1.0.0"
    assert r.headers["Tus-Version"] == "1.0.0"
    assert r.headers["Tus-Max-Size"] == str(1024**3)


def test_head(client: TestClient) -> None:
    job_id = _create(client, 10)
    r = client.head(f"/api/uploads/{job_id}", headers=TUS)
    assert r.status_code == 200
    assert r.headers["Upload-Offset"] == "0"
    assert r.headers["Upload-Length"] == "10"
    assert r.headers["Cache-Control"] == "no-store"
    assert r.headers["Tus-Resumable"] == "1.0.0"


def test_head_unknown_and_not_uploading(client: TestClient) -> None:
    r = client.head("/api/uploads/nope", headers=TUS)
    assert r.status_code == 404
    assert r.headers["Tus-Resumable"] == "1.0.0"
    job_id = _create(client)
    _store(client).set_state(job_id, JobState.CANCELLED)
    assert client.head(f"/api/uploads/{job_id}", headers=TUS).status_code == 404


def test_missing_or_wrong_tus_resumable(client: TestClient) -> None:
    job_id = _create(client)
    for r in (
        client.head(f"/api/uploads/{job_id}"),
        client.patch(
            f"/api/uploads/{job_id}",
            content=b"x",
            headers={"Content-Type": "application/offset+octet-stream", "Upload-Offset": "0"},
        ),
        client.head(f"/api/uploads/{job_id}", headers={"Tus-Resumable": "0.2.2"}),
    ):
        assert r.status_code == 412
        assert r.headers["Tus-Resumable"] == "1.0.0"


def test_patch_appends_and_completes(client: TestClient, completed: list[str]) -> None:
    job_id = _create(client, 10)
    url = f"/api/uploads/{job_id}"
    r = client.patch(url, content=b"01234", headers={**PATCH_HEADERS, "Upload-Offset": "0"})
    assert r.status_code == 204
    assert r.headers["Upload-Offset"] == "5"
    assert r.headers["Tus-Resumable"] == "1.0.0"
    assert completed == []
    assert client.head(url, headers=TUS).headers["Upload-Offset"] == "5"
    r = client.patch(url, content=b"56789", headers={**PATCH_HEADERS, "Upload-Offset": "5"})
    assert r.status_code == 204
    assert r.headers["Upload-Offset"] == "10"
    assert completed == [job_id]
    store = _store(client)
    assert (store.job_dir(job_id) / "source.bin").read_bytes() == b"0123456789"
    job = store.get(job_id)
    assert job is not None
    assert job.upload_offset == 10
    assert job.state is JobState.UPLOADING  # queueing belongs to the completion hook


def test_patch_offset_mismatch(client: TestClient) -> None:
    job_id = _create(client, 10)
    r = client.patch(f"/api/uploads/{job_id}", content=b"abc", headers={**PATCH_HEADERS, "Upload-Offset": "3"})
    assert r.status_code == 409
    assert r.headers["Tus-Resumable"] == "1.0.0"
    assert client.head(f"/api/uploads/{job_id}", headers=TUS).headers["Upload-Offset"] == "0"


def test_patch_bad_content_type(client: TestClient) -> None:
    job_id = _create(client)
    r = client.patch(
        f"/api/uploads/{job_id}", content=b"abc", headers={**TUS, "Content-Type": "text/plain", "Upload-Offset": "0"}
    )
    assert r.status_code == 415


@pytest.mark.parametrize("offset", [None, "abc", "-1"])
def test_patch_bad_offset_header(client: TestClient, offset: str | None) -> None:
    job_id = _create(client)
    headers = dict(PATCH_HEADERS)
    if offset is not None:
        headers["Upload-Offset"] = offset
    r = client.patch(f"/api/uploads/{job_id}", content=b"abc", headers=headers)
    assert r.status_code == 400


def test_patch_body_over_cap(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(uploads, "UPLOAD_CHUNK_BYTES", 4)
    job_id = _create(client, 10)
    r = client.patch(f"/api/uploads/{job_id}", content=b"01234", headers={**PATCH_HEADERS, "Upload-Offset": "0"})
    assert r.status_code == 413
    assert r.headers["Tus-Resumable"] == "1.0.0"
    assert client.head(f"/api/uploads/{job_id}", headers=TUS).headers["Upload-Offset"] == "0"
    ok = client.patch(f"/api/uploads/{job_id}", content=b"0123", headers={**PATCH_HEADERS, "Upload-Offset": "0"})
    assert ok.status_code == 204


def test_patch_streamed_body_over_cap(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without Content-Length the cap is enforced while streaming and nothing is recorded."""
    monkeypatch.setattr(uploads, "UPLOAD_CHUNK_BYTES", 4)
    job_id = _create(client, 10)

    def body() -> Iterator[bytes]:
        yield b"012"
        yield b"345"

    r = client.patch(f"/api/uploads/{job_id}", content=body(), headers={**PATCH_HEADERS, "Upload-Offset": "0"})
    assert r.status_code == 413
    assert client.head(f"/api/uploads/{job_id}", headers=TUS).headers["Upload-Offset"] == "0"
    assert (_store(client).job_dir(job_id) / "source.bin").stat().st_size == 0


def test_patch_beyond_declared_length(client: TestClient) -> None:
    job_id = _create(client, 3)
    r = client.patch(f"/api/uploads/{job_id}", content=b"0123", headers={**PATCH_HEADERS, "Upload-Offset": "0"})
    assert r.status_code == 413


def test_patch_not_uploading_or_unknown(client: TestClient) -> None:
    headers = {**PATCH_HEADERS, "Upload-Offset": "0"}
    assert client.patch("/api/uploads/nope", content=b"a", headers=headers).status_code == 404
    job_id = _create(client)
    _store(client).set_state(job_id, JobState.CANCELLED)
    assert client.patch(f"/api/uploads/{job_id}", content=b"a", headers=headers).status_code == 404


def _expire(client: TestClient) -> None:
    store = _store(client)
    future = store._clock() + 10 * 365 * 24 * 3600  # noqa: SLF001
    store._clock = lambda: future  # noqa: SLF001


def test_head_expired_upload_is_not_found(client: TestClient) -> None:
    job_id = _create(client)
    _expire(client)
    r = client.head(f"/api/uploads/{job_id}", headers=TUS)
    assert r.status_code == 404
    assert r.headers["Tus-Resumable"] == "1.0.0"


def test_patch_expired_upload_appends_nothing_and_is_not_queued(client: TestClient, completed: list[str]) -> None:
    job_id = _create(client, 10)
    _expire(client)
    r = client.patch(f"/api/uploads/{job_id}", content=b"0123456789", headers={**PATCH_HEADERS, "Upload-Offset": "0"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "upload_not_found"
    assert completed == []
    store = _store(client)
    assert not (store.job_dir(job_id) / "source.bin").exists()
    job = store.get(job_id)
    assert job is not None
    assert job.upload_offset == 0
    assert job.state is JobState.UPLOADING


def test_patch_completing_after_expiry_is_not_queued(client: TestClient, completed: list[str]) -> None:
    job_id = _create(client, 10)
    store = _store(client)
    real = store.is_expired
    calls = 0

    def expires_after_first_check(job: Job) -> bool:
        nonlocal calls
        calls += 1
        return calls > 1 or real(job)

    store.is_expired = expires_after_first_check  # type: ignore[method-assign]
    r = client.patch(f"/api/uploads/{job_id}", content=b"0123456789", headers={**PATCH_HEADERS, "Upload-Offset": "0"})
    assert r.status_code == 404
    assert completed == []
