# Internal Interfaces: Remote Transcription Server (delta)

Base contract: `specs/001-videokit-youtube-prep/contracts/internal-interfaces.md`. Only changes are
listed; the `Transcriber` protocol and `TranscriberChain` are unchanged.

## `core/transcribe/base.py`

```python
@dataclass(frozen=True)
class AudioFiles:
    """Extracted audio of one job: 32 kbps mono MP3."""

    mp3: Path  # `wav` is removed (it existed only for local Whisper)


@dataclass(frozen=True)
class Transcript:
    ...  # unchanged fields
    self_hosted: bool = False  # produced by a server other than OpenAI's API: costs 0
```

## `core/transcribe/openai.py`

`OpenAITranscriber` (name `openai`) reads `OPENAI_TRANSCRIBE_URL`, `OPENAI_TRANSCRIBE_MODEL`,
`OPENAI_TRANSCRIBE_API_KEY` and `OPENAI_API_KEY`:

| Address | `base_url` | key sent | `self_hosted` |
|---------|------------|----------|---------------|
| empty, or host `api.openai.com` | SDK default / the address | `OPENAI_TRANSCRIBE_API_KEY`, else `OPENAI_API_KEY` | `False` |
| any other | the address | `OPENAI_TRANSCRIBE_API_KEY`, else a fixed placeholder | `True` |

Client timeout 1800 s for every address; SDK default retries. Request (`verbose_json`,
`timestamp_granularities=["segment"]`), 24 MB chunking, offset restoration and `_language_code` are
unchanged.

`check()` makes no network call: `missing OPENAI_API_KEY` when OpenAI's API has no key, else
`model=<model>` (`, self-hosted` appended for another address). Never the address or a key.

## `core/providers.py`

`TRANSCRIBERS = {"openai": OpenAITranscriber}`; `local` is removed.

## `core/media.py`

Audio extraction writes only `audio.mp3` (`-vn -ac 1 -b:a 32k`).

## `core/pricing.py`

`transcription_cost(transcript)` → `0.0` when `transcript.self_hosted`, else the model's per-minute
price (or `None` for an unknown model). `FREE_TRANSCRIPTION_PROVIDERS` is removed.

## `core/config.py`

`TRANSCRIBE_PROVIDER_NAMES = ("openai",)`; `transcribe_providers` defaults to `[]`; new
`openai_transcribe_url: str | None`, `openai_transcribe_api_key: SecretStr | None`, and a helper that
tells whether an address is OpenAI's API (shared by validation and the transcriber). `whisper_model`,
`whisper_device`, `WHISPER_DEVICES` and `cuda_available()` are removed.

## Tests

`tests/fakes_transcribe.py` keeps the fake transcriber; tests that exercised `local` switch to the fake
or to `openai` with a stub SDK client. `tests/live/` gains a self-hosted smoke test that runs only
when `OPENAI_TRANSCRIBE_URL` is set.
