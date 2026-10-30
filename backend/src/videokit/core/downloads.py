"""Helpers shared by the tools that serve a generated file as a download."""

import re
import unicodedata
from pathlib import PurePosixPath
from urllib.parse import quote

DEFAULT_FALLBACK = "audio.mp3"
# Characters kept in the ASCII fallback; anything else (quotes, backslashes, slashes, `;`, `=`, ...) becomes `_`.
_NOT_FALLBACK_SAFE = re.compile(r"[^A-Za-z0-9 ._()\[\],!&'+@#$%~-]")


def _ascii_fallback(name: str, fallback: str) -> str:
    """An ASCII stand-in for `name` in the plain `filename` parameter: accents dropped, everything outside a
    conservative allowlist `_`, `fallback` when no letter or digit is left besides the extension.

    The allowlist is applied after NFKD, which maps fullwidth and small forms back to ASCII syntax (U+FF02 to `"`,
    U+FE68 to `\\`, U+FF0F to `/`), so the result can never close the quoted string or add a parameter.
    """
    decomposed = "".join(c for c in unicodedata.normalize("NFKD", name) if not unicodedata.combining(c))
    ascii_name = _NOT_FALLBACK_SAFE.sub("_", decomposed)
    stem = ascii_name.removesuffix(PurePosixPath(fallback).suffix)
    return ascii_name if any(c.isalnum() for c in stem) else fallback


def content_disposition(name: str, *, fallback: str = DEFAULT_FALLBACK) -> str:
    """`attachment` with a plain `filename`, plus an RFC 5987 `filename*` for non-ASCII names and names with `%`.

    `name` comes from `download_name`, which removes quotes, backslashes, slashes and control characters, so an
    ASCII name is safe inside the quoted string as is; a non-ASCII name gets a sanitized ASCII fallback there, or the
    caller's `fallback` file name (an ASCII name with the tool's extension) when nothing readable is left. Some
    browsers decode `%XX` in a plain `filename`, so a name with `%` also gets `filename*`, percent-encoded in full,
    which browsers prefer.
    """
    if name.isascii() and "%" not in name:
        return f'attachment; filename="{name}"'
    plain = name if name.isascii() else _ascii_fallback(name, fallback)
    return f"attachment; filename=\"{plain}\"; filename*=UTF-8''{quote(name, safe='')}"
