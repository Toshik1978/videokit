"""Environment-based configuration and startup validation (see contracts/configuration.md)."""

import os
import re
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlparse

from pydantic import SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from pydantic_settings.exceptions import SettingsError

TRANSCRIBE_PROVIDER_NAMES = ("openai",)
LLM_PROVIDER_NAMES = ("anthropic", "openai")
VIDEO_PROVIDER_NAMES = ("omni",)
MAX_FRAMES_RANGE = (1, 100)
ANTHROPIC_EFFORTS = ("low", "medium", "high")


class Settings(BaseSettings):
    """All configuration; read from environment variables only."""

    model_config = SettingsConfigDict(env_ignore_empty=True)

    data_dir: Path = Path("/data")
    port: int = 8000
    max_upload_gb: float = 10
    job_ttl_hours: int = 24
    max_frames: int = 80
    frame_width: int = 768
    transcribe_providers: Annotated[list[str], NoDecode] = []
    openai_transcribe_model: str = "whisper-1"
    openai_transcribe_url: str | None = None
    openai_transcribe_api_key: SecretStr | None = None
    llm_providers: Annotated[list[str], NoDecode] = ["anthropic"]
    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = "claude-opus-5-5"
    anthropic_effort: str = "medium"
    openai_api_key: SecretStr | None = None
    openai_model: str | None = None
    video_providers: Annotated[list[str], NoDecode] = []
    gemini_api_key: SecretStr | None = None
    omni_model: str = "gemini-omni-1.1-flash"
    pricing_overrides: dict[str, dict[str, float]] = {}
    log_level: str = "INFO"

    @field_validator("transcribe_providers", "llm_providers", "video_providers", mode="before")
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


OPENAI_API_HOST = "api.openai.com"


def _host(url: str) -> str | None:
    """The lower-case host of ``url``, or ``None`` when it has none or cannot be parsed."""
    try:
        return urlparse(url).hostname or None
    except ValueError:  # malformed address: reported by validate(), never a crash
        return None


def is_openai_api(url: str | None) -> bool:
    """True when the transcription address is OpenAI's own API: empty, or `https://` with host `api.openai.com`.

    Plain `http://api.openai.com` is not OpenAI's API (the key is never sent over HTTP); validate() refuses it.
    """
    if not url or not url.strip():
        return True
    text = url.strip()
    return text.lower().startswith("https://") and _host(text) == OPENAI_API_HOST


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
            continue
        if var == "TRANSCRIBE_PROVIDERS" and "local" in names:  # removed in 0.4.0: say what to change
            problems.append(
                "TRANSCRIBE_PROVIDERS lists 'local', which was removed in 0.4.0 "
                "(use openai, with OPENAI_TRANSCRIBE_URL for a self-hosted server)"
            )
            names = [name for name in names if name != "local"]
        if any(name not in allowed for name in names):
            problems.append(f"{var} contains an unknown provider (allowed: {', '.join(allowed)})")

    if any(name not in VIDEO_PROVIDER_NAMES for name in settings.video_providers):  # empty is fine: tool hidden
        problems.append(f"VIDEO_PROVIDERS contains an unknown provider (allowed: {', '.join(VIDEO_PROVIDER_NAMES)})")

    if "anthropic" in settings.llm_providers and not _has(settings.anthropic_api_key):
        problems.append("ANTHROPIC_API_KEY is required when 'anthropic' is listed in LLM_PROVIDERS")
    if "openai" in settings.llm_providers and not _has(settings.openai_api_key):
        problems.append("OPENAI_API_KEY is required when 'openai' is listed in LLM_PROVIDERS")
    url = (settings.openai_transcribe_url or "").strip()
    if url and not url.lower().startswith(("http://", "https://")):
        problems.append("OPENAI_TRANSCRIBE_URL must start with http:// or https://")
    elif url and _host(url) is None:
        problems.append("OPENAI_TRANSCRIBE_URL must name a host, e.g. http://whisper.example.com:8000/v1")
    elif url and _host(url) == OPENAI_API_HOST and not is_openai_api(url):
        problems.append("OPENAI_TRANSCRIBE_URL must use https:// for OpenAI's API")
    if (
        "openai" in settings.transcribe_providers
        and is_openai_api(url)
        and not (_has(settings.openai_api_key) or _has(settings.openai_transcribe_api_key))
    ):
        problems.append(
            "OPENAI_API_KEY or OPENAI_TRANSCRIBE_API_KEY is required when TRANSCRIBE_PROVIDERS lists 'openai' "
            "without a self-hosted OPENAI_TRANSCRIBE_URL"
        )
    if "omni" in settings.video_providers and not _has(settings.gemini_api_key):
        problems.append("GEMINI_API_KEY is required when 'omni' is listed in VIDEO_PROVIDERS")
    if "openai" in settings.llm_providers and not (settings.openai_model or "").strip():
        problems.append("OPENAI_MODEL is required when 'openai' is listed in LLM_PROVIDERS")

    if settings.anthropic_effort not in ANTHROPIC_EFFORTS:
        problems.append(f"ANTHROPIC_EFFORT must be one of: {', '.join(ANTHROPIC_EFFORTS)}")

    low, high = MAX_FRAMES_RANGE
    if not low <= settings.max_frames <= high:
        problems.append(f"MAX_FRAMES must be between {low} and {high}")

    if not _data_dir_writable(settings.data_dir):
        problems.append("DATA_DIR is not writable")

    return problems
