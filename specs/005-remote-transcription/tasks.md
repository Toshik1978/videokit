---

description: "Task list for the remote transcription server"
---

# Tasks: Remote Transcription Server

**Input**: Design documents from `specs/005-remote-transcription/`

**Prerequisites**: plan.md, spec.md, research.md (T1–T13), data-model.md, contracts/, quickstart.md

**Tests**: Every task is implemented test-first (Constitution V): each task writes its own failing
tests in the listed test file(s) before the implementation. There are no standalone test tasks.
Default tests must pass without API keys, network access to any provider, or a GPU, and keep line
coverage ≥ 80 % per stack.

**Organization**: Tasks are grouped by user story so each story is an independently testable
increment. Each task is one checklist line (what + main files) followed by indented detail
bullets that are part of the task.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: User story the task belongs to (US1–US3 from spec.md)
- Paths: backend `backend/src/videokit/`, backend tests `backend/tests/`, frontend `frontend/`

No setup or foundational phase: the project, toolchain, provider chain and CI already exist.

---

## Phase 1: User Story 1 - Transcribe through a self-hosted server (Priority: P1) 🎯 MVP

**Goal**: `TRANSCRIBE_PROVIDERS=openai` with `OPENAI_TRANSCRIBE_URL` transcribes YouTube Prep jobs
through an OpenAI-compatible server at cost 0. Local Whisper still exists after this phase (removed
in Phase 2).

**Independent Test**: with a stub SDK client, a YouTube Prep job configured with a self-hosted address
completes with timed chapters, the detected language and transcription cost 0; health shows
`model=…, self-hosted` only; a configuration without an address behaves exactly as before.

- [x] T001 [US1] Add the address and key settings and their validation in `backend/src/videokit/core/config.py` (contracts/configuration.md "Added", "Startup validation"; research T2, T7)
  - New `Settings` fields: `openai_transcribe_url: str | None`, `openai_transcribe_api_key: SecretStr | None`; a helper `is_openai_api(url) -> bool` (empty, or host `api.openai.com`) shared with the transcriber.
  - `OPENAI_TRANSCRIBE_URL must start with http:// or https://`.
  - Split the key rule: `OPENAI_API_KEY is required when 'openai' is listed in LLM_PROVIDERS`; `OPENAI_API_KEY or OPENAI_TRANSCRIBE_API_KEY is required when TRANSCRIBE_PROVIDERS lists 'openai' without a self-hosted OPENAI_TRANSCRIBE_URL`. A self-hosted address needs no key. Messages never contain values.
  - Tests: `backend/tests/unit/test_config.py` (each message; values absent; self-hosted address without any key passes; `https://api.openai.com/v1` counts as OpenAI's API).
- [x] T002 [US1] Route the `openai` transcriber by address in `backend/src/videokit/core/transcribe/openai.py` and `backend/src/videokit/core/transcribe/base.py` (research T2, T4, T7, T8; contracts/internal-interfaces.md)
  - `Transcript.self_hosted: bool = False`.
  - SDK client: `base_url=OPENAI_TRANSCRIBE_URL` when set, `timeout=1800` s for every address, SDK default retries. Key: OpenAI's API → `OPENAI_TRANSCRIBE_API_KEY`, else `OPENAI_API_KEY`; other address → `OPENAI_TRANSCRIBE_API_KEY`, else a fixed placeholder — never `OPENAI_API_KEY`.
  - Same request (`verbose_json`, `timestamp_granularities=["segment"]`), 24 MB chunk plan, offset restoration and `_language_code`; the transcript sets `self_hosted` for a non-OpenAI address and names provider `openai` and the configured model.
  - `check()` makes no network call: `missing OPENAI_API_KEY` when OpenAI's API has no key, else `model=<model>` with `, self-hosted` appended for another address; never the address or a key.
  - Tests: `backend/tests/unit/test_transcribe_openai.py` (stub client: base URL, timeout and the key passed per address; `OPENAI_API_KEY` not passed to a self-hosted address; ISO code `"en"` and name `"english"`; chunked offsets; no segments → `no_speech`; SDK error → `ProviderError`; `check()` details).
- [x] T003 [US1] Price self-hosted transcription at 0 in `backend/src/videokit/core/pricing.py` (research T8)
  - `transcription_cost` → `0.0` when `transcript.self_hosted`, whatever the model id (including `whisper-1`); OpenAI's API priced per minute as before; `local` stays free until Phase 2.
  - Tests: `backend/tests/unit/test_pricing.py`, `backend/tests/integration/test_api_platform.py` (`/api/health` transcription detail `model=…, self-hosted`, no address or key).
- [x] T004 [US1] Guard the transcription keys and server errors in `backend/tests/integration/test_log_hygiene.py`
  - A job and a startup with `OPENAI_TRANSCRIBE_URL`, `OPENAI_TRANSCRIBE_API_KEY` and `OPENAI_API_KEY` set: none of the values appear in logs, health or API responses; a failing server logs the error type only (`Transcription provider openai failed (…)`).
  - Fix any leak found in `backend/src/videokit/core/transcribe/openai.py`.
- [x] T005 [P] [US1] Add a `live` smoke test for a self-hosted server in `backend/tests/live/test_live_providers.py` (quickstart.md §3)
  - Runs only when `OPENAI_TRANSCRIBE_URL` is set (skip otherwise); transcribes a short generated spoken fixture; asserts ascending segment times, a known language code, a positive `audio_minutes` and `self_hosted`.

**Checkpoint**: VideoKit can transcribe through a self-hosted server; existing configurations still work.

---

## Phase 2: User Story 2 - A lighter, GPU-free image (Priority: P2)

**Goal**: local Whisper and every GPU artifact are gone; the image is CPU-only and much smaller.

**Independent Test**: `task test`, `task lint`, the image smoke test without `--gpus`, `task docker:size`
at the lowered limit, and an import check proving `faster_whisper`, `ctranslate2` and `av` are absent.

- [x] T006 [US2] Amend the constitution with `/speckit-constitution` in `.specify/memory/constitution.md` (research T12)
  - MINOR 1.5.0 → 1.6.0: Principle IV transcription provider "OpenAI-compatible (OpenAI or a self-hosted server)" and "VideoKit itself MUST NOT require a GPU"; Technology & Operational Constraints: transcription via an OpenAI-compatible Whisper server or OpenAI, a CPU-only image, an example `compose.yaml` without GPU access. Sync Impact Report updated.
- [x] T007 [US2] Remove the `local` provider from the backend in `backend/src/videokit/core/` (research T1, T10; contracts/internal-interfaces.md)
  - Delete `core/transcribe/local_whisper.py` and its export; `TRANSCRIBERS` = `openai`; `TRANSCRIBE_PROVIDER_NAMES = ("openai",)`; `transcribe_providers` default `[]`.
  - Remove `whisper_model`, `whisper_device`, `WHISPER_DEVICES`, `cuda_available()` and their validation from `core/config.py`; remove `FREE_TRANSCRIPTION_PROVIDERS` from `core/pricing.py` (only `self_hosted` is free).
  - Update every test and fixture that names `local` or the Whisper settings to the fake transcriber or `openai` with a stub client: `backend/tests/conftest.py`, `backend/tests/fakes_transcribe.py`, `backend/tests/unit/{test_config,test_transcribe,test_jobs,test_openapi,test_pricing}.py`, `backend/tests/integration/{test_providers,test_api_platform,test_worker,test_regenerate,test_youtube_prep_pipeline,test_log_hygiene}.py`, `backend/tests/e2e_app.py`, `frontend/tests/unit/usage-footer.spec.ts`; delete the local-only cases.
  - Commit with a breaking marker (`feat!:`; research T13).
- [x] T008 [US2] Extract only the MP3 in `backend/src/videokit/core/media.py` and `backend/src/videokit/core/transcribe/base.py` (research T9)
  - `_extract_audio` writes only `audio.mp3` (`-vn -ac 1 -b:a 32k`); `AudioFiles` keeps only `mp3`; `backend/src/videokit/tools/youtube_prep/pipeline.py` builds `AudioFiles(mp3=…)`.
  - Tests: `backend/tests/integration/test_media.py` (no `audio.wav` in the job dir), `backend/tests/integration/test_youtube_prep_pipeline.py`.
- [x] T009 [US2] Drop the Whisper and GPU dependencies in `backend/pyproject.toml` and `backend/uv.lock` (research T11)
  - Remove `faster-whisper`, `av<19` and `nvidia-cublas-cu12` with their comments; `uv lock`; confirm `ctranslate2`, `onnxruntime`, `huggingface-hub` and `av` leave the lock.
  - Delete `backend/tests/unit/test_whisper_audio_compat.py`; remove the `av` ignore and its comment from `.github/dependabot.yml`.
- [x] T010 [US2] Make the image CPU-only in `Dockerfile` and `scripts/docker_smoke.sh` (research T11)
  - Remove `NVIDIA_VISIBLE_DEVICES`, `NVIDIA_DRIVER_CAPABILITIES`, the `ldconfig` step, `--no-install-package nvidia-cuda-nvrtc-cu12` and `HF_HOME`; keep `XDG_CACHE_HOME=/data/cache`; reword the stage comments (no GPU).
  - Smoke test: drop the Hugging Face cache checks; add the import check that `faster_whisper`, `ctranslate2` and `av` are absent; default `SMOKE_TRANSCRIBE_PROVIDERS` works without a GPU.
  - Tests: `backend/tests/unit/test_dockerfile.py`.
- [x] T011 [P] [US2] Remove GPU tooling from `compose.yaml`, `Taskfile.yml` and `scripts/gpu_check.py` (research T11)
  - Delete the Compose `deploy.resources.reservations.devices` block and its comment; remove the Taskfile `GPU` var, `GPU_FLAGS` and `docker:gpu-check`; delete `scripts/gpu_check.py`.
  - Delete `backend/tests/unit/test_gpu_check.py` and `backend/tests/unit/test_taskfile_gpu.py`; `backend/tests/unit/test_compose.py` asserts no GPU reservation.
- [x] T012 [US2] Lower the image size limit in `scripts/image_size_guard.sh` (research T11; SC-003)
  - Build the image, measure with `task docker:size`, set `LIMIT_BYTES` to the measured size plus about 25 % rounded up to 10 MB; confirm the size is at least 40 % below v0.3.0's limit-checked size and record both numbers in the commit message.
- [x] T013 [US2] Rewrite the transcription and GPU documentation in `README.md`, `.env.example`, `CONTRIBUTING.md` and `.claude/CLAUDE.md` (contracts/configuration.md)
  - README: remove "GPU is optional" and the Compose GPU paragraph; provider table (`TRANSCRIBE_PROVIDERS` no default, only `openai`; `OPENAI_TRANSCRIBE_URL`, `OPENAI_TRANSCRIBE_MODEL`, `OPENAI_TRANSCRIBE_API_KEY` and which key goes where; no `WHISPER_*`); one exact self-hosted example with `http://host.docker.internal:8000/v1` and `--add-host`; privacy table (audio goes to OpenAI or the configured server); costs (self-hosted transcription is free); test section (no GPU wording).
  - `.env.example`: `TRANSCRIBE_PROVIDERS=openai`, commented placeholder `OPENAI_TRANSCRIBE_URL` / `OPENAI_TRANSCRIBE_API_KEY`; drop `WHISPER_*`.
  - `.claude/CLAUDE.md`: remove the faster-whisper/`av`, cuBLAS, `-cu12` and `docker:gpu-check` gotchas and the GPU wording in the intro, layout and Docker notes; update `core/` listing (`transcribe/{openai,chain}`).

**Checkpoint**: the image is CPU-only and slim; all suites and the smoke test pass.

---

## Phase 3: User Story 3 - Clear configuration for operators upgrading (Priority: P3)

**Goal**: an operator with a 0.3.x configuration is told exactly what to change.

**Independent Test**: start with `TRANSCRIBE_PROVIDERS=local`, with nothing set, and with leftover
`WHISPER_MODEL` / `WHISPER_DEVICE`; check the startup lines.

- [x] T014 [US3] Add the upgrade-specific startup messages in `backend/src/videokit/core/config.py` (contracts/configuration.md "Startup validation")
  - `local` listed → `TRANSCRIBE_PROVIDERS lists 'local', which was removed in 0.4.0 (use openai, with OPENAI_TRANSCRIBE_URL for a self-hosted server)` instead of the generic unknown-provider line; empty → `TRANSCRIBE_PROVIDERS must list at least one provider`; leftover `WHISPER_MODEL` / `WHISPER_DEVICE` change nothing.
  - Tests: `backend/tests/unit/test_config.py`, `backend/tests/integration/test_log_hygiene.py` (real-uvicorn startup prints exactly one `Configuration error:` line per problem).
- [x] T015 [US3] Document the upgrade from 0.3.x in `README.md` (contracts/configuration.md "Migration from 0.3.x")
  - An "Upgrading from 0.3" section with the four migration steps (set providers, set `OPENAI_TRANSCRIBE_URL` / `OPENAI_TRANSCRIBE_MODEL` or `OPENAI_API_KEY`, remove `WHISPER_*` and GPU flags, optionally delete `<data>/models` and `<data>/huggingface`; VideoKit never deletes them).
  - `docs/RELEASING.md`: note that a breaking release's changelog section links the upgrade section.

**Checkpoint**: upgrades fail fast with actionable messages and documented steps.

---

## Phase 4: Polish & Cross-Cutting Concerns

- [ ] T016 Run the quickstart validation (quickstart.md §1, §2, §5) and fix what fails
  - `task lint`, `task test`, `task e2e`, image build + smoke + size guard, upgrade-path startup check; `gitleaks detect --redact` and the denylist check stay clean.

---

## Dependencies & Execution Order

- Phase 1 (US1) → Phase 2 (US2) → Phase 3 (US3) → Phase 4. US2 removes `local` only once the self-hosted address
  exists; US3 refines the messages for a removed `local`.
- Within Phase 1: T001 → T002 → T003 → T004; T005 [P] after T002.
- Within Phase 2: T006 → T007 → T008 → T009 → T010 → T012; T011 [P] after T007; T013 after T010–T012.
- Within Phase 3: T014 → T015. Phase 4: T016.

## Parallel Examples

- Phase 1: T005 (live test) alongside T003–T004.
- Phase 2: T011 (Compose/Taskfile/script) alongside T008–T010.

## Implementation Strategy

- **MVP**: Phase 1 alone lets a 0.3.x install switch to a self-hosted server by configuration.
- **Release**: Phases 1–4 together ship as the breaking v0.4.0 (`task changelog TAG=v0.4.0`, then tag).
