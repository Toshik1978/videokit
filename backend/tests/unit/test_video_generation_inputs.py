"""Validation and storage of Video Generation's creation inputs (data-model.md rules, research V7)."""

import io
import json
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from videokit.core.config import Settings
from videokit.core.errors import AppError
from videokit.core.media import MediaService
from videokit.tools.video_generation.inputs import (
    MAX_PICTURE_BYTES,
    MAX_PICTURES,
    MAX_PROMPT_CHARS,
    CreationInputs,
    load_inputs,
    parse_fields,
    store_inputs,
)

MakePicture = Callable[..., Path]

DURATIONS = [5, 6, 7, 8, 9, 10]
JPEG_HEAD = b"\xff\xd8\xff\xe0" + b"\x00" * 8
PNG_HEAD = b"\x89PNG\r\n\x1a\n" + b"\x00" * 4


class FakeMedia:
    """Stands in for `MediaService`: writes a marker JPEG and records what it was given."""

    def __init__(self) -> None:
        self.sources: list[bytes] = []

    def normalize_picture(self, data_path: Path, out_path: Path) -> None:
        data = data_path.read_bytes()
        self.sources.append(data)
        out_path.write_bytes(b"jpeg:" + data[:4])


def _fields(**over: str | None) -> CreationInputs:
    values: dict[str, str | None] = {
        "prompt": "a red fox",
        "duration_s": "5",
        "aspect_ratio": None,
        "resolution": None,
    }
    values.update(over)
    return parse_fields(
        values["prompt"], values["duration_s"], values["aspect_ratio"], values["resolution"], durations=DURATIONS
    )


def _error(call: Callable[[], object]) -> AppError:
    with pytest.raises(AppError) as info:
        call()
    return info.value


# --- fields ---------------------------------------------------------------------------------------------------


def test_defaults_and_trimmed_prompt() -> None:
    assert _fields(prompt="  a red fox \n") == CreationInputs("a red fox", 5, "9:16", "1080p")


def test_explicit_values_are_kept() -> None:
    got = _fields(duration_s="10", aspect_ratio="16:9", resolution="720p")
    assert got == CreationInputs("a red fox", 10, "16:9", "720p")


@pytest.mark.parametrize("prompt", [None, "", "   ", "\n\t "])
def test_missing_or_blank_prompt_is_invalid(prompt: str | None) -> None:
    err = _error(lambda: _fields(prompt=prompt))
    assert (err.code, err.status) == ("invalid_request", 422)


def test_prompt_length_boundary_counts_characters_after_trimming() -> None:
    assert _fields(prompt="x").prompt == "x"
    assert len(_fields(prompt="я" * MAX_PROMPT_CHARS).prompt) == 2000  # characters, not bytes
    assert len(_fields(prompt=f"  {'x' * MAX_PROMPT_CHARS}  ").prompt) == 2000
    err = _error(lambda: _fields(prompt="x" * (MAX_PROMPT_CHARS + 1)))
    assert (err.code, err.status) == ("invalid_request", 422)


def test_line_breaks_are_normalized_to_lf_before_trimming_and_counting() -> None:
    assert _fields(prompt="  one\r\ntwo\rthree\nfour \r\n").prompt == "one\ntwo\nthree\nfour"


def test_prompt_boundary_counts_crlf_line_breaks_as_one_character() -> None:
    # Browsers send every line break of a multipart text field as CRLF; the form counts it as 1 character.
    line = "x" * 99
    at_limit = "\r\n".join([line] * 19) + "\r\n" + "x" * (MAX_PROMPT_CHARS - 19 * 100)
    assert len(at_limit.replace("\r\n", "\n")) == MAX_PROMPT_CHARS
    assert len(at_limit) > MAX_PROMPT_CHARS
    stored = _fields(prompt=at_limit).prompt
    assert len(stored) == MAX_PROMPT_CHARS
    assert "\r" not in stored
    err = _error(lambda: _fields(prompt=at_limit + "x"))
    assert (err.code, err.status) == ("invalid_request", 422)


@pytest.mark.parametrize("duration", ["5", "7", "10"])
def test_supported_durations_are_accepted(duration: str) -> None:
    assert _fields(duration_s=duration).duration_s == int(duration)


@pytest.mark.parametrize("duration", [None, "", "abc", "7.5", "7.0", "-6", "+6", "1_0", "٧", "4", "11", "0", "99999"])
def test_other_durations_are_invalid(duration: str | None) -> None:
    err = _error(lambda: _fields(duration_s=duration))
    assert (err.code, err.status) == ("invalid_request", 422)


def test_duration_must_be_one_the_provider_supports() -> None:
    assert parse_fields("p", "6", None, None, durations=[6, 8]).duration_s == 6
    err = _error(lambda: parse_fields("p", "7", None, None, durations=[6, 8]))
    assert err.code == "invalid_request"


def test_duration_is_limited_to_five_to_ten_even_if_the_provider_offers_more() -> None:
    err = _error(lambda: parse_fields("p", "12", None, None, durations=[5, 12]))
    assert err.code == "invalid_request"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("aspect_ratio", "1:1"),
        ("aspect_ratio", "16:9 "),
        ("resolution", "4k"),
        ("aspect_ratio", ""),
        ("resolution", ""),
    ],
)
def test_unknown_or_empty_aspect_ratio_or_resolution_is_invalid(field: str, value: str) -> None:
    err = _error(lambda: _fields(**{field: value}))
    assert (err.code, err.status) == ("invalid_request", 422)


# --- pictures -------------------------------------------------------------------------------------------------


def _store(job_dir: Path, pictures: list[bytes], *, media: MediaService | FakeMedia | None = None) -> FakeMedia:
    fake = media if media is not None else FakeMedia()
    store_inputs(job_dir, fake, _fields(), [io.BytesIO(p) for p in pictures])
    return cast(FakeMedia, fake)


def test_no_pictures_writes_only_inputs_json(tmp_path: Path) -> None:
    _store(tmp_path, [])
    assert json.loads((tmp_path / "inputs.json").read_text(encoding="utf-8")) == {
        "prompt": "a red fox",
        "duration_s": 5,
        "aspect_ratio": "9:16",
        "resolution": "1080p",
        "pictures": [],
    }
    assert sorted(p.name for p in tmp_path.iterdir()) == ["inputs.json"]  # no tmp file left


def test_pictures_are_stored_in_order_as_numbered_jpegs(tmp_path: Path) -> None:
    first, second = JPEG_HEAD + b"one", PNG_HEAD + b"two"
    media = _store(tmp_path, [first, second])
    assert media.sources == [first, second]
    assert (tmp_path / "pictures" / "1.jpg").read_bytes() == b"jpeg:" + first[:4]
    assert (tmp_path / "pictures" / "2.jpg").read_bytes() == b"jpeg:" + second[:4]
    assert sorted(p.name for p in (tmp_path / "pictures").iterdir()) == ["1.jpg", "2.jpg"]  # no raw copies left
    stored = json.loads((tmp_path / "inputs.json").read_text(encoding="utf-8"))
    assert stored["pictures"] == ["pictures/1.jpg", "pictures/2.jpg"]


def test_non_latin_prompt_is_stored_unescaped(tmp_path: Path) -> None:
    store_inputs(tmp_path, FakeMedia(), _fields(prompt="Лиса бежит"), [])
    assert "Лиса бежит" in (tmp_path / "inputs.json").read_text(encoding="utf-8")


def test_seven_pictures_are_accepted_eight_are_not(tmp_path: Path) -> None:
    _store(tmp_path / "ok", [JPEG_HEAD] * MAX_PICTURES)
    assert len(json.loads((tmp_path / "ok" / "inputs.json").read_text(encoding="utf-8"))["pictures"]) == 7
    err = _error(lambda: _store(tmp_path / "many", [JPEG_HEAD] * (MAX_PICTURES + 1)))
    assert (err.code, err.status) == ("too_many_pictures", 422)
    assert not (tmp_path / "many").exists()  # rejected before anything is written


def test_picture_size_boundary_is_ten_decimal_megabytes(tmp_path: Path) -> None:
    exact = JPEG_HEAD + b"\x00" * (MAX_PICTURE_BYTES - len(JPEG_HEAD))
    assert len(exact) == 10_000_000
    _store(tmp_path / "ok", [exact])
    assert (tmp_path / "ok" / "pictures" / "1.jpg").is_file()
    err = _error(lambda: _store(tmp_path / "big", [exact + b"\x00"]))
    assert (err.code, err.status) == ("picture_too_large", 413)


@pytest.mark.parametrize(
    "head",
    [
        JPEG_HEAD,
        PNG_HEAD,
        b"RIFF\x10\x00\x00\x00WEBPVP8 ",  # WebP
        b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00",  # HEIC
        b"\x00\x00\x00\x18ftypmif1\x00\x00\x00\x00",  # HEIF
    ],
)
def test_supported_types_are_detected_by_content(tmp_path: Path, head: bytes) -> None:
    _store(tmp_path, [head])
    assert (tmp_path / "pictures" / "1.jpg").is_file()


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"hello, this is not a picture",
        b"GIF89a" + b"\x00" * 10,
        b"%PDF-1.7 " + b"\x00" * 10,
        b"RIFF\x10\x00\x00\x00WAVEfmt ",  # RIFF but not WebP
        b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00",  # an MP4 video, not a HEIF picture
        b"\xff\xd8",  # too short for a JPEG signature
    ],
)
def test_unsupported_content_is_an_invalid_picture(tmp_path: Path, data: bytes) -> None:
    err = _error(lambda: _store(tmp_path, [data]))
    assert (err.code, err.status) == ("invalid_picture", 422)


def test_only_the_content_decides_never_a_content_type_or_file_name(tmp_path: Path) -> None:
    # The function takes bare file objects, so a text file labelled image/jpeg cannot get through ...
    err = _error(lambda: _store(tmp_path / "text", [b"plain text pretending to be image/jpeg"]))
    assert err.code == "invalid_picture"
    # ... and a real PNG is accepted whatever it was called.
    _store(tmp_path / "png", [PNG_HEAD])
    assert (tmp_path / "png" / "pictures" / "1.jpg").is_file()


def test_a_failing_picture_leaves_no_inputs_json_and_no_pictures(tmp_path: Path) -> None:
    err = _error(lambda: _store(tmp_path, [JPEG_HEAD, b"not a picture"]))
    assert err.code == "invalid_picture"
    assert not (tmp_path / "inputs.json").exists()
    assert not (tmp_path / "pictures").exists()


def test_pictures_are_read_from_their_start_wherever_the_file_position_is(tmp_path: Path) -> None:
    stream = io.BytesIO(JPEG_HEAD + b"rest")
    stream.seek(5)
    media = FakeMedia()
    store_inputs(tmp_path, media, _fields(), [stream])
    assert media.sources == [JPEG_HEAD + b"rest"]


# --- real decoding --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", ["png", "jpeg", "webp", "heic"])
def test_real_pictures_are_normalized_to_jpegs(tmp_path: Path, make_picture: MakePicture, fmt: str) -> None:
    picture = make_picture(fmt)
    job_dir = tmp_path / "job"
    media = MediaService(Settings(data_dir=tmp_path))
    store_inputs(job_dir, media, _fields(), [io.BytesIO(picture.read_bytes())])
    assert (job_dir / "pictures" / "1.jpg").read_bytes()[:3] == b"\xff\xd8\xff"


def test_an_undecodable_picture_with_a_valid_signature_is_an_invalid_picture(tmp_path: Path) -> None:
    job_dir = tmp_path / "job"
    media = MediaService(Settings(data_dir=tmp_path))
    err = _error(lambda: store_inputs(job_dir, media, _fields(), [io.BytesIO(JPEG_HEAD + b"garbage")]))
    assert (err.code, err.status) == ("invalid_picture", 422)
    assert not (job_dir / "inputs.json").exists()


# --- loading --------------------------------------------------------------------------------------------------


def test_stored_inputs_load_back_with_their_picture_files(tmp_path: Path) -> None:
    store_inputs(tmp_path, FakeMedia(), _fields(duration_s="7"), [io.BytesIO(JPEG_HEAD), io.BytesIO(PNG_HEAD)])
    stored = load_inputs(tmp_path)
    assert stored.fields == CreationInputs("a red fox", 7, "9:16", "1080p")
    assert stored.pictures == [tmp_path / "pictures" / "1.jpg", tmp_path / "pictures" / "2.jpg"]


def test_inputs_without_pictures_load_with_an_empty_list(tmp_path: Path) -> None:
    store_inputs(tmp_path, FakeMedia(), _fields(), [])
    assert load_inputs(tmp_path).pictures == []


def _load_error(job_dir: Path) -> AppError:
    return _error(lambda: load_inputs(job_dir))


def test_missing_inputs_are_not_complete(tmp_path: Path) -> None:
    err = _load_error(tmp_path)
    assert (err.code, err.status) == ("invalid_request", 422)


def test_a_listed_picture_that_is_missing_makes_the_inputs_incomplete(tmp_path: Path) -> None:
    store_inputs(tmp_path, FakeMedia(), _fields(), [io.BytesIO(JPEG_HEAD)])
    (tmp_path / "pictures" / "1.jpg").unlink()
    assert _load_error(tmp_path).code == "invalid_request"


_BASE = {"prompt": "x", "duration_s": 5, "aspect_ratio": "9:16", "resolution": "1080p", "pictures": []}


@pytest.mark.parametrize(
    "document",
    [
        "not json",
        "[]",
        json.dumps({"prompt": "x"}),
        json.dumps({**_BASE, "prompt": ""}),
        json.dumps({**_BASE, "duration_s": "5"}),
        json.dumps({**_BASE, "aspect_ratio": "4:3"}),
        json.dumps({**_BASE, "resolution": "4k"}),
        json.dumps({**_BASE, "pictures": ["../x.jpg"]}),
        json.dumps({**_BASE, "pictures": "a"}),
    ],
)
def test_malformed_inputs_are_rejected(tmp_path: Path, document: str) -> None:
    (tmp_path / "inputs.json").write_text(document, encoding="utf-8")
    assert _load_error(tmp_path).code == "invalid_request"
