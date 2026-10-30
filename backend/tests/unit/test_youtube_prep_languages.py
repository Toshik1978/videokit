import json
from pathlib import Path

import pytest

from videokit.tools.youtube_prep.languages import (
    AUTO,
    LANGUAGE_JSON,
    LANGUAGES,
    LanguageSetting,
    instruction,
    normalize_fallback,
    read_setting,
    write_setting,
)

RESEARCH_L2 = (
    "af ar az be bg bn ca cs cy da de el en es et eu fa fi fr gl gu he hi hr hu hy id is it ja ka kk kn ko "
    "lt lv mk ml mr ms nl no pa pl pt ro ru sk sl sr sv sw ta te th tl tr uk ur vi zh"
).split()


def test_table_has_exactly_the_research_codes() -> None:
    assert len(RESEARCH_L2) == len(set(RESEARCH_L2))
    assert set(LANGUAGES) == set(RESEARCH_L2)
    for code, name in LANGUAGES.items():
        assert len(code) == 2 and code.isascii() and code.islower() and code.isalpha()
        assert name.strip() == name and name


def test_constants_and_default() -> None:
    assert AUTO == "auto"
    assert LANGUAGE_JSON == "language.json"
    assert LanguageSetting() == LanguageSetting(choice="auto", fallback="en")


def test_missing_file_gives_defaults(tmp_path: Path) -> None:
    assert read_setting(tmp_path) == LanguageSetting()


def test_round_trip_leaves_no_temp_file(tmp_path: Path) -> None:
    setting = LanguageSetting(choice="de", fallback="pt")
    write_setting(tmp_path, setting)
    assert read_setting(tmp_path) == setting
    assert [p.name for p in tmp_path.iterdir()] == [LANGUAGE_JSON]


def test_write_overwrites(tmp_path: Path) -> None:
    write_setting(tmp_path, LanguageSetting(choice="de"))
    write_setting(tmp_path, LanguageSetting(choice=AUTO, fallback="fr"))
    assert read_setting(tmp_path) == LanguageSetting(choice=AUTO, fallback="fr")


@pytest.mark.parametrize(
    "data",
    [
        {"choice": "xx", "fallback": "en"},
        {"choice": "auto", "fallback": "auto"},
        {"choice": "auto", "fallback": "xx"},
        {"choice": "DE", "fallback": "en"},
    ],
)
def test_invalid_file_raises(tmp_path: Path, data: dict[str, str]) -> None:
    (tmp_path / LANGUAGE_JSON).write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        read_setting(tmp_path)


def test_malformed_file_raises(tmp_path: Path) -> None:
    (tmp_path / LANGUAGE_JSON).write_text("{nope", encoding="utf-8")
    with pytest.raises(ValueError):
        read_setting(tmp_path)


def test_instruction_text() -> None:
    assert instruction("de") == "Write all output in German (language code de)."
    assert instruction("no") == "Write all output in Norwegian (language code no)."


def test_instruction_unknown_code_uses_code_as_name() -> None:
    assert instruction("haw") == "Write all output in haw (language code haw)."


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("pt-BR", "pt"),
        ("PT_br", "pt"),
        (" de ", "de"),
        ("zh-Hant-TW", "zh"),
        ("xx", "en"),
        ("auto", "en"),
        ("", "en"),
        ("   ", "en"),
        ("-", "en"),
        (None, "en"),
    ],
)
def test_normalize_fallback(value: str | None, expected: str) -> None:
    assert normalize_fallback(value) == expected
