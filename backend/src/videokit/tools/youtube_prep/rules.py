"""YouTube Prep output rules: pure normalize -> validate -> render.

All functions work on plain dicts keyed exactly as the model output schema
(`language`, `cover_titles`, `title`, `description`, `tags`, `hashtags`,
`chapters[{start_seconds, title}]`) and never mutate their input. Each section
can also be processed on its own (used by per-section regeneration).
"""

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

SECTIONS = ("cover_titles", "title", "description", "tags", "hashtags", "chapters")

COVER_TITLE_COUNT = 3
COVER_TITLE_MAX_WORDS = 5
TITLE_MAX_CHARS = 100
DESCRIPTION_MIN_PARAGRAPHS = 2
DESCRIPTION_MAX_PARAGRAPHS = 4
DESCRIPTION_MAX_CHARS = 5000
TAGS_MAX_CHARS = 500
HASHTAGS_MIN = 3
HASHTAGS_MAX = 5
CHAPTERS_MIN = 3
CHAPTER_MIN_GAP_S = 10
CHAPTERS_MIN_DURATION_S = 30

NOTICE_TOO_SHORT = "too_short_for_chapters"

_SPACES = re.compile(r"\s+")
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_TIMESTAMP_LINE = re.compile(r"^[^\w\n]*\d{1,2}:\d{2}(?::\d{2})?(?!\d)", re.MULTILINE)
_QUOTES = frozenset('"“”„‟«»')
_BULLETS = frozenset("•‣◦∙●")
# Symbol categories removed outright; dashes (Pd) become a space so "check-in" stays two words.
# Combining marks (Mn) are kept for scripts that need them; only emoji variation selectors go.
_REMOVED_CATEGORIES = frozenset({"So", "Sk", "Cf", "Me"})
_JOINERS = frozenset("\u200c\u200d")  # kept inside words (Persian, Indic) unless they glue emoji
_VARIATION_SELECTORS = range(0xFE00, 0xFE10)
# Decorative ASCII in chapter titles; letters and digits of any script are never matched.
_DECORATIVE_RUN = re.compile(r"[|~*=<>^_`\[\]{}\\]+|#+")
_DECORATIVE_PUNCT = re.compile(r"(?<!\S)[:;/]+(?!\S)|[:;/]{2,}")
_EDGE_PUNCT = " :;/"


@dataclass(frozen=True)
class Violation:
    """A rule violation: the section it belongs to and a short human-readable problem."""

    section: str
    message: str

    def __str__(self) -> str:
        return f"{self.section}: {self.message}"


def _collapse(text: str) -> str:
    return _SPACES.sub(" ", text).strip()


# ---------------------------------------------------------------- normalize


def _normalize_cover_titles(value: list[str], duration_s: float) -> list[str]:
    return [_collapse(t) for t in value]


def _normalize_title(value: str, duration_s: float) -> str:
    return value.strip()


def _normalize_description(value: str, duration_s: float) -> str:
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    paragraphs = (p.strip() for p in _PARAGRAPH_BREAK.split(text))
    return "\n\n".join(p for p in paragraphs if p)


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.casefold()
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def _normalize_tags(value: list[str], duration_s: float) -> list[str]:
    tags = _dedupe([t for t in (t.strip() for t in value) if t])
    while tags and len(", ".join(tags)) > TAGS_MAX_CHARS:
        tags.pop()
    return tags


def _normalize_hashtags(value: list[str], duration_s: float) -> list[str]:
    tags: list[str] = []
    for raw in value:
        body = _SPACES.sub("", raw).lstrip("#")
        if body:
            tags.append("#" + body)
    return _dedupe(tags)[:HASHTAGS_MAX]


def _decorative_replacement(match: re.Match[str]) -> str:
    """Space for a decorative run, except `C#` style suffixes and `3 > 2` style comparisons."""
    text, start, end = match.string, match.start(), match.end()
    run = match.group()
    if run == "#" and start > 0 and text[start - 1].isalnum() and (end == len(text) or not text[end].isalnum()):
        return run
    if run in ("<", ">", "=") and text[start - 1 : start] == " " and text[end : end + 1] == " ":
        before, after = text[:start].rstrip(), text[end:].lstrip()
        if before[-1:].isdigit() and after[:1].isdigit():
            return run
    return " "


def clean_chapter_title(title: str) -> str:
    """Remove dashes, emojis, decorative symbols and double quotes; collapse spaces."""
    out: list[str] = []
    after_removed = False
    for ch in title:
        category = unicodedata.category(ch)
        removed = (
            ch in _QUOTES
            or ch in _BULLETS
            or ord(ch) in _VARIATION_SELECTORS
            or (category in _REMOVED_CATEGORIES and not (ch in _JOINERS and not after_removed))
        )
        if removed:
            after_removed = True
            continue
        after_removed = False
        if category == "Pd" or (category == "Sm" and ord(ch) > 127):
            out.append(" ")
        else:
            out.append(ch)
    text = _DECORATIVE_RUN.sub(_decorative_replacement, "".join(out))
    text = _DECORATIVE_PUNCT.sub(" ", text)
    return _collapse(text).strip(_EDGE_PUNCT)


def _normalize_chapters(value: list[dict[str, Any]], duration_s: float) -> list[dict[str, Any]]:
    if duration_s < CHAPTERS_MIN_DURATION_S:
        return []
    return [{"start_seconds": c["start_seconds"], "title": clean_chapter_title(c["title"])} for c in value]


_NORMALIZERS: dict[str, Callable[[Any, float], Any]] = {
    "cover_titles": _normalize_cover_titles,
    "title": _normalize_title,
    "description": _normalize_description,
    "tags": _normalize_tags,
    "hashtags": _normalize_hashtags,
    "chapters": _normalize_chapters,
}


def normalize_section(section: str, value: Any, *, duration_s: float) -> Any:
    """Normalize one section's value (raises `KeyError` for an unknown section)."""
    return _NORMALIZERS[section](value, duration_s)


def normalize(raw: dict[str, Any], *, duration_s: float) -> dict[str, Any]:
    """Return a copy of `raw` with every present section normalized."""
    return {
        key: normalize_section(key, value, duration_s=duration_s) if key in _NORMALIZERS else value
        for key, value in raw.items()
    }


def notices(*, duration_s: float) -> list[str]:
    """Result notices implied by the duration (chapters are forced empty for short videos)."""
    return [NOTICE_TOO_SHORT] if duration_s < CHAPTERS_MIN_DURATION_S else []


# ----------------------------------------------------------------- validate


def _validate_cover_titles(value: list[str], duration_s: float) -> list[str]:
    problems: list[str] = []
    if len(value) != COVER_TITLE_COUNT:
        problems.append(f"expected exactly {COVER_TITLE_COUNT} cover titles, got {len(value)}")
    for i, title in enumerate(value, start=1):
        words = len(title.split())
        if words > COVER_TITLE_MAX_WORDS:
            problems.append(f"cover title {i} has {words} words, at most {COVER_TITLE_MAX_WORDS} allowed")
    return problems


def _validate_title(value: str, duration_s: float) -> list[str]:
    if len(value) > TITLE_MAX_CHARS:
        return [f"title is {len(value)} characters, at most {TITLE_MAX_CHARS} allowed"]
    return []


def _validate_description(value: str, duration_s: float) -> list[str]:
    problems: list[str] = []
    count = len([p for p in value.split("\n\n") if p.strip()])
    if not DESCRIPTION_MIN_PARAGRAPHS <= count <= DESCRIPTION_MAX_PARAGRAPHS:
        problems.append(
            f"description has {count} paragraphs, expected {DESCRIPTION_MIN_PARAGRAPHS} to {DESCRIPTION_MAX_PARAGRAPHS}"
        )
    if len(value) > DESCRIPTION_MAX_CHARS:
        problems.append(f"description is {len(value)} characters, at most {DESCRIPTION_MAX_CHARS} allowed")
    if _TIMESTAMP_LINE.search(value):
        problems.append("description contains timestamp lines; chapter timings belong only in the chapters")
    return problems


def _validate_tags(value: list[str], duration_s: float) -> list[str]:
    return [] if value else ["tags are empty"]


def _validate_hashtags(value: list[str], duration_s: float) -> list[str]:
    if len(value) < HASHTAGS_MIN:
        return [f"expected {HASHTAGS_MIN} to {HASHTAGS_MAX} hashtags, got {len(value)}"]
    return []


def _validate_chapters(value: list[dict[str, Any]], duration_s: float) -> list[str]:
    if duration_s < CHAPTERS_MIN_DURATION_S:
        return [] if not value else [f"video is shorter than {CHAPTERS_MIN_DURATION_S} s, chapters must be empty"]
    problems: list[str] = []
    if len(value) < CHAPTERS_MIN:
        problems.append(f"at least {CHAPTERS_MIN} chapters are required, got {len(value)}")
    if value and value[0]["start_seconds"] != 0:
        problems.append("the first chapter must start at 0:00")
    for i, chapter in enumerate(value, start=1):
        if not chapter["title"].strip():
            problems.append(f"chapter {i} has an empty title")
        if chapter["start_seconds"] >= duration_s:
            problems.append(f"chapter {i} starts at or after the video duration")
    remaining = round(duration_s - value[-1]["start_seconds"], 2) if value else CHAPTER_MIN_GAP_S
    if 0 < remaining < CHAPTER_MIN_GAP_S:  # YouTube disables chapters when the last one is under 10 s
        problems.append(f"the last chapter starts only {remaining:g} s before the end; at least {CHAPTER_MIN_GAP_S} s")
    for i in range(1, len(value)):
        gap = value[i]["start_seconds"] - value[i - 1]["start_seconds"]
        if gap <= 0:
            problems.append(f"chapter {i + 1} is not after chapter {i}; start times must be strictly ascending")
        elif gap < CHAPTER_MIN_GAP_S:
            problems.append(f"chapter {i + 1} starts only {gap:g} s after chapter {i}; at least {CHAPTER_MIN_GAP_S} s")
    return problems


_VALIDATORS: dict[str, Callable[[Any, float], list[str]]] = {
    "cover_titles": _validate_cover_titles,
    "title": _validate_title,
    "description": _validate_description,
    "tags": _validate_tags,
    "hashtags": _validate_hashtags,
    "chapters": _validate_chapters,
}


def validate_section(section: str, value: Any, *, duration_s: float) -> list[Violation]:
    """Validate one (already normalized) section (raises `KeyError` for an unknown section)."""
    return [Violation(section, m) for m in _VALIDATORS[section](value, duration_s)]


def validate(normalized: dict[str, Any], *, duration_s: float) -> list[Violation]:
    """Validate every present section, in section order."""
    violations: list[Violation] = []
    for section in SECTIONS:
        if section in normalized:
            violations.extend(validate_section(section, normalized[section], duration_s=duration_s))
    return violations


# ------------------------------------------------------------------- render


def format_time(seconds: float) -> str:
    """`m:ss` below one hour, `h:mm:ss` from one hour on (fractions are floored)."""
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _render_chapters(chapters: list[dict[str, Any]]) -> str:
    return "\n".join(f'{format_time(c["start_seconds"])} "{c["title"]}"' for c in chapters)


_RENDERERS: dict[str, Callable[[Any], str]] = {
    "cover_titles": "\n".join,
    "title": str,
    "description": str,
    "tags": ", ".join,
    "hashtags": " ".join,
    "chapters": _render_chapters,
}


def render(normalized: dict[str, Any]) -> dict[str, str]:
    """Build the `rendered` block (copy-ready text per present section)."""
    return {section: _RENDERERS[section](normalized[section]) for section in SECTIONS if section in normalized}
