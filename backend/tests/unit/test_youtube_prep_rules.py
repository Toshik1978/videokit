"""Tests for the YouTube Prep output rules (normalize -> validate -> render)."""

from typing import Any

import pytest

from videokit.tools.youtube_prep.rules import (
    SECTIONS,
    Violation,
    format_time,
    normalize,
    normalize_section,
    notices,
    render,
    validate,
    validate_section,
)

DURATION = 600.0


def chapters(*pairs: tuple[float, str]) -> list[dict[str, Any]]:
    return [{"start_seconds": s, "title": t} for s, t in pairs]


def good(**over: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "language": "en",
        "cover_titles": ["Hidden Gem Resort", "Worth The Price?", "Best Pool Ever"],
        "title": "A great hotel review",
        "description": "Paragraph one.\n\nParagraph two.",
        "tags": ["hotel review", "travel"],
        "hashtags": ["#travel", "#hotel", "#review"],
        "chapters": chapters((0, "Arrival"), (60, "Room"), (120, "Pool")),
    }
    result.update(over)
    return result


def test_good_result_has_no_violations() -> None:
    assert validate(good(), duration_s=DURATION) == []


# ---------------------------------------------------------------- normalize


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (["  A  B ", "C\t\nD", " x"], ["A B", "C D", "x"]),
        (["Привет   мир", "你好 世界", "ok"], ["Привет мир", "你好 世界", "ok"]),
    ],
)
def test_normalize_cover_titles(raw: list[str], expected: list[str]) -> None:
    assert normalize_section("cover_titles", raw, duration_s=DURATION) == expected


def test_normalize_title_trims_only() -> None:
    assert normalize_section("title", "  Hello   world \n", duration_s=DURATION) == "Hello   world"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  One.\n\nTwo.  ", "One.\n\nTwo."),
        ("One.\r\n\r\nTwo.", "One.\n\nTwo."),
        ("One.\n\n\n\nTwo.", "One.\n\nTwo."),
        ("One.\n  \nTwo.", "One.\n\nTwo."),
        ("One.\n\n   \n\nTwo.\n\nThree.", "One.\n\nTwo.\n\nThree."),
        (" One.  \n\n  Two. ", "One.\n\nTwo."),
        ("Line a\nline b\n\nTwo.", "Line a\nline b\n\nTwo."),
    ],
)
def test_normalize_description(raw: str, expected: str) -> None:
    assert normalize_section("description", raw, duration_s=DURATION) == expected


def test_normalize_tags_trim_dedupe() -> None:
    raw = ["  a ", "b", "A", "", "  ", "b", "c"]
    assert normalize_section("tags", raw, duration_s=DURATION) == ["a", "b", "c"]


@pytest.mark.parametrize(
    ("n_tags", "tag_len", "kept"),
    [(5, 98, 5), (5, 99, 4), (3, 165, 3), (3, 166, 2), (1, 400, 1)],
)
def test_normalize_tags_drops_trailing_until_500(n_tags: int, tag_len: int, kept: int) -> None:
    tags = [chr(97 + i) * tag_len for i in range(n_tags)]
    out = normalize_section("tags", tags, duration_s=DURATION)
    assert out == tags[:kept]
    assert len(", ".join(out)) <= 500


def test_normalize_tags_exactly_500_kept() -> None:
    tags = ["a" * 249, "b" * 249]  # 249 + 2 + 249 = 500
    assert normalize_section("tags", tags, duration_s=DURATION) == tags


def test_normalize_tags_501_drops_last() -> None:
    tags = ["a" * 250, "b" * 249]
    assert normalize_section("tags", tags, duration_s=DURATION) == tags[:1]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (["travel", "#hotel", "##x"], ["#travel", "#hotel", "#x"]),
        (["# travel tips", "hotel review"], ["#traveltips", "#hotelreview"]),
        (["#a", "#A", "a", "#b"], ["#a", "#b"]),
        (["#1", "#2", "#3", "#4", "#5", "#6", "#7"], ["#1", "#2", "#3", "#4", "#5"]),
        (["#", " ", "#ok"], ["#ok"]),
        (["#путешествие", "отель"], ["#путешествие", "#отель"]),
        (["#a", "#a", "#b", "#c", "#d", "#e", "#f"], ["#a", "#b", "#c", "#d", "#e"]),
    ],
)
def test_normalize_hashtags(raw: list[str], expected: list[str]) -> None:
    assert normalize_section("hashtags", raw, duration_s=DURATION) == expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Arrival and lobby", "Arrival and lobby"),
        ('Say "hello" now', "Say hello now"),
        ("“Quoted” „word“", "Quoted word"),
        ("Check-in", "Check in"),
        ("Pool - bar", "Pool bar"),
        ("Pool – bar — end", "Pool bar end"),
        ("\U0001f3ca Pool \U0001f334", "Pool"),
        ("Pool✨", "Pool"),
        ("❤️ Love", "Love"),
        ("\U0001f468‍\U0001f469‍\U0001f467 Family", "Family"),
        ("\U0001f44d\U0001f3fd Nice", "Nice"),
        ("1️⃣ First", "1 First"),
        ("→ Next ←", "Next"),
        ("• Bullet ▪", "Bullet"),
        ("★ Star ☆", "Star"),
        ("   Many    spaces   here ", "Many spaces here"),
        ("Привет - мир \U0001f600", "Привет мир"),
        ("你好　世界", "你好 世界"),
        ("C++ and 3 > 2?", "C++ and 3 > 2?"),
        ("Rock & roll, (live): 100%!", "Rock & roll, (live): 100%!"),
        ("\U0001f600 - —", ""),
    ],
)
def test_normalize_chapter_title(title: str, expected: str) -> None:
    out = normalize_section("chapters", chapters((0, title)), duration_s=DURATION)
    assert out == [{"start_seconds": 0, "title": expected}]


def test_normalize_chapters_keeps_order_and_times() -> None:
    raw = chapters((50, "B"), (0.0, "A"))
    out = normalize_section("chapters", raw, duration_s=DURATION)
    assert [c["title"] for c in out] == ["B", "A"]
    assert [c["start_seconds"] for c in out] == [50, 0.0]


@pytest.mark.parametrize("duration", [0.0, 10.0, 30.0, 44.999])
def test_normalize_chapters_forced_empty_when_short(duration: float) -> None:
    assert normalize_section("chapters", good()["chapters"], duration_s=duration) == []


def test_normalize_chapters_kept_at_45() -> None:
    assert len(normalize_section("chapters", good()["chapters"], duration_s=45.0)) == 3


def test_normalize_full_result_keeps_other_keys_and_skips_missing() -> None:
    out = normalize(good(title="  T  ", extra="x"), duration_s=DURATION)
    assert out["title"] == "T"
    assert out["language"] == "en"
    assert out["extra"] == "x"
    assert normalize({"title": " a "}, duration_s=DURATION) == {"title": "a"}


def test_normalize_does_not_mutate_input() -> None:
    raw = good(title="  T ", tags=[" a ", "a"])
    normalize(raw, duration_s=DURATION)
    assert raw["title"] == "  T "
    assert raw["tags"] == [" a ", "a"]


def test_normalize_unknown_section_raises() -> None:
    with pytest.raises(KeyError):
        normalize_section("nope", "x", duration_s=DURATION)


# ----------------------------------------------------------------- notices


@pytest.mark.parametrize(
    ("duration", "expected"),
    [(0.0, ["too_short_for_chapters"]), (44.9, ["too_short_for_chapters"]), (45.0, []), (600.0, [])],
)
def test_notices(duration: float, expected: list[str]) -> None:
    assert notices(duration_s=duration) == expected


# ---------------------------------------------------------------- validate


@pytest.mark.parametrize(
    ("titles", "violates"),
    [
        (["a", "b", "c"], False),
        (["a b c d e", "a b c d e", "a"], False),  # exactly 5 words
        (["a b c d e f", "b", "c"], True),
        (["a", "b"], True),
        (["a", "b", "c", "d"], True),
        ([], True),
        (["Один два три четыре пять", "b", "c"], False),
        (["Один два три четыре пять шесть", "b", "c"], True),
    ],
)
def test_validate_cover_titles(titles: list[str], violates: bool) -> None:
    v = validate_section("cover_titles", titles, duration_s=DURATION)
    assert bool(v) is violates
    assert all(x.section == "cover_titles" and x.message for x in v)


@pytest.mark.parametrize(("length", "violates"), [(0, False), (99, False), (100, False), (101, True)])
def test_validate_title(length: int, violates: bool) -> None:
    assert bool(validate_section("title", "x" * length, duration_s=DURATION)) is violates


def test_validate_title_counts_characters_not_bytes() -> None:
    assert validate_section("title", "я" * 100, duration_s=DURATION) == []
    assert validate_section("title", "я" * 101, duration_s=DURATION)


@pytest.mark.parametrize(
    ("n_paragraphs", "violates"),
    [(0, True), (1, True), (2, False), (3, False), (4, False), (5, True)],
)
def test_validate_description_paragraphs(n_paragraphs: int, violates: bool) -> None:
    text = "\n\n".join(f"Paragraph {i}." for i in range(n_paragraphs))
    assert bool(validate_section("description", text, duration_s=DURATION)) is violates


@pytest.mark.parametrize(("length", "violates"), [(5000, False), (5001, True)])
def test_validate_description_length(length: int, violates: bool) -> None:
    half = (length - 2) // 2
    text = "a" * half + "\n\n" + "b" * (length - 2 - half)
    assert len(text) == length
    assert bool(validate_section("description", text, duration_s=DURATION)) is violates


@pytest.mark.parametrize(
    "line",
    ["0:00 Intro", "12:34 Something", "1:02:03 Long", "- 0:00 Intro", "  5:07 - x", "00:15 start"],
)
def test_validate_description_timestamp_lines(line: str) -> None:
    text = f"First paragraph.\n\n{line}\n0:30 More"
    v = validate_section("description", text, duration_s=DURATION)
    assert any("timestamp" in x.message for x in v)


@pytest.mark.parametrize(
    "text",
    ["Ratio 3:2 is common.\n\nSecond.", "Meet at 10:30 for lunch.\n\nSecond.", "Version 1.2:3\n\nSecond."],
)
def test_validate_description_inline_times_are_fine(text: str) -> None:
    assert validate_section("description", text, duration_s=DURATION) == []


def test_validate_tags() -> None:
    assert validate_section("tags", [], duration_s=DURATION)[0].section == "tags"
    assert validate_section("tags", ["a"], duration_s=DURATION) == []


@pytest.mark.parametrize(("n", "violates"), [(0, True), (2, True), (3, False), (5, False)])
def test_validate_hashtags(n: int, violates: bool) -> None:
    tags = [f"#t{i}" for i in range(n)]
    assert bool(validate_section("hashtags", tags, duration_s=DURATION)) is violates


@pytest.mark.parametrize(
    ("items", "needle"),
    [
        (chapters((1, "a"), (30, "b"), (60, "c")), "first"),
        (chapters((0, "a"), (30, "b")), "at least 3"),
        ([], "at least 3"),
        (chapters((0, "a"), (9, "b"), (60, "c")), "10"),
        (chapters((0, "a"), (30, "b"), (30, "c")), "ascending"),
        (chapters((0, "a"), (60, "b"), (30, "c")), "ascending"),
        (chapters((0, "a"), (30, "b"), (DURATION, "c")), "duration"),
        (chapters((0, "a"), (30, "b"), (DURATION + 5, "c")), "duration"),
        (chapters((0, "a"), (30, "b"), (DURATION - 9.9, "c")), "before the end"),
        (chapters((0, "a"), (30, ""), (60, "c")), "empty"),
        (chapters((0, "a"), (30, "b"), (60, "")), "empty"),
    ],
)
def test_validate_chapters_violations(items: list[dict[str, Any]], needle: str) -> None:
    v = validate_section("chapters", items, duration_s=DURATION)
    assert v
    assert any(needle in x.message for x in v)
    assert all(x.section == "chapters" for x in v)


@pytest.mark.parametrize(
    "items",
    [
        chapters((0, "a"), (10, "b"), (20, "c")),  # gaps exactly 10 s
        chapters((0, "a"), (30, "b"), (DURATION - 10, "c")),  # last chapter exactly 10 s before the end
        chapters((0, "a"), (30, "b"), (60, "c"), (90, "d")),
    ],
)
def test_validate_chapters_ok(items: list[dict[str, Any]]) -> None:
    assert validate_section("chapters", items, duration_s=DURATION) == []


def test_validate_chapters_gap_just_under_10_violates() -> None:
    assert validate_section("chapters", chapters((0, "a"), (9.99, "b"), (30, "c")), duration_s=DURATION)


def test_validate_chapters_short_video_empty_is_fine() -> None:
    assert validate_section("chapters", [], duration_s=29.9) == []
    assert validate(good(chapters=[]), duration_s=29.9) == []


def test_validate_chapters_short_video_nonempty_flagged() -> None:
    assert validate_section("chapters", good()["chapters"], duration_s=29.9)


def test_validate_chapters_exactly_at_duration() -> None:
    items = chapters((0, "a"), (20, "b"), (40, "c"))
    assert validate_section("chapters", items, duration_s=40.0)
    assert validate_section("chapters", items, duration_s=50.0) == []


def test_validate_last_chapter_needs_10_s_before_the_end() -> None:
    # YouTube disables chapters when the last one is shorter than 10 s.
    items = chapters((0, "a"), (20, "b"), (40, "c"))
    [violation] = validate_section("chapters", items, duration_s=49.99)
    assert violation.message == "the last chapter starts only 9.99 s before the end; at least 10 s"
    assert validate_section("chapters", items, duration_s=50.0) == []


def test_three_chapters_fit_the_shortest_video_with_chapters() -> None:
    assert validate_section("chapters", chapters((0, "a"), (10, "b"), (20, "c")), duration_s=45.0) == []


def test_validate_collects_all_sections_in_order() -> None:
    bad = good(
        cover_titles=["a"],
        title="x" * 101,
        description="one",
        tags=[],
        hashtags=["#a"],
        chapters=[],
    )
    assert [v.section for v in validate(bad, duration_s=DURATION)] == list(SECTIONS)


def test_validate_skips_missing_sections() -> None:
    v = validate({"title": "x" * 101}, duration_s=DURATION)
    assert [x.section for x in v] == ["title"]


def test_validate_unknown_section_raises() -> None:
    with pytest.raises(KeyError):
        validate_section("nope", "x", duration_s=DURATION)


def test_violation_str_is_human_readable() -> None:
    assert str(Violation("title", "too long")) == "title: too long"


# ------------------------------------------------------------------ render


@pytest.mark.parametrize(
    ("seconds", "text"),
    [
        (0, "0:00"),
        (5, "0:05"),
        (59, "0:59"),
        (60, "1:00"),
        (102, "1:42"),
        (599, "9:59"),
        (600, "10:00"),
        (3599, "59:59"),
        (3600, "1:00:00"),
        (3661, "1:01:01"),
        (36000, "10:00:00"),
        (59.9, "0:59"),
        (3599.9, "59:59"),
    ],
)
def test_format_time(seconds: float, text: str) -> None:
    assert format_time(seconds) == text


def test_render() -> None:
    result = good(chapters=chapters((0, "Arrival and lobby"), (102, "Standard room"), (3600, "Late")))
    assert render(result) == {
        "cover_titles": "Hidden Gem Resort\nWorth The Price?\nBest Pool Ever",
        "title": "A great hotel review",
        "description": "Paragraph one.\n\nParagraph two.",
        "tags": "hotel review, travel",
        "hashtags": "#travel #hotel #review",
        "chapters": '0:00 "Arrival and lobby"\n1:42 "Standard room"\n1:00:00 "Late"',
    }


def test_render_empty_chapters() -> None:
    assert render(good(chapters=[]))["chapters"] == ""


def test_render_skips_missing_sections() -> None:
    assert render({"title": "x"}) == {"title": "x"}


@pytest.mark.parametrize(
    "title",
    ["می‌خواهم", "किंतु", "café", "한국어 여행", "العربية"],
)
def test_normalize_chapter_title_keeps_script_specific_marks(title: str) -> None:
    out = normalize_section("chapters", chapters((0, title)), duration_s=DURATION)
    assert out[0]["title"] == title


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("~ Intro ~", "Intro"),
        ("Pool | Bar", "Pool Bar"),
        ("*** Breakfast ***", "Breakfast"),
        ("==> Check out <==", "Check out"),
        ("# Rooftop", "Rooftop"),
        ("Lobby :: Tour", "Lobby Tour"),
        ("[Bonus] Spa", "Bonus Spa"),
        ("Room > View", "Room View"),
        ("{Gym} ^ Sauna", "Gym Sauna"),
        ("`Code` snake_case", "Code snake case"),
        ("\\ Backslash /", "Backslash"),
        (": Leading and trailing ;", "Leading and trailing"),
        ("Pool / Bar", "Pool Bar"),
        ("|||", ""),
    ],
)
def test_normalize_chapter_title_strips_decorative_ascii(title: str, expected: str) -> None:
    out = normalize_section("chapters", chapters((0, title)), duration_s=DURATION)
    assert out[0]["title"] == expected


@pytest.mark.parametrize(
    "title",
    [
        "C++ tips",
        "C# tips",
        "Q&A",
        "50% off",
        "Room 2.0",
        "Don't miss",
        "Café",
        "Привет мир",
        "東京タワー",
        "Hôtel's pool",
        "Step 1: arrival",
        "AC/DC live",
        "3 > 2 and 1 = 1",
        "Price: $5, really?",
        "Hello (world)!",
    ],
)
def test_normalize_chapter_title_keeps_meaningful_characters(title: str) -> None:
    out = normalize_section("chapters", chapters((0, title)), duration_s=DURATION)
    assert out[0]["title"] == title


def test_normalize_chapter_title_wifi_dash_becomes_space() -> None:
    out = normalize_section("chapters", chapters((0, "Wi-Fi")), duration_s=DURATION)
    assert out[0]["title"] == "Wi Fi"
