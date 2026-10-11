"""Video stream end from ffprobe JSON: the bound for frame times (no ffmpeg needed)."""

from typing import Any

import pytest

from videokit.core.media import _video_end

FORMAT: dict[str, Any] = {"start_time": "0.000000", "duration": "10.000000"}


@pytest.mark.parametrize(
    ("video", "container", "expected"),
    [
        ({"start_time": "0.000000", "duration": "4.000000"}, FORMAT, 4.0),
        ({"start_time": "0.000000", "tags": {"DURATION": "00:00:04.500000000"}}, FORMAT, 4.5),
        ({"tags": {"duration": "0:00:06.5"}}, {"duration": "10.0"}, 6.5),  # no start times at all
        # Times are relative to the container start, which can precede the video stream.
        ({"start_time": "0.000000", "duration": "2.0"}, {"start_time": "-0.025000"}, 2.025),
    ],
)
def test_video_end_from_stream_duration_or_tag(
    video: dict[str, Any], container: dict[str, Any], expected: float
) -> None:
    assert _video_end(video, container, 10.0) == pytest.approx(expected)


@pytest.mark.parametrize(
    "video",
    [
        {},  # no duration at all (e.g. a live-recorded WebM)
        {"tags": {"DURATION": "unknown"}},
        {"duration": "N/A"},
        {"start_time": "0.0", "duration": None, "tags": None},
        {"start_time": "0.0", "duration": "12.0"},  # longer than the container: not trusted
        {"start_time": "0.0", "duration": "0.0"},
    ],
)
def test_video_end_falls_back_to_the_container_duration(video: dict[str, Any]) -> None:
    assert _video_end(video, FORMAT, 10.0) == 10.0
