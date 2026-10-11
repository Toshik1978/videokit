"""Local transcription with faster-whisper (research R7)."""

import importlib
import threading
from typing import Any

from videokit.core.config import Settings, cuda_available
from videokit.core.transcribe.base import AudioFiles, ProviderStatus, Segment, Transcript


def _has_words(text: str) -> bool:
    return any(ch.isalnum() for ch in text)


class LocalWhisperTranscriber:
    """`WHISPER_MODEL` on CUDA (float16) or CPU (int8); the model is downloaded to `DATA_DIR/models` and cached."""

    name = "local"

    def __init__(self, settings: Settings) -> None:
        self.model_name = settings.whisper_model
        self.device_setting = settings.whisper_device
        self.download_root = settings.data_dir / "models"
        self._model: Any = None
        self._loading = False  # read without the lock by `check` (health must not wait for a download)
        self._lock = threading.Lock()

    def _device(self) -> tuple[str, str]:
        if self.device_setting == "cuda" or (self.device_setting == "auto" and cuda_available()):
            return "cuda", "float16"
        return "cpu", "int8"

    def check(self) -> ProviderStatus:
        """Device and model; the model is loaded (and downloaded on first use) by the first transcription."""
        if self.device_setting == "cuda" and not cuda_available():
            return ProviderStatus(ready=False, detail="WHISPER_DEVICE is 'cuda' but no usable GPU was found")
        device, _ = self._device()
        detail = f"device={device}, model={self.model_name}"
        if self._loading:
            return ProviderStatus(ready=False, detail=f"{detail} (downloading or loading)")
        if self._model is None:
            return ProviderStatus(ready=True, detail=f"{detail} (not loaded yet)")
        return ProviderStatus(ready=True, detail=detail)

    def _load(self) -> Any:
        with self._lock:
            if self._model is None:
                self._loading = True
                try:
                    # Imported lazily: faster-whisper pulls in ctranslate2 and is untyped.
                    faster_whisper = importlib.import_module("faster_whisper")
                    device, compute_type = self._device()
                    self._model = faster_whisper.WhisperModel(
                        self.model_name,
                        device=device,
                        compute_type=compute_type,
                        download_root=str(self.download_root),
                    )
                finally:
                    self._loading = False
            return self._model

    def transcribe(self, audio: AudioFiles) -> Transcript:
        raw_segments, info = self._load().transcribe(str(audio.wav), vad_filter=True)
        segments = [Segment(s.start, s.end, s.text.strip()) for s in raw_segments if _has_words(s.text)]
        return Transcript(
            language=info.language,
            language_probability=info.language_probability,
            segments=segments,
            audio_minutes=info.duration / 60,
            no_speech=not segments,
            provider=self.name,
            model=self.model_name,
        )
