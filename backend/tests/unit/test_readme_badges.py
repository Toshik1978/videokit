"""The README badges read the same gist files the CI workflow writes."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BADGE_GIST = "gist.githubusercontent.com/toshik1978/3efce57fa029fdddf6aa3c3faefa8a70"


def _badge_files() -> list[tuple[str, str]]:
    """(gist path, file name) for every gist-backed badge in the README."""
    readme = (ROOT / "README.md").read_text()
    pattern = r"https://gist\.githubusercontent\.com/([^)\s]+)/raw/([\w-]+\.json)"
    return [(m.group(1), m.group(2)) for m in re.finditer(pattern, readme)]


def test_readme_badges_point_at_the_project_gist() -> None:
    badges = _badge_files()
    assert len(badges) == 4
    assert {f"gist.githubusercontent.com/{gist}" for gist, _ in badges} == {BADGE_GIST}


def test_readme_badges_read_the_files_ci_writes() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    written = set(re.findall(r"'([\w-]+\.json)': badge\(", ci))
    assert {name for _, name in _badge_files()} == written
