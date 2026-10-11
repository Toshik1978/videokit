"""Environment-based configuration and startup validation (see contracts/configuration.md)."""

import importlib
import os
import re
from pathlib import Path
from typing import Annotated, Any

from pydantic import SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from pydantic_settings.exceptions import SettingsError

TRANSCRIBE_PROVIDER_NAMES = ("local", "openai")
LLM_PROVIDER_NAMES = ("anthropic", "openai")
MAX_FRAMES_RANGE = (1, 100)


class Settings(BaseSettings):
    """All configuration; read from environment variables only."""

    model_config = SettingsConfigDict(env_ignore_empty=True)

    data_dir: Path = Path("/data")
    port: int = 8000
    max_upload_gb: float = 10
    job_ttl_hours: int = 24
    max_frames: int = 80
    frame_width: int = 768
    transcribe_providers: Annotated[list[str], NoDecode] = ["local"]
    whisper_model: str = "large-v3-turbo"
    whisper_device: str = "auto"
    openai_transcribe_model: str = "whisper-1"
    llm_providers: Annotated[list[str], NoDecode] = ["anthropic"]
    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = "claude-opus-5-5"
    anthropic_effort: str = "medium"
    openai_api_key: SecretStr | None = None
    openai_model: str | None = None
    pricing_overrides: dict[str, dict[str, float]] = {}
    log_level: str = "INFO"

    @field_validator("transcribe_providers", "llm_providers", mode="before")
    @classmethod
    def _split_csv(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @property
    def max_upload_bytes(self) -> int:
        return int(self.max_upload_gb * 1024**3)


class ConfigurationError(Exception):
    """Settings could not be parsed; `problems` has one single-line message per variable."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


def load_settings() -> Settings:
    """Build `Settings` from the environment, turning parse errors into one safe line per variable."""
    try:
        return Settings()
    except ValidationError as exc:
        fields = dict.fromkeys(str(err["loc"][0]) for err in exc.errors() if err["loc"])
    except SettingsError as exc:
        match = re.search(r'field "([^"]+)"', str(exc))
        fields = dict.fromkeys([match.group(1)] if match else ["(unknown)"])
    raise ConfigurationError([f"{name.upper()} has an invalid value" for name in fields])


def cuda_available() -> bool:
    """True when ctranslate2 (used by faster-whisper) sees at least one CUDA device."""
    try:
        ctranslate2 = importlib.import_module("ctranslate2")  # untyped third-party package
        return bool(ctranslate2.get_cuda_device_count() > 0)
    except Exception:
        return False


def _has(secret: SecretStr | None) -> bool:
    return secret is not None and bool(secret.get_secret_value().strip())


def _data_dir_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    return os.access(path, os.W_OK | os.X_OK)


def validate(settings: Settings) -> list[str]:
    """Return one message per configuration problem. Messages name variables, never their values."""
    problems: list[str] = []

    for var, names, allowed in (
        ("TRANSCRIBE_PROVIDERS", settings.transcribe_providers, TRANSCRIBE_PROVIDER_NAMES),
        ("LLM_PROVIDERS", settings.llm_providers, LLM_PROVIDER_NAMES),
    ):
        if not names:
            problems.append(f"{var} must list at least one provider")
        elif any(name not in allowed for name in names):
            problems.append(f"{var} contains an unknown provider (allowed: {', '.join(allowed)})")

    if "anthropic" in settings.llm_providers and not _has(settings.anthropic_api_key):
        problems.append("ANTHROPIC_API_KEY is required when 'anthropic' is listed in LLM_PROVIDERS")
    if ("openai" in settings.llm_providers or "openai" in settings.transcribe_providers) and not _has(
        settings.openai_api_key
    ):
        problems.append("OPENAI_API_KEY is required when 'openai' is listed in a providers variable")
    if "openai" in settings.llm_providers and not (settings.openai_model or "").strip():
        problems.append("OPENAI_MODEL is required when 'openai' is listed in LLM_PROVIDERS")

    if settings.whisper_device == "cuda" and not cuda_available():
        problems.append("WHISPER_DEVICE is 'cuda' but no usable GPU was found")

    low, high = MAX_FRAMES_RANGE
    if not low <= settings.max_frames <= high:
        problems.append(f"MAX_FRAMES must be between {low} and {high}")

    if not _data_dir_writable(settings.data_dir):
        problems.append("DATA_DIR is not writable")

    return problems
