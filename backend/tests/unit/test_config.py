from pathlib import Path

import pytest

from videokit.core import config
from videokit.core.config import Settings, validate


@pytest.fixture
def ok_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret-value")
    monkeypatch.setenv("TRANSCRIBE_PROVIDERS", "openai")
    monkeypatch.setenv("OPENAI_TRANSCRIBE_URL", "http://stt.example.com/v1")


def test_defaults() -> None:
    s = Settings()
    assert s.data_dir == Path("/data")
    assert s.port == 8000
    assert s.max_upload_bytes == 10 * 1024**3
    assert s.job_ttl_hours == 24
    assert s.max_frames == 80
    assert s.frame_width == 768
    assert s.transcribe_providers == []
    assert not hasattr(s, "whisper_model") and not hasattr(s, "whisper_device")
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
    monkeypatch.setenv("TRANSCRIBE_PROVIDERS", "openai")
    monkeypatch.setenv("LLM_PROVIDERS", "openai,anthropic")
    monkeypatch.setenv("PRICING_OVERRIDES", '{"m": {"input": 4.0, "output": 20.0}}')
    s = Settings()
    assert s.max_upload_bytes == 2 * 1024**3
    assert s.transcribe_providers == ["openai"]
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
    monkeypatch.delenv("OPENAI_TRANSCRIBE_URL")
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


def test_default_transcribe_providers_are_refused(ok_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRANSCRIBE_PROVIDERS")
    assert [p for p in validate(Settings()) if "TRANSCRIBE_PROVIDERS must list at least one" in p]


def test_local_is_an_unknown_transcription_provider(ok_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRANSCRIBE_PROVIDERS", "local")
    problems = [p for p in validate(Settings()) if "TRANSCRIBE_PROVIDERS" in p]
    assert problems == ["TRANSCRIBE_PROVIDERS contains an unknown provider (allowed: openai)"]


def test_whisper_settings_and_cuda_probe_are_gone() -> None:
    assert not hasattr(config, "cuda_available")
    assert not hasattr(config, "WHISPER_DEVICES")


def test_unknown_effort_is_reported(ok_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_EFFORT", "max")
    problems = [p for p in validate(Settings()) if "TRANSCRIBE_PROVIDERS" not in p]
    assert len(problems) == 1
    assert problems[0].startswith("ANTHROPIC_EFFORT")
    assert "\n" not in problems[0]


@pytest.mark.parametrize("value", ["low", "medium", "high"])
def test_allowed_effort_values_pass(ok_env: None, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("ANTHROPIC_EFFORT", value)
    assert [p for p in validate(Settings()) if "ANTHROPIC_EFFORT" in p] == []


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
    assert s.transcribe_providers == []
    assert s.llm_providers == ["anthropic"]
    assert s.openai_transcribe_model == "whisper-1"
    assert s.video_providers == []
    assert s.gemini_api_key is None
    assert s.omni_model == "gemini-omni-1.1-flash"


def test_transcribe_settings_default_to_none() -> None:
    s = Settings()
    assert s.openai_transcribe_url is None
    assert s.openai_transcribe_api_key is None


def test_is_openai_api() -> None:
    assert config.is_openai_api(None)
    assert config.is_openai_api("")
    assert config.is_openai_api("https://api.openai.com/v1")
    assert not config.is_openai_api("http://whisper.example.com:8000/v1")
    assert not config.is_openai_api("https://api.openai.com.example.com/v1")


def test_transcribe_url_must_be_http(ok_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_TRANSCRIBE_URL", "ftp://secret-host.example.com")
    problems = validate(Settings())
    assert "OPENAI_TRANSCRIBE_URL must start with http:// or https://" in problems
    assert not any("secret-host" in p for p in problems)


def test_self_hosted_transcription_needs_no_key(ok_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRANSCRIBE_PROVIDERS", "openai")
    monkeypatch.setenv("OPENAI_TRANSCRIBE_URL", "http://whisper.example.com:8000/v1")
    assert not any("OPENAI" in p for p in validate(Settings()))


def test_openai_api_address_needs_a_key(ok_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRANSCRIBE_PROVIDERS", "openai")
    monkeypatch.setenv("OPENAI_TRANSCRIBE_URL", "https://api.openai.com/v1")
    expected = (
        "OPENAI_API_KEY or OPENAI_TRANSCRIBE_API_KEY is required when TRANSCRIBE_PROVIDERS lists 'openai' "
        "without a self-hosted OPENAI_TRANSCRIBE_URL"
    )
    assert expected in validate(Settings())
    monkeypatch.setenv("OPENAI_TRANSCRIBE_API_KEY", "sk-tr-secret")
    problems = validate(Settings())
    assert not any("OPENAI" in p for p in problems)
    assert not any("sk-tr-secret" in p for p in problems)


def test_either_key_satisfies_openai_transcription(ok_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRANSCRIBE_PROVIDERS", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-llm-secret")
    assert not any("OPENAI" in p for p in validate(Settings()))


def test_llm_openai_key_message_is_separate(ok_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDERS", "openai")
    monkeypatch.setenv("OPENAI_MODEL", "m")
    monkeypatch.setenv("OPENAI_TRANSCRIBE_API_KEY", "sk-tr-secret")
    assert "OPENAI_API_KEY is required when 'openai' is listed in LLM_PROVIDERS" in validate(Settings())


def test_malformed_transcribe_url_is_a_config_error_not_a_crash(ok_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    assert not config.is_openai_api("http://[bad")
    monkeypatch.setenv("TRANSCRIBE_PROVIDERS", "openai")
    monkeypatch.setenv("OPENAI_TRANSCRIBE_URL", "http://[bad")
    assert validate(Settings()) == []
