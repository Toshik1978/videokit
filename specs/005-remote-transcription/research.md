# Research: Remote Transcription Server

Decisions for `specs/005-remote-transcription/spec.md`. Earlier research that this feature retires:
`specs/001-videokit-youtube-prep/research.md` R2 (GPU libraries) and R7 (local faster-whisper), and
`specs/002-packaging-releases/research.md` R2 (cuBLAS only).

## T1. Approach: remove local Whisper; `openai` gets a configurable address

- **Decision**: Remove the `local` provider and every GPU/Whisper artifact. The existing `openai`
  transcription provider gains an optional base address so it works with any server that implements
  the OpenAI audio transcription API (speaches is the reference server). `openai` becomes the only
  transcription provider.
- **Rationale**: Operators who already run a shared speech-to-text server should not carry a second
  copy of the model (≈ 2 GB of VRAM after first use) or a GPU-capable image. A self-hosted server and
  OpenAI speak the same API, so one provider with an address is the honest model; a second provider
  name for the same protocol was rejected by the user as odd.
- **Alternatives considered**: (A) add a self-hosted provider and keep `local` as the default or a
  fallback — rejected by the user: no reason to keep duplicate Whisper logic when the server runs on
  the same machine. (B′) a separate `whisper-server` provider next to `openai` — rejected: same
  protocol under two names; its only gain, falling back from the server to OpenAI, is not needed
  when the server runs on the same machine. (C) two images (CPU-only and GPU) — rejected: doubles
  CI and release work (constitution II).

## T2. Address and key handling in the `openai` transcriber

- **Decision**: `OpenAITranscriber` builds its SDK client with `base_url=OPENAI_TRANSCRIBE_URL` when
  set. "OpenAI's API" means no address, or an address whose host is `api.openai.com`. Key: OpenAI's
  API uses `OPENAI_TRANSCRIBE_API_KEY`, else `OPENAI_API_KEY`; any other address uses only
  `OPENAI_TRANSCRIBE_API_KEY`, or a fixed placeholder when unset, because the SDK refuses to build a
  client without a key and servers without authentication ignore the header. Request
  (`verbose_json`, `timestamp_granularities=["segment"]`), chunking and `_language_code` stay as they
  are.
- **Rationale**: One code path for both kinds of server. `OPENAI_API_KEY` is the operator's paid key
  for text generation; sending it to an arbitrary host would leak it.
- **Alternatives considered**: a raw `httpx` client for self-hosted servers — rejected: re-implements
  multipart upload, retries and response parsing that the `openai` SDK (already a dependency) does.
  Always sending `OPENAI_API_KEY` — rejected (leak).

## T3. Upload chunking

- **Decision**: Keep the 24 MB chunk plan for both providers.
- **Rationale**: The 32 kbps mono MP3 is ≈ 240 KB per minute, so one chunk holds ≈ 100 minutes; most
  videos go in one request. Chunk boundaries are already handled with offsets, and a self-hosted
  server loses nothing from fewer, larger requests. One code path.
- **Alternatives considered**: no chunking for self-hosted addresses — rejected: an extra branch and an
  untested very-large-request path, for no observable gain.

## T4. Timeout and cold model loads

- **Decision**: The transcription client uses a 1800 s request timeout for every address; SDK default
  retries stay (connection errors, 429, 5xx).
- **Rationale**: speaches unloads its model after an idle period, and the first request then waits for
  the load (seconds to tens of seconds) before transcribing up to ≈ 100 minutes of audio. The SDK
  default (600 s) is too tight for a long chunk on a modest GPU or a CPU-only server. OpenAI answers
  well within it, so one constant serves both and avoids a new setting (YAGNI).
- **Alternatives considered**: an `OPENAI_TRANSCRIBE_TIMEOUT` variable — deferred until needed.

## T5. (merged into T2)

## T6. Language and duration

- **Decision**: No change: `_language_code` already accepts English names (OpenAI) and ISO 639-1
  codes (speaches returns faster-whisper's code); unknown → `None`. `language_probability` stays
  1.0 when a language is known, 0.0 otherwise. Duration comes from `verbose_json.duration`.
- **Rationale**: Covered by the current tests. A `live` test against a real server confirms it.

## T7. Health and startup check

- **Decision**: `check()` makes no network call and reports `model=<model>`, plus `, self-hosted` when
  the address is not OpenAI's — never the address (the unauthenticated health endpoint should not
  reveal the operator's network) and never a key. Startup validation: an address must be
  `http(s)://…`; OpenAI's API requires `OPENAI_TRANSCRIBE_API_KEY` or `OPENAI_API_KEY`.
- **Rationale**: Constitution IV and the project rule that `check()` is cheap and runs on every
  `/api/health`. A server that is down is discovered by the first job.
- **Alternatives considered**: probing `GET {url}/models` in `check()` — rejected: health would hang or
  flap whenever the server is slow, and cold-loading must not be triggered by health checks.

## T8. Pricing by address

- **Decision**: `Transcript` gains `self_hosted: bool` (default `False`), set by the transcriber when
  the address is not OpenAI's. `pricing.transcription_cost` returns 0 for a self-hosted transcript,
  otherwise the model's per-minute price as today. `FREE_TRANSCRIPTION_PROVIDERS` is removed with
  `local`.
- **Rationale**: The model name does not tell who pays — a self-hosted server may call its model
  `whisper-1`. The address does. Spec FR-006.
- **Alternatives considered**: price by model with unknown models at 0 — rejected: wrong for a server
  that names its model `whisper-1`, and it would silently hide genuinely unknown OpenAI models.

## T9. Audio extraction

- **Decision**: Stop writing `audio.wav`; extract only the 32 kbps mono MP3. `AudioFiles` keeps only
  `mp3`.
- **Rationale**: The WAV existed only for local Whisper; it costs an ffmpeg pass and disk per job.

## T10. Configuration migration

- **Decision**: `TRANSCRIBE_PROVIDER_NAMES = ("openai",)` and `TRANSCRIBE_PROVIDERS` has no default
  (empty). Empty → the existing `TRANSCRIBE_PROVIDERS must list at least one provider` error. `local`
  gets its own message: `TRANSCRIBE_PROVIDERS lists 'local', which was removed in 0.4.0 (use openai,
  with OPENAI_TRANSCRIBE_URL for a self-hosted server)`. Leftover `WHISPER_MODEL` / `WHISPER_DEVICE`
  are simply no longer read (pydantic-settings ignores unknown environment variables).
- **Rationale**: Clarification (refuse to start); one readable line per problem; leftovers do not
  change behaviour, so they do not block an upgrade. The variable stays so the provider-chain
  configuration keeps one shape across transcription, LLM and video.

## T11. Image and packaging

- **Decision**: Remove `faster-whisper`, `av<19` and `nvidia-cublas-cu12` from `pyproject.toml`; in the
  Dockerfile remove the NVIDIA ENV, the `ldconfig` step, `--no-install-package nvidia-cuda-nvrtc-cu12`
  and `HF_HOME` (keep `XDG_CACHE_HOME=/data/cache` as a safety net for libraries that cache under a
  read-only home). Remove the Compose GPU reservation, `task docker:gpu-check`, the `GPU` Taskfile
  variable, `scripts/gpu_check.py`, the `av` Dependabot ignore and the smoke test's Hugging Face
  checks. Lower `scripts/image_size_guard.sh`'s limit to the measured size plus about 25 % headroom.
- **Rationale**: Spec FR-009 and SC-003. A tight guard keeps the slim image slim.

## T12. Constitution

- **Decision**: Amend the constitution through `/speckit-constitution` (MINOR, 1.5.0 → 1.6.0):
  Principle IV lists the transcription provider as "OpenAI-compatible (OpenAI or a self-hosted
  server)" and replaces "A GPU MUST be optional" with "VideoKit itself MUST NOT require a GPU";
  Technology & Operational Constraints replaces the faster-whisper line and the CPU/GPU image wording
  with a CPU-only image and Compose without GPU access.
- **Rationale**: The current text names local `faster-whisper` and a GPU-capable image; leaving it
  would make this feature a standing violation.

## T13. Release

- **Decision**: Ship as `v0.4.0`. Commits that remove `local` use a `!` breaking marker (`feat!:`)
  so git-cliff lists them as breaking; the changelog section carries the migration steps
  (contracts/configuration.md).
- **Rationale**: Pre-1.0 versioning bumps the minor for a breaking change (`.cliff.toml`).
