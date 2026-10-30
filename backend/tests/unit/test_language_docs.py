"""The output-language feature (006) is documented in the README, CLAUDE.md and the 001 contract."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _read(*parts: str) -> str:
    return (ROOT.joinpath(*parts)).read_text()


def test_readme_documents_the_language_choice() -> None:
    readme = _read("README.md")
    section = readme.split("\n## YouTube Prep\n", 1)[1].split("\n## ", 1)[0]
    assert "Same as the speech" in section
    assert "remembers" in section
    assert "browser's language" in section


def test_claude_md_has_the_new_output_language_rule() -> None:
    text = _read(".claude", "CLAUDE.md")
    assert "no speech → English" not in text
    assert "specs/006-youtube-prep-language/" in text
    assert "browser's language" in text


def test_http_api_contract_points_at_the_006_delta() -> None:
    text = _read("specs", "001-videokit-youtube-prep", "contracts", "http-api.md")
    assert "specs/006-youtube-prep-language/contracts/http-api.md" in text
