"""The OpenAPI document carries the display name and no fake release number."""

from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import SecretStr

from fakes_llm import FakeLLM
from fakes_transcribe import FakeTranscriber
from videokit.core.config import Settings
from videokit.core.llm import LLMChain
from videokit.core.transcribe import TranscriberChain
from videokit.core.worker import Providers
from videokit.main import create_app


def test_openapi_title_and_neutral_version(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", anthropic_api_key=SecretStr("k"))
    providers = Providers(
        transcriber=TranscriberChain([FakeTranscriber(name="local", segments=[])]),
        llm=LLMChain([FakeLLM([])]),
    )
    with TestClient(create_app(settings, providers, static_dir=tmp_path / "static")) as client:
        info = client.get("/api/openapi.json").json()["info"]
    assert info["title"] == "VideoKit"
    assert info["version"] == "0.0.0"
    assert info["version"] != "0.1.0"
