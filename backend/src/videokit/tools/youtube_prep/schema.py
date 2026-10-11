"""Model output contract and result model for YouTube Prep.

`OUTPUT_SCHEMA` is a plain JSON schema valid for Anthropic structured outputs and OpenAI strict
mode: every property is required, `additionalProperties` is false, and no size keywords are used
(limits are enforced in `rules.py`). `YouTubePrepResult` mirrors the data model's `result.json`.
"""

from __future__ import annotations

import copy
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

SECTIONS: tuple[str, ...] = ("cover_titles", "title", "description", "tags", "hashtags", "chapters")

_STRING_LIST: dict[str, Any] = {"type": "array", "items": {"type": "string"}}

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "language": {"type": "string"},
        "cover_titles": copy.deepcopy(_STRING_LIST),
        "title": {"type": "string"},
        "description": {"type": "string"},
        "tags": copy.deepcopy(_STRING_LIST),
        "hashtags": copy.deepcopy(_STRING_LIST),
        "chapters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start_seconds": {"type": "number"},
                    "title": {"type": "string"},
                },
                "required": ["start_seconds", "title"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["language", *SECTIONS],
    "additionalProperties": False,
}


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class ModelOutputChapter(_StrictModel):
    start_seconds: float
    title: str


class ModelOutput(_StrictModel):
    """Structural check of a model output against `OUTPUT_SCHEMA`, run before the rules index into it."""

    language: str
    cover_titles: list[str]
    title: str
    description: str
    tags: list[str]
    hashtags: list[str]
    chapters: list[ModelOutputChapter]


class Chapter(BaseModel):
    start_seconds: float
    title: str


class Rendered(BaseModel):
    cover_titles: str
    title: str
    description: str
    tags: str
    hashtags: str
    chapters: str


class YouTubePrepResult(BaseModel):
    language: str
    notices: list[str] = Field(default_factory=list)
    cover_titles: list[str]
    title: str
    description: str
    tags: list[str]
    hashtags: list[str]
    chapters: list[Chapter]
    rendered: Rendered
    regenerations: dict[str, int] = Field(default_factory=dict)
