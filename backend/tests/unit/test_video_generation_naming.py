import pytest

from videokit.tools.video_generation.naming import download_name


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("Waves at dusk", "Waves at dusk.mp4"),
        ("  spaced\n\n out\tprompt  ", "spaced out prompt.mp4"),
        # exactly 60 characters, then a space: nothing is cut
        ("x" * 60 + " tail", "x" * 60 + ".mp4"),
        # the 60-character window ends inside "abcdef": that partial word is dropped
        (" ".join(["abcdefgh"] * 8), " ".join(["abcdefgh"] * 6) + ".mp4"),
        # the window ends exactly where a word ends: the last whole word stays
        (" ".join(["abcdefghi"] * 7), " ".join(["abcdefghi"] * 6) + ".mp4"),
        # a first word longer than 60 characters is cut hard
        ("x" * 70 + " tail", "x" * 60 + ".mp4"),
        # a trailing dot left by the cut is stripped like any other
        ("x" * 59 + ". tail", "x" * 59 + ".mp4"),
        ("Полёт над горами", "Полёт над горами.mp4"),
        (" ".join(["горы"] * 20), " ".join(["горы"] * 12) + ".mp4"),
        ("Дом/сад «лето»?", "Дом_сад «лето»_.mp4"),
        ('say "hi" / bye', "say _hi_ _ bye.mp4"),
        ("a\\b:c*d<e>f|g", "a_b_c_d_e_f_g.mp4"),
        ("Wait...", "Wait.mp4"),
        ("  .spaced name. ", "spaced name.mp4"),
        ("???", "clip.mp4"),
        ('/\\:*?"<>|', "clip.mp4"),
        ("...", "clip.mp4"),
        ("—!", "clip.mp4"),
        ("   ", "clip.mp4"),
        ("", "clip.mp4"),
    ],
)
def test_download_name(prompt: str, expected: str) -> None:
    assert download_name(prompt) == expected
