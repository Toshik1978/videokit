"""Purge of Video Generation jobs (SC-007): a finished job's files go with its row at the TTL."""

import io
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from fakes_llm import FakeLLM
from fakes_transcribe import FakeTranscriber
from fakes_videogen import FakeVideoGenerator
from videokit.core.config import Settings
from videokit.core.jobs import JobState
from videokit.core.llm import LLMChain
from videokit.core.purge import purge_expired
from videokit.core.transcribe import TranscriberChain
from videokit.core.videogen import VideoChain
from videokit.core.worker import Providers
from videokit.main import create_app
from videokit.tools.video_generation import pipeline
from videokit.tools.video_generation.inputs import CreationInputs, store_inputs

JOB_FILES = ["inputs.json", "pictures", "submit.json", "operation.json", "clip.mp4", "thumbnail.jpg", "result.json"]


def test_a_video_job_ttl_purge_removes_every_file_with_the_row(
    tmp_path: Path, make_picture: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pipeline, "POLL_INTERVAL_S", 0.01)
    settings = Settings(
        data_dir=tmp_path / "data",
        anthropic_api_key=SecretStr("sk-test"),
        gemini_api_key=SecretStr("g-test"),
        video_providers=["omni"],
    )
    providers = Providers(
        transcriber=TranscriberChain([FakeTranscriber()]),
        llm=LLMChain([FakeLLM([])]),
        video=VideoChain([FakeVideoGenerator(model="gemini-omni-1.1-flash")]),
    )
    picture = make_picture("png", 64, 48).read_bytes()
    with TestClient(create_app(settings, providers, static_dir=tmp_path / "static")) as client:
        access = client.app.state.job_access  # type: ignore[attr-defined]
        store = client.app.state.store  # type: ignore[attr-defined]
        job = access.create("video_generation")
        fields = CreationInputs("A fox in the snow", 5, "9:16", "1080p")
        store_inputs(access.job_dir(job.id), access.ctx.media, fields, [io.BytesIO(picture)])
        client.portal.call(access.complete, job.id)  # type: ignore[union-attr]
        deadline = time.monotonic() + 15
        while store.get(job.id).state is not JobState.DONE:
            assert store.get(job.id).state is not JobState.FAILED
            assert time.monotonic() < deadline, "the job did not finish"
            time.sleep(0.02)
        job_dir = store.job_dir(job.id)
        assert all((job_dir / name).exists() for name in JOB_FILES)
        assert (job_dir / "pictures" / "1.jpg").is_file()

        assert purge_expired(store, store.get(job.id).expires_at - 1) == 0  # still within its TTL
        assert job_dir.exists()

        assert purge_expired(store, store.get(job.id).expires_at + 1) == 1
        assert not job_dir.exists()  # every file listed above went with the directory ...
        assert store.get(job.id) is None  # ... and the row
