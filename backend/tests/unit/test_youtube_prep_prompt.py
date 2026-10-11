from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest
from pydantic import ValidationError

from videokit.tools.youtube_prep import prompt
from videokit.tools.youtube_prep.schema import (
    OUTPUT_SCHEMA,
    ModelOutput,
    ModelOutputChapter,
    YouTubePrepResult,
)

EXAMPLE: dict[str, Any] = {
    "language": "en",
    "notices": ["no_speech"],
    "cover_titles": ["Hidden Gem Resort", "Worth The Price?", "Best Pool Ever"],
    "title": "A title",
    "description": "Paragraph one.\n\nParagraph two.",
    "tags": ["hotel review", "travel"],
    "hashtags": ["#travel", "#hotel", "#review"],
    "chapters": [{"start_seconds": 0, "title": "Arrival and lobby"}],
    "rendered": {
        "cover_titles": "Hidden Gem Resort\nWorth The Price?\nBest Pool Ever",
        "title": "A title",
        "description": "Paragraph one.\n\nParagraph two.",
        "tags": "hotel review, travel",
        "hashtags": "#travel #hotel #review",
        "chapters": '0:00 "Arrival and lobby"',
    },
    "regenerations": {"title": 1},
}


@dataclass
class Seg:
    start: float
    text: str


def _objects(node: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        if node.get("type") == "object":
            found.append(node)
        for value in node.values():
            found.extend(_objects(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_objects(value))
    return found


def test_schema_required_and_strict() -> None:
    expected = {"language", "cover_titles", "title", "description", "tags", "hashtags", "chapters"}
    assert set(OUTPUT_SCHEMA["required"]) == expected
    assert set(OUTPUT_SCHEMA["properties"]) == expected
    for obj in _objects(OUTPUT_SCHEMA):
        assert obj["additionalProperties"] is False
        assert set(obj["required"]) == set(obj["properties"])
    text = json.dumps(OUTPUT_SCHEMA)
    for banned in ("minLength", "maxLength", "minItems", "maxItems", "minimum", "maximum", "pattern"):
        assert banned not in text


def test_model_output_mirrors_the_output_schema() -> None:
    assert list(ModelOutput.model_fields) == OUTPUT_SCHEMA["required"]
    assert set(ModelOutput.model_fields) == set(OUTPUT_SCHEMA["properties"])
    chapter_item = OUTPUT_SCHEMA["properties"]["chapters"]["items"]
    assert list(ModelOutputChapter.model_fields) == chapter_item["required"]
    assert set(ModelOutputChapter.model_fields) == set(chapter_item["properties"])


def test_model_output_is_strict() -> None:
    valid = {k: v for k, v in EXAMPLE.items() if k in OUTPUT_SCHEMA["required"]}
    assert ModelOutput.model_validate(valid).model_dump()["chapters"] == [
        {"start_seconds": 0.0, "title": "Arrival and lobby"}
    ]
    for broken in (
        {**valid, "extra": 1},
        {**valid, "title": 1},
        {**valid, "chapters": [{"start_seconds": "0", "title": "x"}]},
    ):
        with pytest.raises(ValidationError):
            ModelOutput.model_validate(broken)


def test_model_round_trip() -> None:
    model = YouTubePrepResult.model_validate(EXAMPLE)
    assert model.chapters[0].start_seconds == 0
    assert model.model_dump(mode="json") == EXAMPLE


def test_context_text_formats_times() -> None:
    text = prompt.build_context_text([Seg(0, "Hello"), Seg(65.9, " there "), Seg(3725, "late")])
    assert text.splitlines() == ["[0:00] Hello", "[1:05] there", "[1:02:05] late"]


def test_context_text_no_speech() -> None:
    assert "no speech" in prompt.build_context_text([]).lower()


def test_determinism() -> None:
    segs = [Seg(1, "a"), Seg(2, "b")]
    assert prompt.build_context_text(segs) == prompt.build_context_text(segs)
    assert prompt.generate_turn() == prompt.generate_turn()
    assert isinstance(prompt.SYSTEM_PROMPT, str)


def test_system_prompt_rules() -> None:
    s = prompt.SYSTEM_PROMPT.lower()
    for needle in ("output language", "5 words", "timestamp", "dash", "emoji", "0:00", "topic"):
        assert needle in s
    assert "detect" not in s  # the language comes from transcription, not from the model (FR-020)


def test_repair_turn_lists_violations() -> None:
    out = prompt.repair_turn(["title is 120 characters", "only 2 chapters"])
    assert "- title is 120 characters" in out
    assert "- only 2 chapters" in out


def test_regenerate_turn_with_hint() -> None:
    out = prompt.regenerate_turn("title", "make it punchier", {"title": "Old title"})
    assert 'Regenerate only the "title" section' in out
    # The request keeps the full generation schema (cache-friendly), so the other sections come back empty.
    assert 'set every other section to an empty value ("" for text, [] for lists)' in out
    assert "make it punchier" in out
    assert "Old title" in out


@pytest.mark.parametrize("hint", ["", "   ", None])
def test_regenerate_turn_without_hint(hint: str | None) -> None:
    out = prompt.regenerate_turn("tags", hint, {"tags": ["a", "b"]})
    assert "hint" not in out.lower()
    assert "tags" in out


def test_regenerate_unknown_section() -> None:
    with pytest.raises(ValueError):
        prompt.regenerate_turn("nope", None, {})
