"""The provider prompt for Video Generation: picture references prefixed to the user's prompt (research V7)."""

import pytest

from videokit.tools.video_generation.prompt import build_provider_prompt


def test_no_pictures_returns_the_prompt_unchanged() -> None:
    assert build_provider_prompt("A kettle boils on a stove.", 0) == "A kettle boils on a stove."


def test_one_picture_is_declared_as_picture_one() -> None:
    assert build_provider_prompt("The dog runs.", 1) == "[# References <IMAGE_REF_0>@Picture1]\nThe dog runs."


def test_seven_pictures_map_zero_indexed_tags_to_one_based_pictures() -> None:
    expected_declaration = (
        "[# References <IMAGE_REF_0>@Picture1 <IMAGE_REF_1>@Picture2 <IMAGE_REF_2>@Picture3 "
        "<IMAGE_REF_3>@Picture4 <IMAGE_REF_4>@Picture5 <IMAGE_REF_5>@Picture6 <IMAGE_REF_6>@Picture7]"
    )
    assert build_provider_prompt("Pan across the room.", 7) == f"{expected_declaration}\nPan across the room."


def test_non_latin_prompt_is_passed_through_untouched() -> None:
    prompt = "Кошка спит у окна, 猫在窗边睡觉 — 🌧️"
    assert build_provider_prompt(prompt, 0) == prompt
    assert build_provider_prompt(prompt, 2) == (
        "[# References <IMAGE_REF_0>@Picture1 <IMAGE_REF_1>@Picture2]\n" + prompt
    )


def test_negative_picture_count_is_rejected() -> None:
    with pytest.raises(ValueError):
        build_provider_prompt("Anything.", -1)
