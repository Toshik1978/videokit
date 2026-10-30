"""Validate and store a Video Generation job's creation inputs (data-model.md rules, research V7).

The creation route calls `parse_fields` with the form's text fields and `store_inputs` with the uploaded picture
files; both raise `AppError` with a user-safe code (never the prompt or picture content). `inputs.json` is written
last and atomically, so a job directory holding it always holds every picture it lists.
"""

import json
import os
import re
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Protocol, TypeGuard, cast, get_args

from videokit.core.errors import AppError
from videokit.core.videogen import AspectRatio, Resolution

INPUTS_JSON = "inputs.json"
PICTURES_DIR = "pictures"
MAX_PROMPT_CHARS = 2000
MAX_PICTURES = 7
MAX_PICTURE_BYTES = 10_000_000  # decimal megabytes, per picture
MIN_DURATION_S, MAX_DURATION_S = 5, 10
DEFAULT_ASPECT_RATIO: AspectRatio = "9:16"
DEFAULT_RESOLUTION: Resolution = "1080p"

_ASPECT_RATIOS = set(get_args(AspectRatio))
_RESOLUTIONS = set(get_args(Resolution))
_HEIF_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1"}
_SNIFF_BYTES = 12


class PictureNormalizer(Protocol):
    """The slice of `MediaService` used here (`ctx.media`)."""

    def normalize_picture(self, data_path: Path, out_path: Path) -> None: ...


@dataclass(frozen=True)
class CreationInputs:
    """The validated text fields of a creation request."""

    prompt: str
    duration_s: int
    aspect_ratio: AspectRatio
    resolution: Resolution


@dataclass(frozen=True)
class StoredInputs:
    """A job's complete inputs as `store_inputs` wrote them: the fields and the picture files, in order."""

    fields: CreationInputs
    pictures: list[Path]


def _invalid(message: str) -> AppError:
    return AppError("invalid_request", message, 422)


def parse_fields(
    prompt: str | None,
    duration_s: str | None,
    aspect_ratio: str | None,
    resolution: str | None,
    *,
    durations: Sequence[int],
) -> CreationInputs:
    """Validate the form's text fields; ``None`` means the field was absent.

    ``durations`` are the whole seconds the provider supports (``ctx.video.capabilities().durations_s``); the
    accepted values are those within 5-10. ``aspect_ratio`` and ``resolution`` default when absent.
    """
    # Multipart encoding turns every line break into CRLF; the form counts one character per break, so normalize
    # to LF first and the limit means the same on both sides (the stored and sent prompt is the normalized text).
    text = (prompt or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise _invalid("Describe the clip you want.")
    if len(text) > MAX_PROMPT_CHARS:
        raise _invalid(f"The prompt can be at most {MAX_PROMPT_CHARS:,} characters.")
    seconds = _parse_duration(duration_s, durations)
    ratio = DEFAULT_ASPECT_RATIO if aspect_ratio is None else aspect_ratio
    if ratio not in _ASPECT_RATIOS:
        raise _invalid("The aspect ratio must be 9:16 or 16:9.")
    quality = DEFAULT_RESOLUTION if resolution is None else resolution
    if quality not in _RESOLUTIONS:
        raise _invalid("The resolution must be 1080p or 720p.")
    return CreationInputs(text, seconds, cast(AspectRatio, ratio), cast(Resolution, quality))


def _parse_duration(value: str | None, durations: Sequence[int]) -> int:
    text = (value or "").strip()
    if not (text.isascii() and text.isdigit()):  # a whole number: no sign, fraction, separator or non-ASCII digit
        raise _invalid("The duration must be a whole number of seconds.")
    seconds = int(text)
    if not MIN_DURATION_S <= seconds <= MAX_DURATION_S or seconds not in durations:
        raise _invalid("This duration is not supported.")
    return seconds


def _is_picture(head: bytes) -> bool:
    """JPEG, PNG, WebP or HEIC/HEIF by their signatures; the client's content type is never consulted."""
    return (
        head.startswith(b"\xff\xd8\xff")
        or head.startswith(b"\x89PNG\r\n\x1a\n")
        or (head[:4] == b"RIFF" and head[8:12] == b"WEBP")
        or (head[4:8] == b"ftyp" and head[8:12] in _HEIF_BRANDS)
    )


def _inspect(picture: BinaryIO) -> None:
    """Raise ``picture_too_large`` / ``invalid_picture``; leaves the file positioned at its start."""
    size = picture.seek(0, os.SEEK_END)
    if size > MAX_PICTURE_BYTES:
        raise AppError("picture_too_large", "A picture can be at most 10 MB.", 413)
    picture.seek(0)
    head = picture.read(_SNIFF_BYTES)
    picture.seek(0)
    if not _is_picture(head):
        raise AppError("invalid_picture", "Use JPEG, PNG, WebP or HEIC pictures.", 422)


def store_inputs(job_dir: Path, media: PictureNormalizer, fields: CreationInputs, pictures: Sequence[BinaryIO]) -> None:
    """Write ``pictures/<n>.jpg`` (1-based, in order) and then ``inputs.json`` into ``job_dir``.

    Raises ``too_many_pictures`` (422), ``picture_too_large`` (413) or ``invalid_picture`` (422, wrong type or
    undecodable) before ``inputs.json`` exists; the half-written ``pictures/`` is removed, and the caller discards
    the job. All pictures are checked before anything is written.
    """
    if len(pictures) > MAX_PICTURES:
        raise AppError("too_many_pictures", f"At most {MAX_PICTURES} pictures can be used.", 422)
    for picture in pictures:
        _inspect(picture)
    job_dir.mkdir(parents=True, exist_ok=True)
    pictures_dir = job_dir / PICTURES_DIR
    names: list[str] = []
    try:
        if pictures:
            pictures_dir.mkdir()
        for number, picture in enumerate(pictures, start=1):
            raw = pictures_dir / f"{number}.src"
            with raw.open("wb") as out:
                shutil.copyfileobj(picture, out)
            media.normalize_picture(raw, pictures_dir / f"{number}.jpg")
            raw.unlink()
            names.append(f"{PICTURES_DIR}/{number}.jpg")
    except BaseException:
        shutil.rmtree(pictures_dir, ignore_errors=True)
        raise
    document = {
        "prompt": fields.prompt,
        "duration_s": fields.duration_s,
        "aspect_ratio": fields.aspect_ratio,
        "resolution": fields.resolution,
        "pictures": names,
    }
    target = job_dir / INPUTS_JSON
    tmp = target.with_name(f"{INPUTS_JSON}.tmp")
    tmp.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, target)  # readers never see a partial file


_PICTURE_NAME = re.compile(rf"{PICTURES_DIR}/[1-9][0-9]*\.jpg")


def load_inputs(job_dir: Path) -> StoredInputs:
    """Read `inputs.json` back and check it is complete (every listed picture present, every value valid).

    Raises `AppError` 422 `invalid_request` otherwise; the message never carries the prompt.
    """
    incomplete = _invalid("The clip inputs are incomplete.")
    try:
        document = json.loads((job_dir / INPUTS_JSON).read_text(encoding="utf-8"))
    except OSError, ValueError:
        raise incomplete from None
    if not _is_complete(document):
        raise incomplete
    pictures = [job_dir / name for name in document["pictures"]]
    if not all(path.is_file() for path in pictures):
        raise incomplete
    fields = CreationInputs(
        document["prompt"],
        document["duration_s"],
        cast(AspectRatio, document["aspect_ratio"]),
        cast(Resolution, document["resolution"]),
    )
    return StoredInputs(fields, pictures)


def _is_complete(document: object) -> TypeGuard[dict[str, Any]]:
    """Whether a parsed `inputs.json` has every field with a valid value (picture files not checked)."""
    if not isinstance(document, dict):
        return False
    prompt, seconds, names = document.get("prompt"), document.get("duration_s"), document.get("pictures")
    return (
        isinstance(prompt, str)
        and 0 < len(prompt.strip()) <= MAX_PROMPT_CHARS
        and isinstance(seconds, int)
        and not isinstance(seconds, bool)
        and document.get("aspect_ratio") in _ASPECT_RATIOS
        and document.get("resolution") in _RESOLUTIONS
        and isinstance(names, list)
        and len(names) <= MAX_PICTURES
        and all(isinstance(name, str) and _PICTURE_NAME.fullmatch(name) for name in names)
    )
