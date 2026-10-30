"""In-memory `Transcriber` for tests: no model, no network, no GPU."""

from videokit.core.transcribe import AudioFiles, ProviderStatus, Segment, Transcript

DEFAULT_SEGMENTS = (
    Segment(0.0, 4.0, "Welcome to the hotel tour."),
    Segment(4.0, 12.0, "This is the lobby and the pool."),
)


class FakeTranscriber:
    """Returns a fixed transcript, or raises ``error`` on every call.

    - ``FakeTranscriber()`` -> two English segments, 0.5 audio minutes.
    - ``FakeTranscriber(segments=[...], language="de")`` -> a custom transcript.
    - ``FakeTranscriber(no_speech=True)`` -> empty transcript flagged ``no_speech``.
    - ``FakeTranscriber(name="fake", error=ProviderError("boom"))`` -> always fails (for chain tests).
    - ``FakeTranscriber(name="openai", model="whisper-1")`` -> the transcript reports that model (pricing).

    Every call's input is appended to ``calls``.
    """

    def __init__(
        self,
        *,
        name: str = "fake",
        model: str = "",
        self_hosted: bool | None = None,
        segments: list[Segment] | None = None,
        language: str = "en",
        language_probability: float = 0.99,
        audio_minutes: float = 0.5,
        no_speech: bool = False,
        error: Exception | None = None,
    ) -> None:
        self.name = name
        self.model = model
        # Free by default (a self-hosted server); a named cloud model is billed unless told otherwise.
        self.self_hosted = (not model) if self_hosted is None else self_hosted
        self.segments = [] if no_speech else list(DEFAULT_SEGMENTS if segments is None else segments)
        self.language = language
        self.language_probability = language_probability
        self.audio_minutes = audio_minutes
        self.no_speech = no_speech
        self.error = error
        self.calls: list[AudioFiles] = []

    def check(self) -> ProviderStatus:
        return ProviderStatus(ready=True, detail="fake")

    def transcribe(self, audio: AudioFiles) -> Transcript:
        self.calls.append(audio)
        if self.error is not None:
            raise self.error
        return Transcript(
            language=self.language,
            language_probability=self.language_probability,
            segments=list(self.segments),
            audio_minutes=self.audio_minutes,
            no_speech=self.no_speech,
            provider=self.name,
            model=self.model,
            self_hosted=self.self_hosted,
        )
