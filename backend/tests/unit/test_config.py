from pathlib import Path

import pytest

from videokit.core import config
from videokit.core.config import Settings, validate


@pytest.fixture
def ok_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret-value")


def test_defaults() -> None:
    s = Settings()
    assert s.data_dir == Path("/data")
    assert s.port == 8000
    assert s.max_upload_bytes == 10 * 1024**3
    assert s.job_ttl_hours == 24
    assert s.max_frames == 80
    assert s.frame_width == 768
    assert s.transcribe_providers == ["local"]
    assert s.whisper_model == "large-v3-turbo"
    assert s.whisper_device == "auto"
    assert s.openai_transcribe_model == "whisper-1"
    assert s.llm_providers == ["anthropic"]
    assert s.anthropic_api_key is None
    assert s.anthropic_model == "claude-opus-5-5"
    assert s.anthropic_effort == "medium"
    assert s.openai_api_key is None
    assert s.openai_model is None
    assert s.pricing_overrides == {}
    assert s.log_level == "INFO"
    assert s.video_providers == []
    assert s.gemini_api_key is None
    assert s.omni_model == "gemini-omni-1.1-flash"


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_UPLOAD_GB", "2")
    monkeypatch.setenv("TRANSCRIBE_PROVIDERS", "openai, local")
    monkeypatch.setenv("LLM_PROVIDERS", "openai,anthropic")
    monkeypatch.setenv("PRICING_OVERRIDES", '{"m": {"input": 4.0, "output": 20.0}}')
    s = Settings()
    assert s.max_upload_bytes == 2 * 1024**3
    assert s.transcribe_providers == ["openai", "local"]
    assert s.llm_providers == ["openai", "anthropic"]
    assert s.pricing_overrides == {"m": {"input": 4.0, "output": 20.0}}


def test_video_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIDEO_PROVIDERS", " omni ")
    monkeypatch.setenv("GEMINI_API_KEY", "gm-secret-value")
    monkeypatch.setenv("OMNI_MODEL", "gemini-omni-next")
    s = Settings()
    assert s.video_providers == ["omni"]
    assert s.gemini_api_key is not None
    assert s.gemini_api_key.get_secret_value() == "gm-secret-value"
    assert s.omni_model == "gemini-omni-next"
    assert "gm-secret-value" not in repr(s)


def test_valid_settings_have_no_problems(ok_env: None) -> None:
    assert validate(Settings()) == []


def test_unknown_providers(ok_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRANSCRIBE_PROVIDERS", "local,bogus")
    monkeypatch.setenv("LLM_PROVIDERS", "anthropic,nope")
    problems = validate(Settings())
    assert len(problems) == 2
    assert any("TRANSCRIBE_PROVIDERS" in p for p in problems)
    assert any("LLM_PROVIDERS" in p for p in problems)


def test_video_is_optional_and_empty_means_no_problem(ok_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIDEO_PROVIDERS", "")
    assert Settings().video_providers == []
    assert validate(Settings()) == []


def test_unknown_video_provider(ok_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIDEO_PROVIDERS", "bogus")
    assert validate(Settings()) == ["VIDEO_PROVIDERS contains an unknown provider (allowed: omni)"]


def test_omni_requires_the_gemini_key(ok_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIDEO_PROVIDERS", "omni")
    assert validate(Settings()) == ["GEMINI_API_KEY is required when 'omni' is listed in VIDEO_PROVIDERS"]
    monkeypatch.setenv("GEMINI_API_KEY", "   ")
    assert len(validate(Settings())) == 1
    monkeypatch.setenv("GEMINI_API_KEY", "gm-secret-value")
    assert validate(Settings()) == []


def test_gemini_key_is_not_required_when_omni_is_not_listed(ok_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "")
    assert validate(Settings()) == []


def test_missing_anthropic_key(ok_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert [p for p in validate(Settings()) if "ANTHROPIC_API_KEY" in p]


def test_openai_requires_key_and_model(ok_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDERS", "anthropic,openai")
    problems = validate(Settings())
    assert any("OPENAI_API_KEY" in p for p in problems)
    assert any("OPENAI_MODEL" in p for p in problems)


def test_openai_transcription_needs_key_but_not_llm_model(ok_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRANSCRIBE_PROVIDERS", "openai")
    problems = validate(Settings())
    assert any("OPENAI_API_KEY" in p for p in problems)
    assert not any("OPENAI_MODEL" in p for p in problems)


@pytest.mark.parametrize("value", ["0", "101", "-3"])
def test_max_frames_out_of_range(ok_env: None, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("MAX_FRAMES", value)
    assert [p for p in validate(Settings()) if "MAX_FRAMES" in p]


@pytest.mark.parametrize("value", ["1", "100"])
def test_max_frames_bounds_ok(ok_env: None, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("MAX_FRAMES", value)
    assert validate(Settings()) == []


def test_cuda_without_gpu(ok_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WHISPER_DEVICE", "cuda")
    monkeypatch.setattr(config, "cuda_available", lambda: False)
    assert [p for p in validate(Settings()) if "WHISPER_DEVICE" in p]


def test_cuda_with_gpu(ok_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WHISPER_DEVICE", "cuda")
    monkeypatch.setattr(config, "cuda_available", lambda: True)
    assert validate(Settings()) == []


@pytest.mark.parametrize(("var", "value"), [("ANTHROPIC_EFFORT", "max"), ("WHISPER_DEVICE", "gpu")])
def test_unknown_enum_values_are_reported(ok_env: None, monkeypatch: pytest.MonkeyPatch, var: str, value: str) -> None:
    monkeypatch.setenv(var, value)
    monkeypatch.setattr(config, "cuda_available", lambda: False)
    problems = validate(Settings())
    assert len(problems) == 1
    assert problems[0].startswith(var)
    assert "\n" not in problems[0]


@pytest.mark.parametrize(
    ("var", "value"),
    [
        ("ANTHROPIC_EFFORT", "low"),
        ("ANTHROPIC_EFFORT", "medium"),
        ("ANTHROPIC_EFFORT", "high"),
        ("WHISPER_DEVICE", "auto"),
        ("WHISPER_DEVICE", "cpu"),
    ],
)
def test_allowed_enum_values_pass(ok_env: None, monkeypatch: pytest.MonkeyPatch, var: str, value: str) -> None:
    monkeypatch.setenv(var, value)
    assert validate(Settings()) == []


def test_cuda_not_checked_for_other_devices(ok_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> bool:
        raise AssertionError("must not be called")

    monkeypatch.setattr(config, "cuda_available", boom)
    assert validate(Settings()) == []


def test_cuda_available_handles_missing_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ctranslate2.get_cuda_device_count", lambda: 1)
    assert config.cuda_available() is True

    def fail() -> int:
        raise RuntimeError("no driver")

    monkeypatch.setattr("ctranslate2.get_cuda_device_count", fail)
    assert config.cuda_available() is False


def test_data_dir_not_writable(ok_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setenv("DATA_DIR", str(blocker / "sub"))
    assert [p for p in validate(Settings()) if "DATA_DIR" in p]


def test_messages_never_contain_secret_values(ok_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRANSCRIBE_PROVIDERS", "bogus")
    monkeypatch.setenv("MAX_FRAMES", "999")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-other-secret")
    monkeypatch.setenv("LLM_PROVIDERS", "openai")
    monkeypatch.setenv("VIDEO_PROVIDERS", "omni,bogus-video")
    monkeypatch.setenv("GEMINI_API_KEY", "gm-secret-value")
    text = "\n".join(validate(Settings()))
    assert "gm-secret-value" not in text
    assert "bogus-video" not in text
    assert "sk-secret-value" not in text
    assert "sk-other-secret" not in text
    assert "bogus" not in text
    assert "999" not in text


@pytest.mark.parametrize(
    ("var", "value"),
    [
        ("MAX_FRAMES", "abc-secret"),
        ("PORT", "x-secret"),
        ("MAX_UPLOAD_GB", "lots-secret"),
        ("PRICING_OVERRIDES", "{not json secret"),
        ("PRICING_OVERRIDES", '{"m": "secret-string"}'),
    ],
)
def test_load_settings_reports_malformed_values_as_single_lines(
    monkeypatch: pytest.MonkeyPatch, var: str, value: str
) -> None:
    monkeypatch.setenv(var, value)
    with pytest.raises(config.ConfigurationError) as exc:
        config.load_settings()
    problems = exc.value.problems
    assert len(problems) == 1
    assert var in problems[0]
    assert "\n" not in problems[0]
    assert "secret" not in problems[0]


def test_load_settings_reports_each_bad_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_FRAMES", "abc")
    monkeypatch.setenv("PORT", "x")
    with pytest.raises(config.ConfigurationError) as exc:
        config.load_settings()
    assert sorted(p.split()[0] for p in exc.value.problems) == ["MAX_FRAMES", "PORT"]


@pytest.mark.parametrize("var", ["MAX_FRAMES", "PRICING_OVERRIDES", "PORT", "OPENAI_MODEL"])
def test_empty_values_fall_back_to_defaults(monkeypatch: pytest.MonkeyPatch, var: str) -> None:
    monkeypatch.setenv(var, "")
    s = config.load_settings()
    assert s.max_frames == 80
    assert s.pricing_overrides == {}
    assert s.port == 8000
    assert s.openai_model is None


def test_load_settings_success(ok_env: None) -> None:
    assert config.load_settings().max_frames == 80


def test_polluted_host_environment_does_not_leak_into_tests() -> None:
    # The autouse conftest fixture removes every Settings variable, so the host shell cannot change results.
    s = Settings()
    assert s.openai_api_key is None
    assert s.anthropic_api_key is None
    assert s.max_frames == 80
    assert s.transcribe_providers == ["local"]
    assert s.llm_providers == ["anthropic"]
    assert s.openai_transcribe_model == "whisper-1"
    assert s.video_providers == []
    assert s.gemini_api_key is None
    assert s.omni_model == "gemini-omni-1.1-flash"
