"""`.env.example` and the README document every setting, with placeholders only."""

import re
from pathlib import Path

from videokit.core.config import Settings

ROOT = Path(__file__).resolve().parents[3]
VIDEO_VARIABLES = ("VIDEO_PROVIDERS", "GEMINI_API_KEY", "OMNI_MODEL")


def _example_values() -> dict[str, str]:
    """Variable -> value for every `NAME=value` line, commented-out examples included."""
    text = (ROOT / ".env.example").read_text()
    return dict(re.findall(r"^#?\s*([A-Z][A-Z0-9_]*)=(.*)$", text, flags=re.MULTILINE))


def test_env_example_lists_every_setting() -> None:
    documented = set(_example_values())
    missing = {name.upper() for name in Settings.model_fields} - documented
    assert not missing, f".env.example lacks: {sorted(missing)}"


def test_env_example_keys_are_placeholders() -> None:
    secrets = {name: value for name, value in _example_values().items() if name.endswith("_API_KEY")}
    assert "GEMINI_API_KEY" in secrets
    assert all(re.fullmatch(r"<[a-z-]+>", value) for value in secrets.values()), secrets


def test_readme_documents_the_video_generation_settings() -> None:
    readme = (ROOT / "README.md").read_text()
    for name in VIDEO_VARIABLES:
        assert f"`{name}`" in readme, name
    assert "POST /api/tools/<id>/jobs" in readme


def test_docs_describe_remote_transcription_only() -> None:
    values = _example_values()
    assert values["TRANSCRIBE_PROVIDERS"] == "openai"
    assert not [name for name in values if name.startswith("WHISPER_")]
    assert re.search(r"^#\s*OPENAI_TRANSCRIBE_URL=", (ROOT / ".env.example").read_text(), re.MULTILINE)
    readme = (ROOT / "README.md").read_text()
    for name in ("OPENAI_TRANSCRIBE_URL", "OPENAI_TRANSCRIBE_MODEL", "OPENAI_TRANSCRIBE_API_KEY"):
        assert f"`{name}`" in readme, name
    assert "--add-host" in readme
    assert "http://host.docker.internal:8000/v1" in readme
    # The upgrade section legitimately names the removed variables.
    current = readme.split("\n## Upgrading from 0.3\n")[0] + readme.split("\n## Cost\n", 1)[1]
    for text in (current, (ROOT / ".claude" / "CLAUDE.md").read_text()):
        assert not re.search(r"WHISPER_|faster-whisper(?!-large)|cuBLAS|gpu-check|HF_HOME", text)
