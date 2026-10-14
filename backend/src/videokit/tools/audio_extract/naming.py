"""Download file names for Audio Extract (research A6)."""

import re

_EXTENSION = re.compile(r"\.[A-Za-z0-9]{1,8}\Z")
_UNSAFE = re.compile(r'[/\\:*?"<>|\x00-\x1f\x7f-\x9f]')
_MAX_STEM = 150
_FALLBACK = "audio"


def download_name(source_name: str | None) -> str:
    """Name the MP3 after the uploaded video: ``trip.mp4`` becomes ``trip.mp3``."""
    stem = _EXTENSION.sub("", source_name or "")
    stem = _UNSAFE.sub("_", stem).strip(" .")
    stem = stem[:_MAX_STEM].strip(" .")
    return f"{stem or _FALLBACK}.mp3"
