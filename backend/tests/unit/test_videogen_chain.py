"""`VideoChain`: fallback only before acceptance, routing by provider, log hygiene (research V3, V10)."""

import logging
import subprocess
from pathlib import Path

import pytest

from fakes_videogen import FakeVideoGenerator
from videokit.core.videogen import (
    Accepted,
    Capabilities,
    Picture,
    PollResult,
    ProviderStatus,
    Usage,
    VideoChain,
    VideoError,
    VideoGenerator,
    VideoRequest,
)

REQUEST = VideoRequest(
    prompt="a slow pan across a lake",
    pictures=[Picture(position=1, jpeg=b"\xff\xd8jpeg")],
    duration_s=5,
    aspect_ratio="9:16",
    resolution="720p",
)


def test_fake_satisfies_the_protocol() -> None:
    assert isinstance(FakeVideoGenerator(), VideoGenerator)


def test_fake_reports_usage_at_the_documented_rate_of_its_request(tmp_path: Path) -> None:
    fake = FakeVideoGenerator()
    fake.submit(REQUEST)  # 5 s at 720p, one picture
    assert fake.poll("op-1", tmp_path).usage == Usage(input_tokens=1120, video_output_tokens=5 * 5792)

    fake = FakeVideoGenerator()
    fake.submit(
        VideoRequest(
            prompt="p",
            pictures=[Picture(position=1, jpeg=b"a"), Picture(position=2, jpeg=b"b")],
            duration_s=8,
            aspect_ratio="16:9",
            resolution="1080p",
        )
    )
    assert fake.poll("op-1", tmp_path).usage == Usage(input_tokens=2 * 1120, video_output_tokens=8 * 8688)


def test_chain_requires_a_provider() -> None:
    with pytest.raises(ValueError, match="at least one"):
        VideoChain([])


def test_submit_names_the_accepting_provider_and_its_ref() -> None:
    first = FakeVideoGenerator(name="a", model="m1", submits=["op-1"])
    accepted = VideoChain([first]).submit(REQUEST)
    assert accepted == Accepted(provider="a:m1", ref="op-1")
    assert first.submit_count == 1


@pytest.mark.parametrize("code", ["provider_out_of_credit", "provider_unavailable", "video_failed"])
def test_submit_falls_back_when_a_provider_raised_before_accepting(code: str) -> None:
    first = FakeVideoGenerator(name="a", model="m1", submits=[VideoError(code)])  # type: ignore[arg-type]
    second = FakeVideoGenerator(name="b", model="m2", submits=["op-2"])
    accepted = VideoChain([first, second]).submit(REQUEST)
    assert accepted == Accepted(provider="b:m2", ref="op-2")
    assert (first.submit_count, second.submit_count) == (1, 1)


def test_submit_treats_an_unexpected_provider_exception_as_unavailable() -> None:
    first = FakeVideoGenerator(name="a", submits=[RuntimeError("boom")])
    second = FakeVideoGenerator(name="b", submits=["op-2"])
    assert VideoChain([first, second]).submit(REQUEST).provider == "b:fake-model"


@pytest.mark.parametrize("code", ["content_blocked", "invalid_picture"])
def test_submit_stops_the_chain_on_content_blocked_and_invalid_picture(code: str) -> None:
    first = FakeVideoGenerator(name="a", submits=[VideoError(code)])  # type: ignore[arg-type]
    second = FakeVideoGenerator(name="b", submits=["op-2"])
    with pytest.raises(VideoError) as raised:
        VideoChain([first, second]).submit(REQUEST)
    assert raised.value.code == code
    assert second.submit_count == 0


def test_submit_raises_the_last_error_when_none_accepted() -> None:
    first = FakeVideoGenerator(name="a", submits=[VideoError("provider_out_of_credit")])
    second = FakeVideoGenerator(name="b", submits=[VideoError("provider_unavailable")])
    with pytest.raises(VideoError) as raised:
        VideoChain([first, second]).submit(REQUEST)
    assert raised.value.code == "provider_unavailable"


def test_no_fallback_after_acceptance(tmp_path: Path) -> None:
    first = FakeVideoGenerator(name="a", submits=["op-1"], polls=[VideoError("video_failed")])
    second = FakeVideoGenerator(name="b", submits=["op-2"])
    chain = VideoChain([first, second])
    accepted = chain.submit(REQUEST)
    with pytest.raises(VideoError) as raised:
        chain.poll(accepted, tmp_path)
    assert raised.value.code == "video_failed"
    assert second.submit_count == 0
    assert second.poll_refs == []


def test_poll_cancel_and_forget_route_to_the_accepting_provider_only(tmp_path: Path) -> None:
    first = FakeVideoGenerator(name="a", model="m1", submits=[VideoError("provider_unavailable")])
    second = FakeVideoGenerator(name="b", model="m2", submits=["op-2"], polls=["pending", "done"])
    third = FakeVideoGenerator(name="c", model="m3")
    chain = VideoChain([first, second, third])
    accepted = chain.submit(REQUEST)

    assert chain.poll(accepted, tmp_path).done is False
    result = chain.poll(accepted, tmp_path)
    assert result.done is True
    assert result.video is not None
    assert result.video.is_file()
    chain.cancel(accepted)
    chain.forget(accepted)

    assert second.poll_refs == ["op-2", "op-2"]
    assert second.cancelled == ["op-2"]
    assert second.forgotten == ["op-2"]
    for other in (first, third):
        assert (other.poll_refs, other.cancelled, other.forgotten) == ([], [], [])


def test_the_provider_is_matched_by_name_and_model(tmp_path: Path) -> None:
    old = FakeVideoGenerator(name="a", model="old")
    new = FakeVideoGenerator(name="a", model="new", polls=["pending"])
    chain = VideoChain([old, new])
    chain.poll(Accepted(provider="a:new", ref="op-9"), tmp_path)
    assert (old.poll_refs, new.poll_refs) == ([], ["op-9"])


def test_an_unknown_provider_is_a_video_failure_not_a_reroute(tmp_path: Path) -> None:
    only = FakeVideoGenerator(name="a")
    chain = VideoChain([only])
    gone = Accepted(provider="gone:model", ref="op-1")
    with pytest.raises(VideoError) as raised:
        chain.poll(gone, tmp_path)
    assert raised.value.code == "video_failed"
    assert only.poll_refs == []
    # best-effort operations on an unknown provider do nothing
    chain.cancel(gone)
    chain.forget(gone)
    assert (only.cancelled, only.forgotten) == ([], [])


def test_poll_passes_the_provider_result_through(tmp_path: Path) -> None:
    usage = Usage(input_tokens=3, output_tokens=4)
    scripted = PollResult(done=True, video=None, usage=usage, error=VideoError("content_blocked"))
    chain = VideoChain([FakeVideoGenerator(polls=[scripted])])
    assert chain.poll(Accepted(provider="fake:fake-model", ref="op-1"), tmp_path) is scripted


def test_best_effort_operations_swallow_provider_errors() -> None:
    class Broken(FakeVideoGenerator):
        def cancel(self, ref: str) -> None:
            raise RuntimeError("nope")

        def forget(self, ref: str) -> None:
            raise RuntimeError("nope")

    chain = VideoChain([Broken()])
    accepted = Accepted(provider="fake:fake-model", ref="op-1")
    chain.cancel(accepted)
    chain.forget(accepted)


def test_check_is_ready_when_any_provider_is_ready() -> None:
    class NotReady(FakeVideoGenerator):
        def check(self) -> ProviderStatus:
            return ProviderStatus(ready=False, detail="missing key")

    status = VideoChain([NotReady(name="a"), FakeVideoGenerator(name="b")]).check()
    assert status.ready is True
    assert "a: missing key" in status.detail
    assert "b: " in status.detail


def test_capabilities_come_from_the_first_provider() -> None:
    caps = Capabilities(
        durations_s=[5, 6],
        max_pictures=2,
        video_tokens_per_second={"720p": 1, "1080p": 2},
        tokens_per_image=3,
    )
    chain = VideoChain([FakeVideoGenerator(name="a", caps=caps), FakeVideoGenerator(name="b")])
    assert chain.capabilities() == caps


def test_logs_carry_exception_types_and_codes_only(caplog: pytest.LogCaptureFixture) -> None:
    marker = "SECRET-PROMPT-ECHO"
    first = FakeVideoGenerator(name="a", submits=[VideoError("provider_unavailable", marker)])
    second = FakeVideoGenerator(name="b", submits=[RuntimeError(marker)])
    third = FakeVideoGenerator(name="c", submits=["op-3"])
    with caplog.at_level(logging.DEBUG):
        VideoChain([first, second, third]).submit(REQUEST)
    assert [r for r in caplog.records if r.name == "videokit"]
    assert marker not in caplog.text
    assert not any(r.exc_info or r.exc_text or r.stack_info for r in caplog.records)
    assert "VideoError" in caplog.text
    assert "RuntimeError" in caplog.text


def test_fake_clip_is_h264_with_aac(tmp_path: Path) -> None:
    fake = FakeVideoGenerator(polls=["done"])
    result = fake.poll(fake.submit(REQUEST), tmp_path)
    assert result.video is not None
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(result.video)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert sorted(probe.stdout.split()) == ["aac", "h264"]
