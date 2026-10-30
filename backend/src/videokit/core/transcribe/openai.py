"""OpenAI cloud transcription (research R8): `whisper-1` with `verbose_json` and segment timestamps."""

import math
import subprocess
import tempfile
import threading
from pathlib import Path

import openai
from pydantic import SecretStr

from videokit.core.config import Settings, is_openai_api
from videokit.core.transcribe.base import AudioFiles, ProviderError, ProviderStatus, Segment, Transcript

MAX_UPLOAD_BYTES = 24 * 1024 * 1024  # the API rejects uploads above 25 MB
REQUEST_TIMEOUT_S = 1800  # a long upload on a slow or busy server must not be cut off at the SDK default
SELF_HOSTED_PLACEHOLDER_KEY = "not-needed"  # the SDK refuses an empty key; servers without auth ignore it
CHUNK_FILL = 0.9  # chunks are planned at 90 % of the limit to absorb bitrate variation

# `verbose_json` returns the language as an English name ("english"), not an ISO 639-1 code. These are the
# languages Whisper supports; an unknown value yields ``None`` ("language not detected").
_LANGUAGE_CODES = {
    name: code
    for code, name in (
        ("af", "afrikaans"),
        ("am", "amharic"),
        ("ar", "arabic"),
        ("as", "assamese"),
        ("az", "azerbaijani"),
        ("ba", "bashkir"),
        ("be", "belarusian"),
        ("bg", "bulgarian"),
        ("bn", "bengali"),
        ("bo", "tibetan"),
        ("br", "breton"),
        ("bs", "bosnian"),
        ("ca", "catalan"),
        ("cs", "czech"),
        ("cy", "welsh"),
        ("da", "danish"),
        ("de", "german"),
        ("el", "greek"),
        ("en", "english"),
        ("es", "spanish"),
        ("et", "estonian"),
        ("eu", "basque"),
        ("fa", "persian"),
        ("fi", "finnish"),
        ("fo", "faroese"),
        ("fr", "french"),
        ("gl", "galician"),
        ("gu", "gujarati"),
        ("ha", "hausa"),
        ("he", "hebrew"),
        ("hi", "hindi"),
        ("hr", "croatian"),
        ("ht", "haitian creole"),
        ("hu", "hungarian"),
        ("hy", "armenian"),
        ("id", "indonesian"),
        ("is", "icelandic"),
        ("it", "italian"),
        ("ja", "japanese"),
        ("jw", "javanese"),
        ("ka", "georgian"),
        ("kk", "kazakh"),
        ("km", "khmer"),
        ("kn", "kannada"),
        ("ko", "korean"),
        ("la", "latin"),
        ("lb", "luxembourgish"),
        ("ln", "lingala"),
        ("lo", "lao"),
        ("lt", "lithuanian"),
        ("lv", "latvian"),
        ("mg", "malagasy"),
        ("mi", "maori"),
        ("mk", "macedonian"),
        ("ml", "malayalam"),
        ("mn", "mongolian"),
        ("mr", "marathi"),
        ("ms", "malay"),
        ("mt", "maltese"),
        ("my", "myanmar"),
        ("ne", "nepali"),
        ("nl", "dutch"),
        ("nn", "nynorsk"),
        ("no", "norwegian"),
        ("oc", "occitan"),
        ("pa", "punjabi"),
        ("pl", "polish"),
        ("ps", "pashto"),
        ("pt", "portuguese"),
        ("ro", "romanian"),
        ("ru", "russian"),
        ("sa", "sanskrit"),
        ("sd", "sindhi"),
        ("si", "sinhala"),
        ("sk", "slovak"),
        ("sl", "slovenian"),
        ("sn", "shona"),
        ("so", "somali"),
        ("sq", "albanian"),
        ("sr", "serbian"),
        ("su", "sundanese"),
        ("sv", "swedish"),
        ("sw", "swahili"),
        ("ta", "tamil"),
        ("te", "telugu"),
        ("tg", "tajik"),
        ("th", "thai"),
        ("tk", "turkmen"),
        ("tl", "tagalog"),
        ("tr", "turkish"),
        ("tt", "tatar"),
        ("uk", "ukrainian"),
        ("ur", "urdu"),
        ("uz", "uzbek"),
        ("vi", "vietnamese"),
        ("yi", "yiddish"),
        ("yo", "yoruba"),
        ("zh", "chinese"),
    )  # fmt: skip
}
_KNOWN_CODES = frozenset(_LANGUAGE_CODES.values())


def _language_code(value: str | None) -> str | None:
    text = (value or "").strip().lower()
    return text if text in _KNOWN_CODES else _LANGUAGE_CODES.get(text)


def _has_words(text: str) -> bool:
    return any(ch.isalnum() for ch in text)


def plan_chunks(duration_s: float, size_bytes: int, max_bytes: int | None = None) -> list[tuple[float, float]]:
    """``(start, length)`` in seconds of equal-length time chunks, each planned to stay under ``max_bytes``."""
    limit = MAX_UPLOAD_BYTES if max_bytes is None else max_bytes
    bytes_per_second = size_bytes / duration_s
    length = max(1.0, math.floor(limit * CHUNK_FILL / bytes_per_second))
    count = math.ceil(duration_s / length)
    return [(i * length, min(length, duration_s - i * length)) for i in range(count)]


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, check=True, capture_output=True, text=True, errors="replace").stdout
    except (subprocess.CalledProcessError, OSError) as exc:
        raise ProviderError("Could not split the audio into upload chunks") from exc


def _duration(path: Path) -> float:
    out = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)])
    try:
        duration = float(out.strip())
    except ValueError as exc:
        raise ProviderError("Could not split the audio into upload chunks") from exc
    if duration <= 0:
        raise ProviderError("Could not split the audio into upload chunks")
    return duration


def _secret(value: SecretStr | None) -> str | None:
    text = value.get_secret_value().strip() if value is not None else ""
    return text or None


class OpenAITranscriber:
    """Uploads ``audio.mp3`` (split into time chunks above the upload limit) and restores the segment offsets."""

    name = "openai"

    def __init__(self, settings: Settings, client: openai.OpenAI | None = None) -> None:
        self.model = settings.openai_transcribe_model
        self._url = (settings.openai_transcribe_url or "").strip()
        self._self_hosted = not is_openai_api(self._url)
        self._transcribe_key = _secret(settings.openai_transcribe_api_key)
        self._openai_key = _secret(settings.openai_api_key)
        self._client = client
        self._lock = threading.Lock()

    def check(self) -> ProviderStatus:
        if self._self_hosted:
            return ProviderStatus(ready=True, detail=f"model={self.model}, self-hosted")
        if not (self._transcribe_key or self._openai_key):
            return ProviderStatus(ready=False, detail="missing OPENAI_API_KEY")
        return ProviderStatus(ready=True, detail=f"model={self.model}")

    def _sdk(self) -> openai.OpenAI:
        with self._lock:
            if self._client is None:
                if self._self_hosted:  # never send the operator's OpenAI key to another host
                    key = self._transcribe_key or SELF_HOSTED_PLACEHOLDER_KEY
                else:
                    key = self._transcribe_key or self._openai_key or ""
                # SDK default retries handle 429/5xx
                if self._url:
                    self._client = openai.OpenAI(api_key=key, base_url=self._url, timeout=REQUEST_TIMEOUT_S)
                else:
                    self._client = openai.OpenAI(api_key=key, timeout=REQUEST_TIMEOUT_S)
            return self._client

    def _upload(self, path: Path) -> tuple[list[Segment], float, str | None]:
        """One request: ``(segments, duration_seconds, language_name)``; segment times are relative to the file."""
        try:
            with path.open("rb") as handle:
                result = self._sdk().audio.transcriptions.create(
                    model=self.model,
                    file=handle,
                    response_format="verbose_json",
                    timestamp_granularities=["segment"],
                )
        except openai.OpenAIError as exc:
            raise ProviderError(f"OpenAI transcription failed ({type(exc).__name__})") from exc
        segments = [Segment(s.start, s.end, s.text.strip()) for s in result.segments or [] if _has_words(s.text)]
        return segments, result.duration, result.language

    def transcribe(self, audio: AudioFiles) -> Transcript:
        size = audio.mp3.stat().st_size
        parts: list[tuple[list[Segment], float, str | None]]
        offsets: list[float]
        if size <= MAX_UPLOAD_BYTES:
            parts, offsets = [self._upload(audio.mp3)], [0.0]
        else:
            plan = plan_chunks(_duration(audio.mp3), size)
            offsets = [start for start, _ in plan]
            parts = []
            with tempfile.TemporaryDirectory(dir=audio.mp3.parent) as tmp:
                for i, (start, length) in enumerate(plan):
                    chunk = Path(tmp) / f"chunk-{i:04d}.mp3"
                    _run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.3f}", "-t", f"{length:.3f}"]
                         + ["-i", str(audio.mp3), "-c", "copy", str(chunk)])  # fmt: skip
                    parts.append(self._upload(chunk))
                    chunk.unlink()

        segments = [
            Segment(s.start + offset, s.end + offset, s.text)
            for (segs, _, _), offset in zip(parts, offsets, strict=True)
            for s in segs
        ]
        language = next((code for _, _, name in parts if (code := _language_code(name))), None)
        return Transcript(
            language=language,
            language_probability=1.0 if language else 0.0,  # the API reports no confidence
            segments=segments,
            audio_minutes=sum(duration for _, duration, _ in parts) / 60,
            no_speech=not segments,
            provider=self.name,
            model=self.model,
            self_hosted=self._self_hosted,
        )
