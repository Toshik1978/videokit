import re

import pytest

from videokit.core.downloads import content_disposition


@pytest.mark.parametrize(
    ("name", "disposition"),
    [
        ("my trip.mp3", 'attachment; filename="my trip.mp3"'),
        ("Café.mp3", "attachment; filename=\"Cafe.mp3\"; filename*=UTF-8''Caf%C3%A9.mp3"),
        (
            "Поездка.mp3",
            "attachment; filename=\"audio.mp3\"; filename*=UTF-8''%D0%9F%D0%BE%D0%B5%D0%B7%D0%B4%D0%BA%D0%B0.mp3",
        ),
        # `%XX` in a plain `filename` is decoded by some browsers: `filename*` carries the exact name.
        ("50%41 off.mp3", "attachment; filename=\"50%41 off.mp3\"; filename*=UTF-8''50%2541%20off.mp3"),
        ("100%.mp3", "attachment; filename=\"100%.mp3\"; filename*=UTF-8''100%25.mp3"),
        ("clip.mp4", 'attachment; filename="clip.mp4"'),
        ("Café.mp4", "attachment; filename=\"Cafe.mp4\"; filename*=UTF-8''Caf%C3%A9.mp4"),
    ],
)
def test_content_disposition(name: str, disposition: str) -> None:
    assert content_disposition(name) == disposition


_DISPOSITION = re.compile(r"""attachment; filename="([^"\\]*)"; filename\*=UTF-8''[A-Za-z0-9%._~-]+""")


@pytest.mark.parametrize(
    ("name", "fallback"),
    [
        ("a＂; filename=＂evil.exe＂ x.mp3", "a__ filename__evil.exe_ x.mp3"),  # fullwidth quotes
        ("a＼.mp3", "a_.mp3"),  # fullwidth reverse solidus
        ("a﹨b.mp3", "a_b.mp3"),  # small reverse solidus
        ("é／..／z.mp3", "e_.._z.mp3"),  # fullwidth solidus
    ],
)
def test_ascii_fallback_cannot_smuggle_header_syntax(name: str, fallback: str) -> None:
    """NFKD maps fullwidth and small forms back to `"`, `\\` and `/`: the fallback is sanitized again."""
    disposition = content_disposition(name)
    match = _DISPOSITION.fullmatch(disposition)
    assert match is not None, disposition
    assert match.group(1) == fallback
    assert "/" not in match.group(1)
    assert disposition.count("filename=") == 1
    assert disposition.count("filename*=") == 1


@pytest.mark.parametrize(
    ("name", "fallback", "disposition"),
    [
        (
            "Поездка.mp4",
            "clip.mp4",
            "attachment; filename=\"clip.mp4\"; filename*=UTF-8''%D0%9F%D0%BE%D0%B5%D0%B7%D0%B4%D0%BA%D0%B0.mp4",
        ),
        (
            "Café au lait.mp4",
            "clip.mp4",
            "attachment; filename=\"Cafe au lait.mp4\"; filename*=UTF-8''Caf%C3%A9%20au%20lait.mp4",
        ),
        ("my clip.mp4", "clip.mp4", 'attachment; filename="my clip.mp4"'),
    ],
)
def test_content_disposition_takes_the_ascii_fallback_from_the_caller(
    name: str, fallback: str, disposition: str
) -> None:
    assert content_disposition(name, fallback=fallback) == disposition
