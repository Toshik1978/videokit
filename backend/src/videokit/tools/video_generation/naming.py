"""Download file names for Video Generation (research V13)."""

import re

_UNSAFE = re.compile(r'[/\\:*?"<>|\x00-\x1f\x7f-\x9f]')
_MAX_PROMPT_CHARS = 60
_FALLBACK = "clip"


def download_name(prompt: str) -> str:
    """Name the clip after the start of its prompt: ``A cat on a roof`` becomes ``A cat on a roof.mp4``."""
    words = " ".join(prompt.split())
    head = words[:_MAX_PROMPT_CHARS]
    if len(words) > _MAX_PROMPT_CHARS and words[_MAX_PROMPT_CHARS] != " " and " " in head:
        head = head[: head.rindex(" ")]
    stem = _UNSAFE.sub("_", head).strip(" .")
    if not any(ch.isalnum() for ch in stem):
        stem = _FALLBACK
    return f"{stem}.mp4"
