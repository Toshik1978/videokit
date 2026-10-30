"""YouTube Prep's supported output languages and the per-job language setting (`language.json`)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

# ISO 639-1 code -> English name (the name goes into the model instruction).
LANGUAGES: dict[str, str] = {
    "af": "Afrikaans",
    "ar": "Arabic",
    "az": "Azerbaijani",
    "be": "Belarusian",
    "bg": "Bulgarian",
    "bn": "Bengali",
    "ca": "Catalan",
    "cs": "Czech",
    "cy": "Welsh",
    "da": "Danish",
    "de": "German",
    "el": "Greek",
    "en": "English",
    "es": "Spanish",
    "et": "Estonian",
    "eu": "Basque",
    "fa": "Persian",
    "fi": "Finnish",
    "fr": "French",
    "gl": "Galician",
    "gu": "Gujarati",
    "he": "Hebrew",
    "hi": "Hindi",
    "hr": "Croatian",
    "hu": "Hungarian",
    "hy": "Armenian",
    "id": "Indonesian",
    "is": "Icelandic",
    "it": "Italian",
    "ja": "Japanese",
    "ka": "Georgian",
    "kk": "Kazakh",
    "kn": "Kannada",
    "ko": "Korean",
    "lt": "Lithuanian",
    "lv": "Latvian",
    "mk": "Macedonian",
    "ml": "Malayalam",
    "mr": "Marathi",
    "ms": "Malay",
    "nl": "Dutch",
    "no": "Norwegian",
    "pa": "Punjabi",
    "pl": "Polish",
    "pt": "Portuguese",
    "ro": "Romanian",
    "ru": "Russian",
    "sk": "Slovak",
    "sl": "Slovenian",
    "sr": "Serbian",
    "sv": "Swedish",
    "sw": "Swahili",
    "ta": "Tamil",
    "te": "Telugu",
    "th": "Thai",
    "tl": "Tagalog",
    "tr": "Turkish",
    "uk": "Ukrainian",
    "ur": "Urdu",
    "vi": "Vietnamese",
    "zh": "Chinese",
}

AUTO = "auto"
LANGUAGE_JSON = "language.json"


@dataclass(frozen=True)
class LanguageSetting:
    """The language chosen at job creation: `choice` is `AUTO` or a supported code; `fallback` a supported code."""

    choice: str = AUTO
    fallback: str = "en"


def write_setting(job_dir: Path, setting: LanguageSetting) -> None:
    """Write `language.json` atomically (temp file + replace)."""
    target = job_dir / LANGUAGE_JSON
    tmp = target.with_name(f"{LANGUAGE_JSON}.tmp")
    tmp.write_text(json.dumps({"choice": setting.choice, "fallback": setting.fallback}), encoding="utf-8")
    os.replace(tmp, target)


def read_setting(job_dir: Path) -> LanguageSetting:
    """Read `language.json`; a missing file means the defaults. Raises `ValueError` on a malformed or invalid file."""
    path = job_dir / LANGUAGE_JSON
    if not path.exists():
        return LanguageSetting()
    data = json.loads(path.read_text(encoding="utf-8"))  # JSONDecodeError is a ValueError
    if not isinstance(data, dict):
        raise ValueError("language setting must be an object")
    choice, fallback = data.get("choice"), data.get("fallback")
    if not isinstance(choice, str) or (choice != AUTO and choice not in LANGUAGES):
        raise ValueError("unsupported language choice")
    if not isinstance(fallback, str) or fallback not in LANGUAGES:
        raise ValueError("unsupported fallback language")
    return LanguageSetting(choice=choice, fallback=fallback)


def instruction(code: str) -> str:
    """The per-call turn instruction; a code outside the table stands in for its own name."""
    return f"Write all output in {LANGUAGES.get(code, code)} (language code {code})."
