"""The README documents the upgrade from 0.3.x and the release guide points at it."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _section() -> str:
    readme = (ROOT / "README.md").read_text()
    assert "\n## Upgrading from 0.3\n" in readme
    return readme.split("\n## Upgrading from 0.3\n", 1)[1].split("\n## ", 1)[0]


def test_upgrade_section_lists_the_migration_steps() -> None:
    section = _section()
    for needle in (
        "TRANSCRIBE_PROVIDERS=openai",
        "OPENAI_TRANSCRIBE_URL",
        "OPENAI_TRANSCRIBE_MODEL",
        "OPENAI_API_KEY",
        "WHISPER_MODEL",
        "WHISPER_DEVICE",
        "--gpus",
        "<data>/models",
        "<data>/huggingface",
        "TRANSCRIBE_PROVIDERS lists 'local', which was removed in 0.4.0",
        "(#transcription)",
    ):
        assert needle in section


def test_releasing_guide_links_the_upgrade_section() -> None:
    assert "#upgrading-from-03" in (ROOT / "docs" / "RELEASING.md").read_text()
