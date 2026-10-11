"""Prompt text and builders for YouTube Prep.

Everything here is deterministic: the system prompt and context text must be byte-identical for
the same input so providers can cache the stable prefix (system + context + frames). Builders
return plain strings; the pipeline wraps them into request turns.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any, Protocol

from videokit.tools.youtube_prep.schema import SECTIONS

SYSTEM_PROMPT = """\
You prepare YouTube publishing metadata for a video. You are given the timestamped transcript of \
the video (lines like "[m:ss] text") and frames sampled from it (each preceded by a label like \
"[frame m:ss]"). Base everything on what is actually said and shown.

Language
- Write EVERYTHING in the output language named in the request: cover titles, title, \
description, tags, hashtags and chapter titles, even where the video uses other languages. Return \
its ISO 639-1 code in "language".
- If there is no speech, rely on the frames.

YouTube best practices
- Cover titles: exactly 3 distinct options for the thumbnail, each at most 5 words, punchy and \
curiosity-driven, no clickbait that the video does not deliver.
- Title: at most 100 characters, the main keyword early, specific and honest.
- Description: 2 to 4 paragraphs separated by blank lines, at most 5000 characters. Open with a \
strong hook in the first lines, summarize the value of the video, and use natural keywords. Do \
NOT put chapters, timestamps or time ranges in the description, and do not put hashtags in it.
- Tags: a list of relevant search phrases, most important first, without the # symbol.
- Hashtags: 3 to 5, each starting with # and containing no spaces.

Chapters
- Add chapters only at real topic changes, not at every sentence. The first chapter starts at \
0:00 (start_seconds 0). Use at least 3 chapters, each at least 10 seconds after the previous one, \
in ascending order, and the last one at least 10 seconds before the end of the video.
- Chapter titles are short (a few words) and plain: no dashes, no emojis, no symbols or \
decoration, no quotation marks.
- start_seconds is a number of seconds from the start of the video.
- If the video is shorter than 30 seconds, return an empty chapters list.

Output
- Reply only with JSON matching the provided schema.\
"""

NO_SPEECH_TEXT = "(No speech detected in this video. Use the frames only.)"


class SegmentLike(Protocol):
    start: float
    text: str


def format_time(seconds: float) -> str:
    """`m:ss` below one hour, `h:mm:ss` from one hour on."""
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def build_context_text(segments: Iterable[SegmentLike]) -> str:
    """Transcript as `[m:ss] text` lines, or a clear no-speech line."""
    lines = [f"[{format_time(s.start)}] {s.text.strip()}" for s in segments if s.text.strip()]
    return "\n".join(lines) if lines else NO_SPEECH_TEXT


def generate_turn() -> str:
    return (
        "Produce the YouTube metadata for this video: language, cover_titles, title, description, "
        "tags, hashtags and chapters, following all the rules in the system instructions."
    )


def repair_turn(violations: list[str]) -> str:
    listed = "\n".join(f"- {v}" for v in violations)
    return (
        "Your previous output violates these rules:\n"
        f"{listed}\n"
        "Produce the complete output again, fixing every violation and keeping everything else "
        "that was already correct."
    )


def regenerate_turn(section: str, hint: str | None, current_result: Mapping[str, Any]) -> str:
    """Instruction to replace only `section`, optionally steered by a user hint."""
    if section not in SECTIONS:
        raise ValueError(f"unknown section: {section}")
    current = json.dumps(current_result.get(section), ensure_ascii=False)
    parts = [
        f'Regenerate only the "{section}" section. Return the complete JSON object with "language" and a new '
        f'"{section}", and set every other section to an empty value ("" for text, [] for lists).',
        f"Current value of {section}: {current}",
        "Write a clearly different alternative that still follows all the rules.",
    ]
    if hint and hint.strip():
        parts.append(f"User hint: {hint.strip()}")
    return "\n".join(parts)
