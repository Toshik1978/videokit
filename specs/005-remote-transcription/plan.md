# Implementation Plan: Remote Transcription Server

**Branch**: `005-remote-transcription` | **Date**: 2026-10-30 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/005-remote-transcription/spec.md`

## Summary

Remove in-process faster-whisper and let the existing `openai` transcription provider talk to any
OpenAI-compatible transcription server (speaches is the reference) through an optional
`OPENAI_TRANSCRIBE_URL`. Removed with local Whisper: faster-whisper, ctranslate2, cuBLAS, the `av<19`
pin, the Whisper settings, GPU detection, the Compose GPU reservation, the GPU check and the model
cache. `openai` becomes the only transcription provider: an empty address means OpenAI's API (priced,
`OPENAI_API_KEY` or `OPENAI_TRANSCRIBE_API_KEY`); any other address is a self-hosted server (cost 0,
only `OPENAI_TRANSCRIBE_API_KEY` is sent). The client gets a 1800 s timeout for cold model loads.
`TRANSCRIBE_PROVIDERS` loses its default, so startup refuses until transcription is configured. The
image becomes CPU-only and much smaller; the change ships as the breaking release v0.4.0.

**Approach (from brainstorming)**: the user chose **B — remove local Whisper entirely**. Trade-off: a
much smaller CPU-only image, no GPU or CUDA anywhere, and fewer gotchas (`av<19`, `-cu12`,
`docker:gpu-check`), against a breaking change with no zero-config transcription default. Rejected:
**A — keep local Whisper** as the default or fallback ("no reason to keep any Whisper logic when the
server runs on the same machine"). In review the user also rejected a separate `whisper-server`
provider next to `openai` as odd for one protocol; the address now decides cost and which key is
sent, at the price of no fallback from the self-hosted server to OpenAI. Details: research.md T1, T2,
T8.

## Technical Context

**Language/Version**: Python 3.14 (backend); TypeScript 6 / Vue 3 (frontend, untouched except a test
fixture that names the `local` provider)

**Primary Dependencies**: FastAPI, pydantic-settings, `openai` SDK (already present). Removed:
`faster-whisper`, `av`, `nvidia-cublas-cu12` (and transitively ctranslate2, onnxruntime,
huggingface-hub, tokenizers)

**Storage**: unchanged (SQLite + `DATA_DIR/jobs/<id>/`); `audio.wav` no longer written

**Testing**: pytest with a stub `openai` client and the existing fake transcriber; `live` marker for a
real server; vitest unchanged; image smoke + size guard

**Target Platform**: Linux container, CPU only; the transcription server may use a GPU outside
VideoKit

**Project Type**: web service (backend + SPA in one image)

**Performance Goals**: a cold server (model unloaded) plus ≈ 100 minutes of audio per request fits the
1800 s timeout (research T4)

**Constraints**: `check()` makes no network call; no key, URL, audio or transcript in logs, health or
UI; default suites need no network

**Scale/Scope**: ≈ 25 files touched, mostly deletions; no new endpoints, no UI change

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Open-source hygiene | Pass | Examples use `example.com`, `localhost` and Docker's generic `host.docker.internal`; no operator host names. |
| II. Simplicity | Pass | Net removal of dependencies, settings and code paths; no new provider, two new settings (address, key) on the existing one; the timeout is a constant. |
| III. Multi-tool modularity | Pass | Change is inside `core/transcribe`, `core/config`, `core/media`, `core/pricing`; YouTube Prep only drops the `wav` path. |
| IV. Provider abstraction with fallback | **Amend** | Still behind `Transcriber`, configured as an ordered list (of one transcription provider) with startup validation. The text names "local Whisper" and "A GPU MUST be optional"; amended via `/speckit-constitution` (research T12). |
| V. Test-first | Pass | Each task test-first; default suites stay offline; real server under `live`. |
| VI. Privacy & data lifecycle | Pass | Audio goes only to the address the operator configures; `OPENAI_API_KEY` is never sent to a non-OpenAI address; WAV no longer written. |
| VII. Security boundary | Pass | No auth change; the server address is not exposed by health. |
| Tech & operational constraints | **Amend** | "local faster-whisper" and "single image for CPU and GPU hosts … GPU access enabled by default" replaced by a CPU-only image and a self-hosted Whisper server (research T12). |

Post-design re-check: unchanged — the two amendments are part of this feature's tasks and land with
it, so no standing violation remains.

## Project Structure

### Documentation (this feature)

```text
specs/005-remote-transcription/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── configuration.md
│   └── internal-interfaces.md
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
backend/src/videokit/core/
  config.py                 provider names, openai_transcribe_url/_api_key, validation; drop whisper_*/cuda_available
  providers.py              TRANSCRIBERS: openai
  pricing.py                self-hosted transcripts cost 0; drop FREE_TRANSCRIPTION_PROVIDERS
  media.py                  extract audio.mp3 only
  transcribe/base.py        AudioFiles(mp3); Transcript.self_hosted
  transcribe/openai.py      address, key selection, 1800 s timeout
  transcribe/local_whisper.py   deleted
  transcribe/__init__.py    exports
backend/src/videokit/tools/youtube_prep/pipeline.py   AudioFiles(mp3=…)
backend/pyproject.toml, backend/uv.lock               drop faster-whisper, av, nvidia-cublas-cu12
backend/tests/                unit/integration updates; delete test_gpu_check, test_taskfile_gpu,
                              test_whisper_audio_compat, local parts of test_transcribe; live test
frontend/tests/unit/usage-footer.spec.ts             provider name fixture
Dockerfile, compose.yaml, Taskfile.yml, scripts/{docker_smoke.sh,image_size_guard.sh}
scripts/gpu_check.py          deleted
.github/dependabot.yml        drop av ignore
.env.example, README.md, CONTRIBUTING.md, .claude/CLAUDE.md
.specify/memory/constitution.md   via /speckit-constitution
```

**Structure Decision**: existing web-service layout; no new modules.

## Delivery notes

- Breaking-change commits use `feat!:` so `task changelog` marks them; the v0.4.0 section carries the
  migration steps from `contracts/configuration.md`.
- The datron operator receives a deploy contract delta (env vars, no GPU device, no model cache,
  memory cap, release tag) once v0.4.0 is tagged; it is derived from `contracts/configuration.md`.

## Complexity Tracking

No violations to justify; the constitution amendments are recorded above.
