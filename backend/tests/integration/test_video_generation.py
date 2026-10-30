"""Video Generation: the tool run through the worker with the fake provider (no network, no cost)."""

import io
import json
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from fakes_llm import FakeLLM
from fakes_transcribe import FakeTranscriber
from fakes_videogen import CAPABILITIES, FakeVideoGenerator
from videokit.core.config import Settings
from videokit.core.jobs import Job, JobState, JobStore
from videokit.core.llm import LLMChain
from videokit.core.llm.base import Usage
from videokit.core.transcribe import TranscriberChain
from videokit.core.videogen import PollResult, VideoChain, VideoError
from videokit.core.worker import Providers
from videokit.main import create_app
from videokit.tools.video_generation import TOOL, pipeline
from videokit.tools.video_generation.inputs import CreationInputs, store_inputs

MakePicture = Callable[..., Path]
PROMPT = "A red fox runs across a snowy field"
FAKE_MODEL = "gemini-omni-1.1-flash"  # a priced model, so the cost is known


@pytest.fixture(autouse=True)
def fast_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pipeline, "POLL_INTERVAL_S", 0.01)
    monkeypatch.setattr(pipeline, "WAIT_CAP_S", 5.0)


def _client(tmp_path: Path, fake: FakeVideoGenerator, **settings: Any) -> TestClient:
    config = Settings(
        data_dir=tmp_path / "data",
        anthropic_api_key=SecretStr("sk-test"),
        gemini_api_key=SecretStr("g-test"),
        video_providers=["omni"],
        **settings,
    )
    providers = Providers(
        transcriber=TranscriberChain([FakeTranscriber()]), llm=LLMChain([FakeLLM([])]), video=VideoChain([fake])
    )
    return TestClient(create_app(config, providers, static_dir=tmp_path / "static"))


@pytest.fixture
def fake() -> FakeVideoGenerator:
    return FakeVideoGenerator(model=FAKE_MODEL)


@pytest.fixture
def client(tmp_path: Path, fake: FakeVideoGenerator) -> Iterator[TestClient]:
    with _client(tmp_path, fake) as c:
        yield c


def _store(client: TestClient) -> JobStore:
    store: JobStore = client.app.state.store  # type: ignore[attr-defined]
    return store


def _job(client: TestClient, job_id: str) -> Job:
    job = _store(client).get(job_id)
    assert job is not None
    return job


def _wait(client: TestClient, job_id: str, *states: JobState, timeout: float = 15.0) -> Job:
    deadline = time.monotonic() + timeout
    while _job(client, job_id).state not in states:
        if time.monotonic() > deadline:
            raise AssertionError(f"job did not reach {states} (now {_job(client, job_id).state})")
        time.sleep(0.02)
    return _job(client, job_id)


def _submit(
    client: TestClient,
    pictures: list[bytes] | None = None,
    *,
    prompt: str = PROMPT,
    duration_s: int = 5,
    aspect_ratio: str = "9:16",
    resolution: str = "1080p",
) -> str:
    """Create a job and store its inputs without the multipart route, so a test can control the fields exactly."""
    access = client.app.state.job_access  # type: ignore[attr-defined]
    job = access.create("video_generation")
    fields = CreationInputs(prompt, duration_s, aspect_ratio, resolution)  # type: ignore[arg-type]
    store_inputs(access.job_dir(job.id), access.ctx.media, fields, [io.BytesIO(p) for p in pictures or []])
    client.portal.call(access.complete, job.id)  # type: ignore[union-attr]
    return str(job.id)


def _json(client: TestClient, job_id: str, name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((_store(client).job_dir(job_id) / name).read_text(encoding="utf-8"))
    return data


def _streams(path: Path, kind: str) -> str:
    cmd = ["ffprobe", "-v", "error", "-select_streams", kind, "-show_entries", "stream=codec_name", "-of", "csv=p=0"]
    return subprocess.run([*cmd, str(path)], check=True, capture_output=True, text=True).stdout.strip()


# --- tool object ------------------------------------------------------------------------------------------------


def test_tool_identity_and_lane_settings() -> None:
    assert TOOL.id == "video_generation"
    assert TOOL.name == "Video Generation"
    assert TOOL.description == "Generate a short silent clip from a prompt and pictures."
    assert TOOL.concurrency == 3
    assert TOOL.resume_after_restart is True


def test_enabled_only_with_a_video_provider() -> None:
    assert TOOL.enabled(Settings(video_providers=[])) is False
    assert TOOL.enabled(Settings(video_providers=["omni"])) is True


def test_the_tool_is_listed_when_enabled(client: TestClient) -> None:
    listed = {t["id"] for t in client.get("/api/tools").json()}
    assert "video_generation" in listed


def test_the_tool_is_hidden_when_no_video_provider_is_configured(tmp_path: Path) -> None:
    config = Settings(data_dir=tmp_path / "data", anthropic_api_key=SecretStr("sk-test"))
    providers = Providers(transcriber=TranscriberChain([FakeTranscriber()]), llm=LLMChain([FakeLLM([])]))
    with TestClient(create_app(config, providers, static_dir=tmp_path / "static")) as c:
        assert "video_generation" not in {t["id"] for t in c.get("/api/tools").json()}


def test_accept_fails_a_job_whose_inputs_are_not_complete(client: TestClient) -> None:
    access = client.app.state.job_access  # type: ignore[attr-defined]
    job = access.create("video_generation")
    client.portal.call(access.complete, job.id)  # type: ignore[union-attr]
    failed = _job(client, job.id)
    assert (failed.state, failed.failure_code) == (JobState.FAILED, "invalid_request")


# --- success --------------------------------------------------------------------------------------------------


def test_happy_path_without_pictures(client: TestClient, fake: FakeVideoGenerator) -> None:
    job_id = _submit(client, duration_s=6, aspect_ratio="16:9", resolution="720p")
    job = _wait(client, job_id, JobState.DONE, JobState.FAILED)
    assert job.state is JobState.DONE
    assert len(fake.requests) == 1
    request = fake.requests[0]
    assert (request.prompt, request.pictures) == (PROMPT, [])
    assert (request.duration_s, request.aspect_ratio, request.resolution) == (6, "16:9", "720p")
    job_dir = _store(client).job_dir(job_id)
    assert (job_dir / "clip.mp4").is_file() and (job_dir / "thumbnail.jpg").is_file()
    assert not (job_dir / "raw.mp4").exists()


def test_happy_path_with_pictures_sends_them_in_order_with_the_reference_prompt(
    client: TestClient, fake: FakeVideoGenerator, make_picture: MakePicture
) -> None:
    first, second = make_picture("png", 64, 48).read_bytes(), make_picture("jpeg", 32, 32).read_bytes()
    job_id = _submit(client, [first, second])
    assert _wait(client, job_id, JobState.DONE, JobState.FAILED).state is JobState.DONE
    request = fake.requests[0]
    assert [p.position for p in request.pictures] == [1, 2]
    assert all(p.jpeg[:3] == b"\xff\xd8\xff" for p in request.pictures)
    assert request.prompt == f"[# References <IMAGE_REF_0>@Picture1 <IMAGE_REF_1>@Picture2]\n{PROMPT}"
    inputs = _json(client, job_id, "inputs.json")
    assert request.pictures[0].jpeg == (_store(client).job_dir(job_id) / inputs["pictures"][0]).read_bytes()


def test_the_job_passes_through_generating(client: TestClient) -> None:
    states: list[JobState] = []
    job_id = _submit(client)
    deadline = time.monotonic() + 15
    while not states or states[-1] is not JobState.DONE:
        state = _job(client, job_id).state
        if not states or states[-1] is not state:
            states.append(state)
        assert time.monotonic() < deadline
        time.sleep(0.001)
    assert JobState.GENERATING in states


def test_result_json_describes_the_clip(client: TestClient) -> None:
    job_id = _submit(client, duration_s=5, aspect_ratio="9:16", resolution="1080p")
    _wait(client, job_id, JobState.DONE)
    result = _json(client, job_id, "result.json")
    clip = _store(client).job_dir(job_id) / "clip.mp4"
    assert list(result) == [
        "file",
        "thumbnail",
        "download_name",
        "duration_s",
        "width",
        "height",
        "aspect_ratio",
        "resolution",
        "size_bytes",
    ]
    assert (result["file"], result["thumbnail"]) == ("clip.mp4", "thumbnail.jpg")
    assert result["download_name"] == f"{PROMPT}.mp4"
    assert (result["width"], result["height"]) == (160, 120)  # the fake provider's clip, as probed
    assert (result["aspect_ratio"], result["resolution"]) == ("16:9", "120p")  # derived from it, not the request
    assert result["duration_s"] == pytest.approx(1.0, abs=0.2)
    assert result["size_bytes"] == clip.stat().st_size


@pytest.mark.parametrize(
    ("clip_size", "requested", "expected"),
    [
        ("1080x1920", ("16:9", "720p"), ("9:16", "1080p")),
        ("1920x1080", ("9:16", "720p"), ("16:9", "1080p")),
        ("720x1280", ("16:9", "1080p"), ("9:16", "720p")),
        ("1280x720", ("9:16", "1080p"), ("16:9", "720p")),
        ("640x640", ("9:16", "1080p"), ("16:9", "640p")),
        ("480x848", ("16:9", "720p"), ("9:16", "480p")),
    ],
)
def test_result_reports_the_actual_orientation_and_resolution_of_the_clip(
    tmp_path: Path, clip_size: str, requested: tuple[str, str], expected: tuple[str, str]
) -> None:
    with _client(tmp_path, FakeVideoGenerator(model=FAKE_MODEL, clip_size=clip_size)) as client:
        job_id = _submit(client, aspect_ratio=requested[0], resolution=requested[1])
        _wait(client, job_id, JobState.DONE)
        result = _json(client, job_id, "result.json")
        assert (result["aspect_ratio"], result["resolution"]) == expected
        assert f"{result['width']}x{result['height']}" == clip_size  # delivered as returned


def test_the_clip_has_no_audio_stream(client: TestClient) -> None:
    job_id = _submit(client)
    _wait(client, job_id, JobState.DONE)
    clip = _store(client).job_dir(job_id) / "clip.mp4"
    assert _streams(clip, "v") == "h264"
    assert _streams(clip, "a") == ""


def test_polls_until_the_provider_is_done_and_the_stored_interaction_is_forgotten(tmp_path: Path) -> None:
    fake = FakeVideoGenerator(model=FAKE_MODEL, submits=["op-xyz"], polls=["pending", "pending", "done"])
    with _client(tmp_path, fake) as client:
        job_id = _submit(client)
        _wait(client, job_id, JobState.DONE)
    assert fake.poll_refs == ["op-xyz"] * 3
    assert fake.forgotten == ["op-xyz"]
    assert fake.cancelled == []


def test_records_the_provider_and_the_submit_and_operation_files(tmp_path: Path) -> None:
    fake = FakeVideoGenerator(model=FAKE_MODEL, submits=["op-xyz"])
    with _client(tmp_path, fake) as client:
        job_id = _submit(client)
        job = _wait(client, job_id, JobState.DONE)
        assert job.providers == {"video": f"fake:{FAKE_MODEL}"}
        assert "started_at" in _json(client, job_id, "submit.json")
        operation = _json(client, job_id, "operation.json")
    assert (operation["provider"], operation["ref"]) == (f"fake:{FAKE_MODEL}", "op-xyz")
    assert operation["submitted_at"]


def test_submit_json_is_written_before_the_provider_is_called_and_operation_json_right_after(
    tmp_path: Path,
) -> None:
    seen: dict[str, tuple[bool, bool]] = {}

    class Spying(FakeVideoGenerator):
        def submit(self, req: Any) -> str:
            job_dir = next((tmp_path / "data" / "jobs").iterdir())
            seen["during submit"] = ((job_dir / "submit.json").exists(), (job_dir / "operation.json").exists())
            return super().submit(req)

    with _client(tmp_path, Spying(model=FAKE_MODEL)) as client:
        _wait(client, _submit(client), JobState.DONE)
    assert seen["during submit"] == (True, False)


# --- usage and cost -------------------------------------------------------------------------------------------


def test_usage_and_cost_are_recorded_from_the_provider_report(tmp_path: Path, make_picture: MakePicture) -> None:
    fake = FakeVideoGenerator(model=FAKE_MODEL, usage=Usage(input_tokens=1000, video_output_tokens=50_000))
    with _client(tmp_path, fake) as client:
        job_id = _submit(client, [make_picture("png").read_bytes()])
        job = _wait(client, job_id, JobState.DONE)
    assert job.usage["input_tokens"] == 1000
    assert job.usage["video_output_tokens"] == 50_000
    assert job.usage["cost_usd"] == pytest.approx(1000 * 1.5 / 1e6 + 50_000 * 17.5 / 1e6)


def test_the_default_fake_usage_follows_the_documented_rate(client: TestClient, make_picture: MakePicture) -> None:
    job_id = _submit(client, [make_picture("png").read_bytes()], duration_s=7, resolution="720p")
    job = _wait(client, job_id, JobState.DONE)
    assert job.usage["video_output_tokens"] == 7 * 5792
    assert job.usage["input_tokens"] == 1120
    assert job.usage["cost_usd"] == pytest.approx(1120 * 1.5 / 1e6 + 7 * 5792 * 17.5 / 1e6)


def test_zero_reported_usage_falls_back_to_the_estimate_from_the_stored_inputs(
    tmp_path: Path, make_picture: MakePicture
) -> None:
    fake = FakeVideoGenerator(model=FAKE_MODEL, usage=Usage())
    with _client(tmp_path, fake) as client:
        job_id = _submit(client, [make_picture("png").read_bytes()] * 2, duration_s=8, resolution="1080p")
        job = _wait(client, job_id, JobState.DONE)
    assert job.usage["video_output_tokens"] == 8 * 8688
    assert job.usage["input_tokens"] == 2 * 1120
    assert job.usage["cost_usd"] == pytest.approx(2 * 1120 * 1.5 / 1e6 + 8 * 8688 * 17.5 / 1e6)


def test_an_unpriced_model_records_an_unknown_cost(tmp_path: Path) -> None:
    fake = FakeVideoGenerator(model="not-in-the-price-table")
    with _client(tmp_path, fake) as client:
        job = _wait(client, _submit(client), JobState.DONE)
    assert job.usage["cost_usd"] is None
    assert job.usage["video_output_tokens"] > 0


def test_pricing_overrides_apply_to_the_cost(tmp_path: Path) -> None:
    fake = FakeVideoGenerator(model=FAKE_MODEL, usage=Usage(video_output_tokens=1_000_000))
    with _client(tmp_path, fake, pricing_overrides={FAKE_MODEL: {"video_output": 1.0}}) as client:
        job = _wait(client, _submit(client), JobState.DONE)
    assert job.usage["cost_usd"] == pytest.approx(1.0)


# --- failures -------------------------------------------------------------------------------------------------

FAILURE_CODES = [
    "provider_out_of_credit",
    "content_blocked",
    "invalid_picture",
    "provider_unavailable",
    "video_failed",
]


@pytest.mark.parametrize("code", FAILURE_CODES)
def test_a_submit_error_fails_the_job_with_its_code(tmp_path: Path, code: str) -> None:
    fake = FakeVideoGenerator(model=FAKE_MODEL, submits=[VideoError(code, "provider said: secret")])  # type: ignore[arg-type]
    with _client(tmp_path, fake) as client:
        job = _wait(client, _submit(client), JobState.FAILED, JobState.DONE)
        assert (job.state, job.failure_code) == (JobState.FAILED, code)
        assert job.failure_reason and "secret" not in job.failure_reason
        assert not (_store(client).job_dir(job.id) / "operation.json").exists()
    assert fake.poll_refs == []


@pytest.mark.parametrize("code", FAILURE_CODES)
def test_a_finished_poll_that_carries_an_error_fails_the_job_with_its_code(tmp_path: Path, code: str) -> None:
    error = VideoError(code, "provider said: secret")  # type: ignore[arg-type]
    fake = FakeVideoGenerator(model=FAKE_MODEL, polls=[PollResult(done=True, video=None, usage=Usage(), error=error)])
    with _client(tmp_path, fake) as client:
        job = _wait(client, _submit(client), JobState.FAILED, JobState.DONE)
        assert (job.state, job.failure_code) == (JobState.FAILED, code)
        assert job.failure_reason and "secret" not in job.failure_reason
        assert job.usage["video_output_tokens"] == 0  # nothing was produced, nothing is charged
    assert fake.forgotten == []


@pytest.mark.parametrize("code", ["provider_out_of_credit", "content_blocked", "invalid_picture", "video_failed"])
def test_a_raised_terminal_poll_error_fails_the_job_with_its_code(tmp_path: Path, code: str) -> None:
    fake = FakeVideoGenerator(model=FAKE_MODEL, polls=["pending", VideoError(code, "secret")])  # type: ignore[arg-type]
    with _client(tmp_path, fake) as client:
        job = _wait(client, _submit(client), JobState.FAILED, JobState.DONE)
    assert (job.state, job.failure_code) == (JobState.FAILED, code)


def test_a_raised_provider_unavailable_while_polling_is_transient(tmp_path: Path) -> None:
    transient = VideoError("provider_unavailable")
    fake = FakeVideoGenerator(model=FAKE_MODEL, polls=[transient, "pending", transient, "done"])
    with _client(tmp_path, fake) as client:
        job = _wait(client, _submit(client), JobState.DONE, JobState.FAILED)
    assert job.state is JobState.DONE
    assert fake.submit_count == 1  # never resubmitted
    assert len(fake.poll_refs) == 4


def test_a_provider_unavailable_that_never_recovers_times_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pipeline, "WAIT_CAP_S", 0.15)
    fake = FakeVideoGenerator(model=FAKE_MODEL, polls=[VideoError("provider_unavailable")] * 1000)
    with _client(tmp_path, fake) as client:
        job = _wait(client, _submit(client), JobState.FAILED, JobState.DONE)
    assert (job.state, job.failure_code) == (JobState.FAILED, "provider_timeout")


def test_an_unexpected_poll_exception_fails_the_job_as_video_failed(tmp_path: Path) -> None:
    fake = FakeVideoGenerator(model=FAKE_MODEL, polls=[OSError("disk full: secret")])
    with _client(tmp_path, fake) as client:
        job = _wait(client, _submit(client), JobState.FAILED, JobState.DONE)
    assert (job.state, job.failure_code) == (JobState.FAILED, "video_failed")
    assert job.failure_reason and "secret" not in job.failure_reason


def test_a_finished_poll_without_a_video_fails_the_job(tmp_path: Path) -> None:
    fake = FakeVideoGenerator(model=FAKE_MODEL, polls=[PollResult(done=True, video=None, usage=Usage(), error=None)])
    with _client(tmp_path, fake) as client:
        job = _wait(client, _submit(client), JobState.FAILED, JobState.DONE)
    assert (job.state, job.failure_code) == (JobState.FAILED, "video_failed")


def test_an_unprocessable_download_fails_the_job_but_is_still_charged(tmp_path: Path) -> None:
    def broken(ref: str, out_dir: Path) -> PollResult:
        raw = out_dir / "raw.mp4"
        raw.write_bytes(b"not a video")
        return PollResult(done=True, video=raw, usage=Usage(video_output_tokens=1000), error=None)

    fake = FakeVideoGenerator(model=FAKE_MODEL)
    fake.poll = broken  # type: ignore[method-assign]
    with _client(tmp_path, fake) as client:
        job = _wait(client, _submit(client), JobState.FAILED, JobState.DONE)
    assert (job.state, job.failure_code) == (JobState.FAILED, "video_failed")
    assert job.usage["video_output_tokens"] == 1000


def test_the_wait_cap_fails_the_job_as_provider_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pipeline, "WAIT_CAP_S", 0.15)
    fake = FakeVideoGenerator(model=FAKE_MODEL, polls=["pending"] * 1000)
    with _client(tmp_path, fake) as client:
        job = _wait(client, _submit(client), JobState.FAILED, JobState.DONE)
        assert (job.state, job.failure_code) == (JobState.FAILED, "provider_timeout")
        assert job.failure_reason
    assert fake.cancelled == ["op-1"]  # stop generating at the provider


def test_the_default_wait_cap_is_twenty_minutes_and_the_poll_interval_ten_seconds() -> None:
    code = "from videokit.tools.video_generation import pipeline as p; print(p.POLL_INTERVAL_S, p.WAIT_CAP_S)"
    out = subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True).stdout
    assert out.split() == ["10.0", "1200.0"]


def test_a_failed_generation_does_not_forget_or_leave_a_clip(tmp_path: Path) -> None:
    fake = FakeVideoGenerator(model=FAKE_MODEL, polls=[VideoError("video_failed")])
    with _client(tmp_path, fake) as client:
        job = _wait(client, _submit(client), JobState.FAILED, JobState.DONE)
        assert not (_store(client).job_dir(job.id) / "result.json").exists()


# --- cancel ---------------------------------------------------------------------------------------------------


def test_cancel_while_generating_cancels_at_the_provider_and_deletes_the_job_data(tmp_path: Path) -> None:
    fake = FakeVideoGenerator(model=FAKE_MODEL, submits=["op-9"], polls=["pending"] * 1000)
    with _client(tmp_path, fake) as client:
        job_id = _submit(client)
        _wait(client, job_id, JobState.GENERATING)
        deadline = time.monotonic() + 10
        while not fake.poll_refs:  # the pipeline is inside its poll loop
            assert time.monotonic() < deadline
            time.sleep(0.005)
        assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 202
        _wait(client, job_id, JobState.CANCELLED)
        job_dir = _store(client).job_dir(job_id)
        deadline = time.monotonic() + 10
        while job_dir.exists():
            assert time.monotonic() < deadline
            time.sleep(0.01)
    assert fake.cancelled == ["op-9"]
    assert fake.forgotten == []


# --- resume after a restart (research V3) ---------------------------------------------------------------------


class _Crash(BaseException):
    """Stands in for the process dying: it passes every `except Exception`, so the job is left as it was."""


def _wait_polling(fake: FakeVideoGenerator, count: int = 1) -> None:
    deadline = time.monotonic() + 15
    while len(fake.poll_refs) < count:
        assert time.monotonic() < deadline, "the job never started polling"
        time.sleep(0.005)


def _back_date_submission(job_dir: Path, seconds: float) -> None:
    path = job_dir / "operation.json"
    operation = json.loads(path.read_text(encoding="utf-8"))
    operation["submitted_at"] = (datetime.now(UTC) - timedelta(seconds=seconds)).isoformat()
    path.write_text(json.dumps(operation), encoding="utf-8")


def test_a_restart_between_accept_and_completion_polls_the_stored_operation_and_never_submits(
    tmp_path: Path, make_picture: MakePicture
) -> None:
    before = FakeVideoGenerator(model=FAKE_MODEL, submits=["op-A"], polls=["pending"] * 1000)
    with _client(tmp_path, before) as client:
        job_id = _submit(client, [make_picture("png").read_bytes()], duration_s=7, resolution="720p")
        _wait_polling(before)
    assert _json(client, job_id, "operation.json")["ref"] == "op-A"
    assert _job(client, job_id).state is JobState.GENERATING  # the stop left it mid-run

    after = FakeVideoGenerator(model=FAKE_MODEL)  # reports no usage: it never saw the request
    with _client(tmp_path, after) as client:
        job = _wait(client, job_id, JobState.DONE, JobState.FAILED)
        assert job.state is JobState.DONE
        assert (_store(client).job_dir(job_id) / "clip.mp4").is_file()
        assert _json(client, job_id, "result.json")["file"] == "clip.mp4"
    assert before.submit_count == 1
    assert after.submit_count == 0  # never paid twice
    assert after.poll_refs == ["op-A"]
    assert after.forgotten == ["op-A"]
    assert job.providers == {"video": f"fake:{FAKE_MODEL}"}


def test_a_resumed_poll_without_reported_usage_is_billed_from_the_stored_inputs(
    tmp_path: Path, make_picture: MakePicture
) -> None:
    before = FakeVideoGenerator(model=FAKE_MODEL, submits=["op-A"], polls=["pending"] * 1000)
    with _client(tmp_path, before) as client:
        job_id = _submit(client, [make_picture("png").read_bytes()] * 2, duration_s=8, resolution="1080p")
        _wait_polling(before)
    with _client(tmp_path, FakeVideoGenerator(model=FAKE_MODEL)) as client:
        job = _wait(client, job_id, JobState.DONE)
    assert job.usage["video_output_tokens"] == 8 * 8688
    assert job.usage["input_tokens"] == 2 * 1120
    assert job.usage["cost_usd"] == pytest.approx(2 * 1120 * 1.5 / 1e6 + 8 * 8688 * 17.5 / 1e6)


def test_a_restart_with_only_submit_json_fails_resume_impossible_without_submitting(tmp_path: Path) -> None:
    class Dying(FakeVideoGenerator):
        def submit(self, req: Any) -> str:
            raise _Crash  # after submit.json, before any operation.json

    with _client(tmp_path, Dying(model=FAKE_MODEL)) as client:
        job_id = _submit(client)
        _wait(client, job_id, JobState.GENERATING)
        deadline = time.monotonic() + 10
        job_dir = _store(client).job_dir(job_id)
        while not (job_dir / "submit.json").exists():
            assert time.monotonic() < deadline
            time.sleep(0.005)
    assert not (job_dir / "operation.json").exists()

    after = FakeVideoGenerator(model=FAKE_MODEL)
    with _client(tmp_path, after) as client:
        job = _wait(client, job_id, JobState.FAILED, JobState.DONE)
        assert (job.state, job.failure_code) == (JobState.FAILED, "resume_impossible")
        assert job.failure_reason
    assert after.submit_count == 0
    assert after.poll_refs == []


def test_a_job_left_generating_before_it_wrote_anything_is_submitted_fresh(tmp_path: Path) -> None:
    class Dying(FakeVideoGenerator):
        def poll(self, ref: str, out_dir: Path) -> PollResult:
            raise _Crash

    # Leave a job `generating` with no submit.json and no operation.json: it stopped before the first write.
    with _client(tmp_path, Dying(model=FAKE_MODEL)) as client:
        job_id = _submit(client)
        _wait(client, job_id, JobState.GENERATING)
        job_dir = _store(client).job_dir(job_id)
        deadline = time.monotonic() + 10
        while not (job_dir / "operation.json").exists():
            assert time.monotonic() < deadline
            time.sleep(0.005)
    (job_dir / "operation.json").unlink()
    (job_dir / "submit.json").unlink()

    after = FakeVideoGenerator(model=FAKE_MODEL)
    with _client(tmp_path, after) as client:
        assert _wait(client, job_id, JobState.DONE, JobState.FAILED).state is JobState.DONE
    assert after.submit_count == 1


def test_a_queued_video_job_survives_a_restart_and_runs(tmp_path: Path) -> None:
    # Three slots hold generating jobs; the fourth job waits in the queue when the server stops.
    before = FakeVideoGenerator(model=FAKE_MODEL, polls=["pending"] * 10_000)
    with _client(tmp_path, before) as client:
        running = [_submit(client, prompt=f"{PROMPT} {n}") for n in range(3)]
        for job_id in running:
            _wait(client, job_id, JobState.GENERATING)
        waiting = _submit(client, prompt=f"{PROMPT} waiting")
        assert _job(client, waiting).state is JobState.QUEUED
        _wait_polling(before, 3)
    assert before.submit_count == 3

    after = FakeVideoGenerator(model=FAKE_MODEL, submits=["op-new"], polls=["done"] * 4)
    with _client(tmp_path, after) as client:
        for job_id in [*running, waiting]:
            assert _wait(client, job_id, JobState.DONE, JobState.FAILED).state is JobState.DONE
    assert after.submit_count == 1  # only the job that never ran; the others were polled, not resubmitted
    assert sorted(after.poll_refs) == sorted(["op-1", "op-2", "op-3", "op-new"])


def test_youtube_prep_jobs_in_the_same_restart_still_fail_server_restarted(tmp_path: Path) -> None:
    before = FakeVideoGenerator(model=FAKE_MODEL, submits=["op-A"], polls=["pending"] * 1000)
    with _client(tmp_path, before) as client:
        video_id = _submit(client)
        _wait_polling(before)
    config = Settings(data_dir=tmp_path / "data")
    store = JobStore(config.data_dir, config.job_ttl_hours, config.max_upload_bytes)
    prep = store.create("youtube_prep", 10)
    store.set_upload_offset(prep.id, 10)
    for state in (JobState.QUEUED, JobState.EXTRACTING, JobState.TRANSCRIBING):
        store.set_state(prep.id, state)

    with _client(tmp_path, FakeVideoGenerator(model=FAKE_MODEL)) as client:
        assert _wait(client, video_id, JobState.DONE, JobState.FAILED).state is JobState.DONE
        lost = _job(client, prep.id)
    assert (lost.state, lost.failure_code) == (JobState.FAILED, "server_restarted")


def test_the_wait_cap_counts_from_the_original_submission(tmp_path: Path) -> None:
    before = FakeVideoGenerator(model=FAKE_MODEL, submits=["op-A"], polls=["pending"] * 1000)
    with _client(tmp_path, before) as client:
        job_id = _submit(client)
        _wait_polling(before)
        job_dir = _store(client).job_dir(job_id)
    _back_date_submission(job_dir, pipeline.WAIT_CAP_S + 60)  # submitted longer ago than the cap allows

    after = FakeVideoGenerator(model=FAKE_MODEL, polls=["pending"] * 1000)
    with _client(tmp_path, after) as client:
        job = _wait(client, job_id, JobState.FAILED, JobState.DONE)
    assert (job.state, job.failure_code) == (JobState.FAILED, "provider_timeout")
    assert after.poll_refs == ["op-A"]  # one last look before giving up
    assert after.cancelled == ["op-A"]
    assert after.submit_count == 0


def test_a_clip_finished_while_the_server_was_down_is_collected_even_past_the_cap(tmp_path: Path) -> None:
    before = FakeVideoGenerator(model=FAKE_MODEL, submits=["op-A"], polls=["pending"] * 1000)
    with _client(tmp_path, before) as client:
        job_id = _submit(client)
        _wait_polling(before)
        job_dir = _store(client).job_dir(job_id)
    _back_date_submission(job_dir, pipeline.WAIT_CAP_S + 60)

    with _client(tmp_path, FakeVideoGenerator(model=FAKE_MODEL)) as client:
        assert _wait(client, job_id, JobState.DONE, JobState.FAILED).state is JobState.DONE


def test_a_resumed_job_can_be_cancelled(tmp_path: Path) -> None:
    before = FakeVideoGenerator(model=FAKE_MODEL, submits=["op-A"], polls=["pending"] * 1000)
    with _client(tmp_path, before) as client:
        job_id = _submit(client)
        _wait_polling(before)
    after = FakeVideoGenerator(model=FAKE_MODEL, polls=["pending"] * 10_000)
    with _client(tmp_path, after) as client:
        _wait_polling(after)
        assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 202
        _wait(client, job_id, JobState.CANCELLED)
    assert after.cancelled == ["op-A"]


# --- creation route -------------------------------------------------------------------------------------------

OMNI_CAPS = replace(CAPABILITIES, max_pictures=7)  # what the Omni adapter reports
CREATE = "/api/tools/video_generation/jobs"
FORM = {"prompt": PROMPT, "duration_s": "6", "aspect_ratio": "16:9", "resolution": "720p"}


@pytest.fixture
def omni(tmp_path: Path) -> Iterator[TestClient]:
    with _client(tmp_path, FakeVideoGenerator(model=FAKE_MODEL, caps=OMNI_CAPS)) as c:
        yield c


def _picture_files(*pictures: bytes) -> list[tuple[str, tuple[str, bytes, str]]]:
    return [("pictures", (f"p{n}.bin", data, "application/octet-stream")) for n, data in enumerate(pictures, start=1)]


def _row_count(client: TestClient) -> int:
    with closing(sqlite3.connect(_store(client)._db_path)) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])


def _nothing_left(client: TestClient) -> None:
    assert _row_count(client) == 0
    jobs_dir = _store(client).job_dir("x").parent
    assert not jobs_dir.exists() or list(jobs_dir.iterdir()) == []


def test_create_stores_the_inputs_and_queues_the_job_without_an_upload(
    omni: TestClient, make_picture: MakePicture
) -> None:
    first, second = make_picture("png", 64, 48).read_bytes(), make_picture("jpeg", 32, 32).read_bytes()
    r = omni.post(CREATE, data=FORM, files=_picture_files(first, second))
    assert r.status_code == 201
    assert list(r.json()) == ["job_id"]
    job_id = r.json()["job_id"]
    assert _job(omni, job_id).tool_id == "video_generation"
    inputs = _json(omni, job_id, "inputs.json")
    assert inputs == {
        "prompt": PROMPT,
        "duration_s": 6,
        "aspect_ratio": "16:9",
        "resolution": "720p",
        "pictures": ["pictures/1.jpg", "pictures/2.jpg"],
    }
    assert _wait(omni, job_id, JobState.DONE, JobState.FAILED).state is JobState.DONE
    assert omni.get(f"/api/jobs/{job_id}").json()["state"] == "done"


def test_create_without_pictures_and_with_defaults(omni: TestClient) -> None:
    r = omni.post(CREATE, data={"prompt": f"  {PROMPT}  ", "duration_s": "5"})
    assert r.status_code == 201
    inputs = _json(omni, r.json()["job_id"], "inputs.json")
    assert (inputs["prompt"], inputs["aspect_ratio"], inputs["resolution"], inputs["pictures"]) == (
        PROMPT,
        "9:16",
        "1080p",
        [],
    )


def test_a_multi_line_prompt_at_the_limit_is_stored_with_lf_line_breaks(omni: TestClient) -> None:
    # A browser sends each line break of a multipart text field as CRLF.
    prompt = "\r\n".join(["x" * 99] * 19) + "\r\n" + "x" * 100
    assert len(prompt) > 2000
    r = omni.post(CREATE, data={"prompt": prompt, "duration_s": "5"})
    assert r.status_code == 201
    stored = _json(omni, r.json()["job_id"], "inputs.json")["prompt"]
    assert stored == prompt.replace("\r\n", "\n")
    assert len(stored) == 2000


def test_an_empty_file_part_is_no_picture(omni: TestClient, make_picture: MakePicture) -> None:
    data = make_picture("png").read_bytes()
    files = [*_picture_files(data), ("pictures", ("", b"", "application/octet-stream"))]
    r = omni.post(CREATE, data=FORM, files=files)
    assert r.status_code == 201
    assert _json(omni, r.json()["job_id"], "inputs.json")["pictures"] == ["pictures/1.jpg"]


def test_the_job_has_no_upload_stage(tmp_path: Path) -> None:
    fake = FakeVideoGenerator(model=FAKE_MODEL, caps=OMNI_CAPS, polls=["pending"] * 1000)
    with _client(tmp_path, fake) as client:
        job_id = client.post(CREATE, data=FORM).json()["job_id"]
        job = _job(client, job_id)
        assert job.state in (JobState.QUEUED, JobState.GENERATING)
        assert job.upload_length == 0


@pytest.mark.parametrize(
    "form",
    [
        {**FORM, "prompt": "   "},
        {"duration_s": "5"},
        {**FORM, "prompt": "x" * 2001},
        {**FORM, "duration_s": "4"},
        {**FORM, "duration_s": "five"},
        {"prompt": PROMPT},
        {**FORM, "aspect_ratio": "4:3"},
        {**FORM, "resolution": "4k"},
    ],
)
def test_invalid_fields_are_rejected_and_leave_nothing_behind(omni: TestClient, form: dict[str, str]) -> None:
    r = omni.post(CREATE, data=form)
    assert (r.status_code, r.json()["error"]["code"]) == (422, "invalid_request")
    _nothing_left(omni)


def test_a_duration_the_provider_does_not_support_is_rejected(tmp_path: Path) -> None:
    caps = replace(OMNI_CAPS, durations_s=[5, 6, 7])
    with _client(tmp_path, FakeVideoGenerator(model=FAKE_MODEL, caps=caps)) as client:
        r = client.post(CREATE, data={**FORM, "duration_s": "8"})
        assert (r.status_code, r.json()["error"]["code"]) == (422, "invalid_request")
        _nothing_left(client)


def test_a_non_picture_is_rejected_and_the_job_is_discarded(omni: TestClient, make_picture: MakePicture) -> None:
    good = make_picture("png").read_bytes()
    r = omni.post(CREATE, data=FORM, files=_picture_files(good, b"this is not a picture"))
    assert (r.status_code, r.json()["error"]["code"]) == (422, "invalid_picture")
    _nothing_left(omni)


def test_an_undecodable_picture_is_rejected_and_the_job_is_discarded(omni: TestClient) -> None:
    r = omni.post(CREATE, data=FORM, files=_picture_files(b"\xff\xd8\xff" + b"\x00" * 64))
    assert (r.status_code, r.json()["error"]["code"]) == (422, "invalid_picture")
    _nothing_left(omni)


def test_a_text_field_over_10_mb_is_an_invalid_request(omni: TestClient) -> None:
    r = omni.post(CREATE, data={**FORM, "prompt": "x" * 10_000_001})
    assert (r.status_code, r.json()["error"]["code"]) == (422, "invalid_request")
    _nothing_left(omni)


def test_a_picture_over_10_mb_is_rejected(omni: TestClient) -> None:
    big = b"\xff\xd8\xff" + b"\x00" * 10_000_000
    r = omni.post(CREATE, data=FORM, files=_picture_files(big))
    assert (r.status_code, r.json()["error"]["code"]) == (413, "picture_too_large")
    _nothing_left(omni)


def test_more_than_seven_pictures_are_rejected(omni: TestClient, make_picture: MakePicture) -> None:
    data = make_picture("png").read_bytes()
    r = omni.post(CREATE, data=FORM, files=_picture_files(*[data] * 8))
    assert (r.status_code, r.json()["error"]["code"]) == (422, "too_many_pictures")
    _nothing_left(omni)


def test_the_picture_limit_follows_the_provider(tmp_path: Path, make_picture: MakePicture) -> None:
    data = make_picture("png").read_bytes()
    with _client(tmp_path, FakeVideoGenerator(model=FAKE_MODEL)) as client:  # the fake reports max_pictures=3
        assert client.post(CREATE, data=FORM, files=_picture_files(*[data] * 3)).status_code == 201
        r = client.post(CREATE, data=FORM, files=_picture_files(*[data] * 4))
        assert (r.status_code, r.json()["error"]["code"]) == (422, "too_many_pictures")
        assert _row_count(client) == 1


def test_an_unexpected_storage_failure_discards_the_job(omni: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_: Any, **__: Any) -> None:
        raise OSError("disk full: secret")

    monkeypatch.setattr(omni.app.state.job_access.ctx.media, "normalize_picture", boom)  # type: ignore[attr-defined]
    r = omni.post(CREATE, data=FORM, files=_picture_files(b"\xff\xd8\xff" + b"\x00" * 64))
    assert r.status_code == 500
    assert "secret" not in r.text
    _nothing_left(omni)


def _multipart_head() -> tuple[bytes, str]:
    boundary = "xBOUNDARYx"
    head = f'--{boundary}\r\nContent-Disposition: form-data; name="pictures"; filename="p.bin"\r\n\r\n'.encode()
    return head, f"multipart/form-data; boundary={boundary}"


def _asgi_post(
    client: TestClient, chunks: Iterator[bytes], content_type: str, content_length: int | None
) -> tuple[int, dict[str, Any]]:
    """Drive the app directly with a body that is produced lazily, so a test can see how much of it was read."""
    headers = [(b"content-type", content_type.encode()), (b"host", b"testserver")]
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode()))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": CREATE,
        "raw_path": CREATE.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("testclient", 5000),
        "server": ("testserver", 80),
    }
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        chunk = next(chunks, None)
        if chunk is None:
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.request", "body": chunk, "more_body": True}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    async def run() -> None:
        await client.app(scope, receive, send)  # type: ignore[arg-type]

    client.portal.call(run)  # type: ignore[union-attr]
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return int(start["status"]), json.loads(body)


def test_an_oversized_streamed_body_is_cut_off_before_it_is_read_to_the_end(omni: TestClient) -> None:
    head, content_type = _multipart_head()
    read = {"chunks": 0}

    def endless() -> Iterator[bytes]:
        yield head
        while True:  # a client that never stops sending
            read["chunks"] += 1
            yield b"x" * 1_000_000

    status, body = _asgi_post(omni, endless(), content_type, content_length=None)
    assert (status, body["error"]["code"]) == (413, "picture_too_large")
    assert read["chunks"] <= 72  # the 71 MB cap, plus the chunk that crossed it
    _nothing_left(omni)


def test_an_oversized_declared_length_is_refused_without_reading_the_body(omni: TestClient) -> None:
    head, content_type = _multipart_head()
    read = {"chunks": 0}

    def counted() -> Iterator[bytes]:
        read["chunks"] += 1
        yield head

    status, body = _asgi_post(omni, counted(), content_type, content_length=500_000_000)
    assert (status, body["error"]["code"]) == (413, "picture_too_large")
    assert read["chunks"] == 0
    _nothing_left(omni)


# --- options --------------------------------------------------------------------------------------------------

OPTIONS = "/api/tools/video_generation/options"


def test_options_describe_the_form_and_the_prices(omni: TestClient) -> None:
    body = omni.get(OPTIONS).json()
    assert body == {
        "durations_s": [5, 6, 7, 8, 9, 10],
        "default_duration_s": 8,
        "aspect_ratios": ["9:16", "16:9"],
        "resolutions": ["1080p", "720p"],
        "max_pictures": 7,
        "max_picture_bytes": 10_000_000,
        "picture_types": ["image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"],
        "max_prompt_chars": 2000,
        "price_per_second_usd": {"720p": pytest.approx(0.10136), "1080p": pytest.approx(0.15204)},
        "price_per_picture_usd": pytest.approx(0.00168),
    }


def test_options_follow_the_provider_capabilities(tmp_path: Path) -> None:
    caps = replace(CAPABILITIES, durations_s=[5, 6, 7], max_pictures=2)
    with _client(tmp_path, FakeVideoGenerator(model=FAKE_MODEL, caps=caps)) as client:
        body = client.get(OPTIONS).json()
    assert (body["durations_s"], body["default_duration_s"], body["max_pictures"]) == ([5, 6, 7], 7, 2)


def test_options_prices_are_null_for_an_unpriced_model(tmp_path: Path) -> None:
    with _client(tmp_path, FakeVideoGenerator(model="not-in-the-price-table", caps=OMNI_CAPS)) as client:
        body = client.get(OPTIONS).json()
    assert body["price_per_second_usd"] is None
    assert body["price_per_picture_usd"] is None


def test_options_prices_follow_the_pricing_overrides(tmp_path: Path) -> None:
    overrides = {FAKE_MODEL: {"video_output": 10.0, "input": 1.0}}
    with _client(tmp_path, FakeVideoGenerator(model=FAKE_MODEL, caps=OMNI_CAPS), pricing_overrides=overrides) as c:
        body = c.get(OPTIONS).json()
    assert body["price_per_second_usd"] == {"720p": pytest.approx(0.05792), "1080p": pytest.approx(0.08688)}
    assert body["price_per_picture_usd"] == pytest.approx(0.00112)


def test_the_routes_are_not_found_when_the_tool_is_disabled(tmp_path: Path) -> None:
    config = Settings(data_dir=tmp_path / "data", anthropic_api_key=SecretStr("sk-test"))
    providers = Providers(transcriber=TranscriberChain([FakeTranscriber()]), llm=LLMChain([FakeLLM([])]))
    with TestClient(create_app(config, providers, static_dir=tmp_path / "static")) as c:
        assert c.get(OPTIONS).status_code == 404
        assert c.get(_file_url("any-job", "clip")).status_code == 404
        listed = {tool["id"] for tool in c.get("/api/tools").json()}
        assert "video_generation" not in listed
        for response in (c.post(CREATE, data=FORM), c.put(CREATE), c.patch(CREATE), c.delete(CREATE), c.head(OPTIONS)):
            assert response.status_code == 404
        assert c.post("/api/health").status_code == 405  # a mounted path keeps its 405
        body = c.post(CREATE, data=FORM).json()
        assert body["error"]["code"] == "not_found"


# --- clip and thumbnail ---------------------------------------------------------------------------------------


def _file_url(job_id: str, name: str) -> str:
    return f"/api/tools/video_generation/jobs/{job_id}/{name}"


def _done(client: TestClient, **form: str) -> str:
    job_id = client.post(CREATE, data={**FORM, **form}).json()["job_id"]
    _wait(client, job_id, JobState.DONE)
    return str(job_id)


def test_the_clip_downloads_as_an_attachment_named_after_the_prompt(omni: TestClient) -> None:
    job_id = _done(omni)
    clip = (_store(omni).job_dir(job_id) / "clip.mp4").read_bytes()
    r = omni.get(_file_url(job_id, "clip"))
    assert r.status_code == 200
    assert r.headers["content-type"] == "video/mp4"
    assert r.headers["content-disposition"] == f'attachment; filename="{PROMPT}.mp4"'
    assert r.headers["accept-ranges"] == "bytes"
    assert r.content == clip
    head = omni.head(_file_url(job_id, "clip"))
    assert head.status_code == 200 and head.content == b""
    for name in ("content-type", "content-disposition", "content-length", "accept-ranges"):
        assert head.headers[name] == r.headers[name]


def test_the_clip_supports_byte_ranges(omni: TestClient) -> None:
    job_id = _done(omni)
    clip = (_store(omni).job_dir(job_id) / "clip.mp4").read_bytes()
    part = omni.get(_file_url(job_id, "clip"), headers={"Range": "bytes=10-99"})
    assert part.status_code == 206
    assert part.headers["content-range"] == f"bytes 10-99/{len(clip)}"
    assert part.content == clip[10:100]


def test_a_non_latin_prompt_downloads_with_a_clip_mp4_fallback_name(omni: TestClient) -> None:
    job_id = _done(omni, prompt="Поездка к морю")
    disposition = omni.get(_file_url(job_id, "clip")).headers["content-disposition"]
    assert disposition == (
        "attachment; filename=\"clip.mp4\"; filename*=UTF-8''"
        "%D0%9F%D0%BE%D0%B5%D0%B7%D0%B4%D0%BA%D0%B0%20%D0%BA%20%D0%BC%D0%BE%D1%80%D1%8E.mp4"
    )


def test_a_tampered_download_name_falls_back_to_clip_mp4(omni: TestClient) -> None:
    job_id = _done(omni)
    path = _store(omni).job_dir(job_id) / "result.json"
    result = json.loads(path.read_text(encoding="utf-8"))
    result["download_name"] = 'a"; filename="evil.exe'
    path.write_text(json.dumps(result), encoding="utf-8")
    assert omni.get(_file_url(job_id, "clip")).headers["content-disposition"] == 'attachment; filename="clip.mp4"'


def test_the_thumbnail_is_a_jpeg(omni: TestClient) -> None:
    job_id = _done(omni)
    r = omni.get(_file_url(job_id, "thumbnail"))
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert r.content == (_store(omni).job_dir(job_id) / "thumbnail.jpg").read_bytes()
    assert r.content[:3] == b"\xff\xd8\xff"


@pytest.mark.parametrize("name", ["clip", "thumbnail"])
@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_files_of_an_unknown_or_another_tools_job_are_not_found(omni: TestClient, name: str, method: str) -> None:
    other = omni.post("/api/tools/youtube_prep/jobs", json={"size": 100}).json()["job_id"]
    for job_id in ("no-such-job", other):
        r = omni.request(method, _file_url(job_id, name))
        assert r.status_code == 404
        if method == "GET":
            assert r.json()["error"]["code"] == "job_not_found"


@pytest.mark.parametrize("name", ["clip", "thumbnail"])
def test_files_of_an_unfinished_job_are_a_conflict(tmp_path: Path, name: str) -> None:
    fake = FakeVideoGenerator(model=FAKE_MODEL, caps=OMNI_CAPS, polls=["pending"] * 1000)
    with _client(tmp_path, fake) as client:
        job_id = client.post(CREATE, data=FORM).json()["job_id"]
        _wait(client, job_id, JobState.GENERATING)
        r = client.get(_file_url(job_id, name))
        assert (r.status_code, r.json()["error"]["code"]) == (409, "job_not_done")


def test_files_of_a_failed_job_are_a_conflict(tmp_path: Path) -> None:
    fake = FakeVideoGenerator(model=FAKE_MODEL, caps=OMNI_CAPS, polls=[VideoError("video_failed")])
    with _client(tmp_path, fake) as client:
        job_id = client.post(CREATE, data=FORM).json()["job_id"]
        _wait(client, job_id, JobState.FAILED)
        assert client.get(_file_url(job_id, "clip")).status_code == 409


def test_files_of_an_expired_job_are_not_found(tmp_path: Path) -> None:
    with _client(tmp_path, FakeVideoGenerator(model=FAKE_MODEL, caps=OMNI_CAPS), job_ttl_hours=1) as client:
        job_id = _done(client)
        _store(client)._clock = lambda: time.time() + 2 * 3600
        r = client.get(_file_url(job_id, "clip"))
        assert (r.status_code, r.json()["error"]["code"]) == (404, "job_not_found")


def test_a_purged_file_is_not_found(omni: TestClient) -> None:
    job_id = _done(omni)
    (_store(omni).job_dir(job_id) / "clip.mp4").unlink()
    assert omni.get(_file_url(job_id, "clip")).status_code == 404


@pytest.mark.parametrize("content", ["not json", "[1, 2]", '{"file": "../../secret", "thumbnail": ".hidden"}'])
def test_an_unreadable_or_unsafe_result_json_is_not_found(omni: TestClient, content: str) -> None:
    job_id = _done(omni)
    (_store(omni).job_dir(job_id) / "result.json").write_text(content, encoding="utf-8")
    assert omni.get(_file_url(job_id, "clip")).status_code == 404
    assert omni.get(_file_url(job_id, "thumbnail")).status_code == 404


# --- inputs and pictures (Try again) --------------------------------------------------------------------------


def _picture_url(job_id: str, n: int) -> str:
    return _file_url(job_id, f"pictures/{n}")


def _done_with_pictures(client: TestClient, make_picture: MakePicture, count: int = 2) -> str:
    pictures = [make_picture("png", 64 + n, 48).read_bytes() for n in range(count)]
    job_id = client.post(CREATE, data=FORM, files=_picture_files(*pictures)).json()["job_id"]
    _wait(client, job_id, JobState.DONE)
    return str(job_id)


def test_a_done_jobs_inputs_list_the_fields_and_the_picture_urls_in_order(
    omni: TestClient, make_picture: MakePicture
) -> None:
    job_id = _done_with_pictures(omni, make_picture)
    r = omni.get(_file_url(job_id, "inputs"))
    assert r.status_code == 200
    assert r.json() == {
        "prompt": PROMPT,
        "duration_s": 6,
        "aspect_ratio": "16:9",
        "resolution": "720p",
        "pictures": [_picture_url(job_id, 1), _picture_url(job_id, 2)],
    }


def test_inputs_without_pictures_have_an_empty_list(omni: TestClient) -> None:
    job_id = _done(omni)
    assert omni.get(_file_url(job_id, "inputs")).json()["pictures"] == []


def test_a_picture_is_the_normalized_jpeg(omni: TestClient, make_picture: MakePicture) -> None:
    job_id = _done_with_pictures(omni, make_picture)
    for n in (1, 2):
        r = omni.get(_picture_url(job_id, n))
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/jpeg"
        assert r.content == (_store(omni).job_dir(job_id) / "pictures" / f"{n}.jpg").read_bytes()
        assert r.content[:3] == b"\xff\xd8\xff"
        assert omni.head(_picture_url(job_id, n)).status_code == 200


def test_inputs_and_pictures_are_served_for_a_failed_job(tmp_path: Path, make_picture: MakePicture) -> None:
    fake = FakeVideoGenerator(model=FAKE_MODEL, caps=OMNI_CAPS, polls=[VideoError("video_failed")])
    with _client(tmp_path, fake) as client:
        job_id = _submit(client, [make_picture("png", 64, 48).read_bytes()])
        _wait(client, job_id, JobState.FAILED)
        r = client.get(_file_url(job_id, "inputs"))
        assert (r.status_code, r.json()["prompt"], r.json()["pictures"]) == (200, PROMPT, [_picture_url(job_id, 1)])
        assert client.get(_picture_url(job_id, 1)).status_code == 200


def test_inputs_are_served_while_the_job_is_still_running(tmp_path: Path) -> None:
    fake = FakeVideoGenerator(model=FAKE_MODEL, caps=OMNI_CAPS, polls=["pending"] * 1000)
    with _client(tmp_path, fake) as client:
        job_id = _submit(client)
        _wait(client, job_id, JobState.GENERATING)
        assert client.get(_file_url(job_id, "inputs")).json()["prompt"] == PROMPT


def test_a_cancelled_jobs_inputs_are_not_found_once_its_data_is_deleted(
    tmp_path: Path, make_picture: MakePicture
) -> None:
    fake = FakeVideoGenerator(model=FAKE_MODEL, submits=["op-9"], polls=["pending"] * 1000)
    with _client(tmp_path, fake) as client:
        job_id = _submit(client, [make_picture("png", 64, 48).read_bytes()])
        _wait(client, job_id, JobState.GENERATING)
        deadline = time.monotonic() + 10
        while not fake.poll_refs:
            assert time.monotonic() < deadline
            time.sleep(0.005)
        assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 202
        _wait(client, job_id, JobState.CANCELLED)
        job_dir = _store(client).job_dir(job_id)
        deadline = time.monotonic() + 10
        while job_dir.exists():
            assert time.monotonic() < deadline
            time.sleep(0.01)
        for url in (_file_url(job_id, "inputs"), _picture_url(job_id, 1)):
            r = client.get(url)
            assert (r.status_code, r.json()["error"]["code"]) == (404, "job_not_found")


def test_inputs_and_pictures_of_an_unknown_or_another_tools_job_are_not_found(omni: TestClient) -> None:
    other = omni.post("/api/tools/youtube_prep/jobs", json={"size": 100}).json()["job_id"]
    for job_id in ("no-such-job", other):
        for url in (_file_url(job_id, "inputs"), _picture_url(job_id, 1)):
            r = omni.get(url)
            assert (r.status_code, r.json()["error"]["code"]) == (404, "job_not_found")


def test_inputs_and_pictures_of_an_expired_job_are_not_found(tmp_path: Path, make_picture: MakePicture) -> None:
    with _client(tmp_path, FakeVideoGenerator(model=FAKE_MODEL, caps=OMNI_CAPS), job_ttl_hours=1) as client:
        job_id = _done_with_pictures(client, make_picture, 1)
        _store(client)._clock = lambda: time.time() + 2 * 3600
        for url in (_file_url(job_id, "inputs"), _picture_url(job_id, 1)):
            r = client.get(url)
            assert (r.status_code, r.json()["error"]["code"]) == (404, "job_not_found")


@pytest.mark.parametrize("n", ["0", "3", "99999999999999999999", "-1", "01", "1.5", "abc", "%20", "٢", "1.jpg"])
def test_a_picture_out_of_range_or_not_a_positive_integer_is_not_found(
    omni: TestClient, make_picture: MakePicture, n: str
) -> None:
    job_id = _done_with_pictures(omni, make_picture)
    r = omni.get(_file_url(job_id, f"pictures/{n}"))
    assert (r.status_code, r.json()["error"]["code"]) == (404, "job_not_found")


def test_a_picture_number_never_reaches_outside_the_job_directory(omni: TestClient, make_picture: MakePicture) -> None:
    job_id = _done_with_pictures(omni, make_picture, 1)
    (_store(omni).job_dir(job_id) / "secret.jpg").write_bytes(b"\xff\xd8\xff secret")
    for n in ("..%2Fsecret", f"..%2F..%2Fjobs%2F{job_id}%2Fsecret", "1%2F..%2Fsecret"):
        r = omni.get(_file_url(job_id, f"pictures/{n}"))
        assert r.status_code == 404 and b"secret" not in r.content


@pytest.mark.parametrize("content", ["not json", "[1, 2]", '{"prompt": "x"}', None])
def test_unreadable_inputs_are_not_found(omni: TestClient, make_picture: MakePicture, content: str | None) -> None:
    job_id = _done_with_pictures(omni, make_picture, 1)
    path = _store(omni).job_dir(job_id) / "inputs.json"
    if content is None:
        path.unlink()
    else:
        path.write_text(content, encoding="utf-8")
    r = omni.get(_file_url(job_id, "inputs"))
    assert (r.status_code, r.json()["error"]["code"]) == (404, "job_not_found")
