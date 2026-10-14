import pytest

from videokit.tools.audio_extract.naming import download_name


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("trip.mp4", "trip.mp3"),
        ("Поездка в горы.final.mov", "Поездка в горы.final.mp3"),
        ("clip", "clip.mp3"),
        ("v1.2 final", "v1.2 final.mp3"),
        ("???.mp4", "audio.mp3"),
        ("___.mov", "audio.mp3"),
        ("a?b.mp4", "a_b.mp3"),
        ("Видео?.mp4", "Видео_.mp3"),
        ("_1_.mp4", "_1_.mp3"),
        (None, "audio.mp3"),
        ("", "audio.mp3"),
        (".mp4", "audio.mp3"),
        (".", "audio.mp3"),
        ("..", "audio.mp3"),
        ("   ", "audio.mp3"),
        ('a/b\\c:d*e?f"g<h>i|j.mkv', "a_b_c_d_e_f_g_h_i_j.mp3"),
        ("bad\x00na\tme.mp4", "bad_na_me.mp3"),
        ("  .spaced name. .mp4", "spaced name.mp3"),
        ("archive.tar.gz", "archive.tar.mp3"),
        ("name.verylongext", "name.verylongext.mp3"),
        ("名前.mp4", "名前.mp3"),
        ("x" * 300 + ".mp4", "x" * 150 + ".mp3"),
        ("x" * 300, "x" * 150 + ".mp3"),
        ("a" * 149 + " " + "b" * 50, "a" * 149 + ".mp3"),
    ],
)
def test_download_name(source: str | None, expected: str) -> None:
    assert download_name(source) == expected
