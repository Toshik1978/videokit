---

description: "Task list for the Video Generation tool"
---

# Tasks: Video Generation Tool

**Input**: Design documents from `specs/004-video-generation/`

**Prerequisites**: plan.md, spec.md, research.md (V1–V14), data-model.md, contracts/, quickstart.md

**Tests**: Every task is implemented test-first (Constitution V): each task writes its own failing
tests in the listed test file(s) before the implementation. There are no standalone test tasks.
Default tests must pass without API keys, network access to AI providers, or a GPU, and keep line
coverage ≥ 80 % per stack.

**Organization**: Tasks are grouped by user story so each story is an independently testable
increment. Each task is one checklist line (what + main files) followed by indented detail
bullets that are part of the task.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: User story the task belongs to (US1–US4 from spec.md)
- Paths: backend `backend/src/videokit/`, backend tests `backend/tests/`, frontend `frontend/src/`

---

## Phase 1: Foundational (platform building blocks, blocking all stories)

**Purpose**: The tool-agnostic core changes of plan.md Complexity Tracking. No setup phase: the
project, toolchain and CI already exist. After this phase YouTube Prep and Audio Extract work
exactly as before, through their own creation routes.

- [x] T001 Extend the `Tool` protocol and discovery in `backend/src/videokit/core/registry.py` (contracts/internal-interfaces.md "Tool — changed")
  - New required members: `concurrency: int`, `resume_after_restart: bool`, `enabled(settings) -> bool`, `accept(job: JobHandle, ctx: CoreContext) -> None` (blocking; raise `AppError` to fail).
  - `discover_tools` raises `RegistryError` for a tool missing a member, `concurrency < 1`, or a non-bool `resume_after_restart`.
  - Add `concurrency = 1`, `resume_after_restart = False`, `enabled() -> True` and `accept()` (calls `ctx.media.probe_source(job.dir)` and records `duration_s`) to `backend/src/videokit/tools/youtube_prep/__init__.py` and `backend/src/videokit/tools/audio_extract/__init__.py`.
  - Move today's probe out of `Worker.on_upload_complete` into `MediaService.probe_source(job_dir) -> ProbeResult` in `backend/src/videokit/core/media.py` (same `not_a_video` error).
  - Tests: `backend/tests/unit/test_registry.py` (each new validation case), `backend/tests/integration/test_media.py` (`probe_source` on a playable video and a text file).
- [x] T002 Make one inputs-complete path that asks the tool in `backend/src/videokit/core/worker.py`, `core/uploads.py` and `core/jobs.py` (research V1)
  - `Worker.complete_inputs(job_id)` replaces `on_upload_complete`: same guards (state `uploading`, offset == length, not expired, tool known); runs `tool.accept(handle, ctx)` in a thread; `AppError` → `_fail` with its message and code (inputs deleted); else `uploading → queued` and `notify()`. Idempotent.
  - tus completion in `uploads.py` and the startup re-fire of `finished_uploads` call `complete_inputs`.
  - `JobStore.create(tool_id, upload_length, source_name=None)` accepts `upload_length == 0` ("a job whose tool takes no resumable upload"); `upload_length > 0` keeps "0 < length ≤ max".
  - Tests: `backend/tests/integration/test_worker.py` (accept OK → queued; accept raises → failed with that code and inputs removed; repeated completion is a no-op; a zero-length job completes without any upload), `backend/tests/unit/test_jobs.py` (zero-length create; negative and over-max still rejected).
- [x] T003 Add the job-creation building blocks to `JobAccess` and move creation into the tools in `backend/src/videokit/core/registry.py`, `core/worker.py`, `core/uploads.py`, `core/api.py`, `tools/youtube_prep/routes.py`, `tools/audio_extract/routes.py` (contracts/internal-interfaces.md "JobAccess — added", contracts/http-api.md)
  - `JobAccess.create(tool_id, *, upload_length=0, source_name=None) -> Job`, `job_dir(job_id)`, `async complete(job_id)` (→ `Worker.complete_inputs`), `async discard(job_id)` (delete row and dir of a job still `uploading`).
  - `core.uploads.create_upload_job(request, tool_id, body)` → `{"job_id", "upload_url": "/api/uploads/<id>"}`, with today's checks (`size` positive → else `422 invalid_request`; `> max_upload_bytes` → `413 file_too_large`; `file_name` sanitizing unchanged).
  - Each video tool registers `POST /jobs` (body `{"size", "file_name"}`) calling the helper; remove `POST /api/jobs` and `CreateJobRequest` from `core/api.py`.
  - Update `specs/001-videokit-youtube-prep/contracts/http-api.md` and `contracts/internal-interfaces.md` to point at the new routes and seam.
  - Tests: `backend/tests/integration/test_api_platform.py` (`POST /api/jobs` is gone; per-tool create returns 201 with upload URL; 413/422 cases; file-name sanitizing table moved here), `backend/tests/integration/test_youtube_prep_*.py` and `test_audio_extract.py` end-to-end via the new route, `backend/tests/e2e_app.py` updated.
- [x] T004 [P] Post uploads to the tool's creation route in `frontend/src/components/UploadPanel.vue`
  - `POST /api/tools/${toolId}/jobs` with `{size, file_name}`; resume of a saved upload unchanged.
  - Tests: `frontend/tests/unit/upload.spec.ts` (URL and body), existing Playwright upload flows still pass.
- [x] T005 Run each lane with `tool.concurrency` slots in `backend/src/videokit/core/worker.py` (research V4)
  - A lane starts `concurrency` slot tasks; a slot claims, under `self._lock`, the oldest `queued` job of its tool whose id is not in `_current` (`JobStore.next_queued(tool_id, exclude=…)`), adds it to `_current`, runs it, removes it.
  - `queue_position` and `cancel(active=…)` behaviour unchanged.
  - Tests: `backend/tests/integration/test_worker.py` (a fake tool with concurrency 3: four jobs → three run at once, the fourth waits with position 1 and starts when one finishes; no job runs twice; concurrency-1 tools unchanged), `backend/tests/unit/test_jobs.py` (`next_queued` with `exclude`).
- [x] T006 Let tools choose restart behaviour in `backend/src/videokit/core/jobs.py`, `core/worker.py` and `main.py` (research V3)
  - `JobStore.recover_after_restart(resumable: set[str])`: fails waiting/running jobs of tools **not** in `resumable` exactly as today (`server_restarted`, source deleted); jobs of resumable tools keep their state and files.
  - Lanes of resumable tools first re-run that tool's jobs found in a running state (not `queued`, not `uploading`), then take queued jobs.
  - Add `S.GENERATING` to `TRANSITIONS[S.QUEUED]` (data-model.md state machine).
  - Tests: `backend/tests/unit/test_jobs.py` (mixed resumable/non-resumable recovery; new edge), `backend/tests/integration/test_worker.py` (a resumable fake job left `generating` is re-run once after restart before a queued job).
- [x] T007 Mount, list and run only enabled tools in `backend/src/videokit/main.py` and `core/api.py` (research V11)
  - `create_app` filters `discover_tools(...)` by `tool.enabled(settings)` before mounting routers; `/api/tools` and the worker lanes use the filtered set; a job of a disabled tool follows the existing "tool removed" path (`internal_error`).
  - Tests: `backend/tests/integration/test_api_platform.py` (a fake disabled tool is absent from `/api/tools`, its routes answer 404, no lane runs).
- [x] T008 [P] Add the video generation seam and chain in `backend/src/videokit/core/videogen/{__init__,base,chain}.py` (contracts/internal-interfaces.md "Video generation seam")
  - `Picture`, `VideoRequest` (`aspect_ratio: "9:16" | "16:9"`, `resolution: "720p" | "1080p"`), `Capabilities`, `Accepted`, `PollResult`, `VideoError(code)` with codes `provider_out_of_credit | content_blocked | invalid_picture | provider_unavailable | video_failed`, `VideoGenerator` protocol (`check`, `capabilities`, `submit`, `poll`, `cancel`, `forget`).
  - `VideoChain.submit`: providers in order, moving on only when one raised before accepting (any `VideoError` except `content_blocked`/`invalid_picture`, which stop the chain); raises the last error when none accepted. `poll/cancel/forget` route to the provider named in `Accepted.provider`, never another.
  - Fake provider `backend/tests/fakes_videogen.py` (scripted accept/poll/error sequences; writes a tiny ffmpeg H.264+AAC clip; counts `submit` calls).
  - Tests: `backend/tests/unit/test_videogen_chain.py` (fallback before acceptance; no fallback after; stop on content_blocked; routing by provider name; log records carry exception types only).
- [x] T009 Add video usage and Omni prices in `backend/src/videokit/core/llm/base.py`, `core/jobs.py`, `core/registry.py`, `core/worker.py` and `core/pricing.py` (research V8)
  - `Usage.video_output_tokens: int = 0` (summed by `__add__`); job `usage` JSON gains `video_output_tokens` (missing in old rows → 0); `JobHandle.add_usage` stores it.
  - Price field `video_output` ($ per 1M tokens); `DEFAULT_PRICES["gemini-omni-1.1-flash"] = {"input": 1.50, "output": 9.00, "video_output": 17.50}` — verify against Google's official pricing page before merging; `PRICING_OVERRIDES` accepts the field.
  - Tests: `backend/tests/unit/test_pricing.py` (8 s at 1080p = 8 × 8688 video tokens + 2 pictures × 1120 input tokens → expected cost; override merge), `backend/tests/unit/test_jobs.py` (old row without the key).
- [x] T010 Configure and check video providers in `backend/src/videokit/core/config.py`, `core/providers.py`, `core/worker.py`, `core/registry.py`, `main.py` and `core/api.py` (contracts/configuration.md)
  - Settings `video_providers` (CSV, default empty, allowed `omni`), `gemini_api_key: SecretStr | None`, `omni_model = "gemini-omni-1.1-flash"`; validation lines "VIDEO_PROVIDERS contains an unknown provider (allowed: omni)" and "GEMINI_API_KEY is required when 'omni' is listed in VIDEO_PROVIDERS".
  - `providers.VIDEOS` registry; `Providers.video: VideoChain | None` (None when empty); `status()`/`problems()` include `"video"` (`VIDEO_PROVIDERS`); `CoreContext.video` / `Context.video`; `/api/health` shows `"video": [...]` (empty list when none).
  - Tests: `backend/tests/unit/test_config.py`, `backend/tests/integration/test_api_platform.py` (health with and without a fake video provider; an unready configured provider fails startup with one `Configuration error:` line; empty `VIDEO_PROVIDERS` starts fine).
- [x] T011 Add picture normalization and clip finalization to `backend/src/videokit/core/media.py` with fixtures in `backend/tests/conftest.py` (research V6, V7)
  - `normalize_picture(src, out)`: decode JPEG/PNG/WebP/HEIC (first image) with ffmpeg, write JPEG, "long side ≤ 2048 px", all metadata stripped; undecodable → `AppError("invalid_picture", …, 422)`.
  - `Clip` dataclass and `finalize_clip(raw, job_dir) -> Clip`: `ffmpeg -i raw -map 0:v:0 -an -sn -dn -map_metadata -1 -c:v copy -movflags +faststart clip.mp4` (libx264 re-encode when the stream is not H.264); `thumbnail.jpg` at 0.5 s, "≤ 480 px long side"; probe duration/width/height/size; delete `raw`; ffmpeg failure → `AppError("video_failed", …, 502)`.
  - Fixtures: `make_picture(format, w, h, exif_gps=False)`, `make_clip(codec, with_audio)`; HEIC fixture only where the local ffmpeg can encode it (skip otherwise).
  - Image check (research V7): `scripts/docker_smoke.sh` decodes a HEIC sample generated during the smoke run through `normalize_picture` inside the built image. If Debian's ffmpeg cannot decode HEIC, add `pillow-heif` behind `normalize_picture` (same interface, same errors), lock it in `backend/uv.lock`, and record the decision in research.md V7.
  - Tests: `backend/tests/integration/test_media.py` (PNG/WebP/JPEG → JPEG, 4000×3000 → 2048×1536, GPS tag gone, text file → invalid_picture; clip with AAC → no audio stream, faststart, thumbnail exists, metadata gone; HEVC raw → re-encoded H.264).
- [x] T012 [P] Move shared download and share helpers in `backend/src/videokit/core/downloads.py`, `tools/audio_extract/routes.py`, `frontend/src/components/ShareButton.vue` and `frontend/src/tools/audio-extract/JobPage.vue` (research V12, V13)
  - `content_disposition(name)` moves unchanged to `core/downloads.py`; Audio Extract imports it.
  - `ShareButton.vue` moves to `frontend/src/components/` with a generic `fileUrl`/`fileName`/`mimeType` interface; Audio Extract uses it.
  - Tests: existing `backend/tests/unit/test_audio_extract_naming.py` disposition cases move to `backend/tests/unit/test_downloads.py`; `frontend/tests/unit/share.spec.ts` against the moved component.
- [x] T013 [P] Add the backend dependencies in `backend/pyproject.toml` and `backend/uv.lock` (research V2, V14)
  - `google-genai>=2.28` (no extras — never `local-tokenizer`) and `python-multipart`; `uv lock`; Dependabot needs no change.
  - Tests: `backend/tests/unit/test_dependencies.py` asserts `google.genai` and `multipart` import and that `torch`/`transformers` are not installed.

- [x] T014 Amend the constitution in `.specify/memory/constitution.md` together with the new SDK (plan.md Complexity Tracking)
  - MINOR 1.4.0 → 1.5.0: Principle IV lists video generation providers; Technology & Operational Constraints adds "Video generation: Google Gemini Omni via the official `google-genai` SDK"; Sync Impact Report updated.

**Checkpoint**: `task lint && task test && task e2e` green; YouTube Prep and Audio Extract behave
as before through `POST /api/tools/<id>/jobs`.

---

## Phase 2: User Story 1 - Generate a clip from a prompt and pictures (Priority: P1) 🎯 MVP

**Goal**: A prompt and 0–7 ordered pictures produce one silent clip the user previews, downloads
and shares.

**Independent Test**: With the fake provider, create a job with two pictures and one without;
each goes `queued → generating → done`, the clip has the chosen duration/orientation/resolution,
no audio, and downloads with the prompt-based name (quickstart §1).

- [x] T015 [P] [US1] Validate and store creation inputs in `backend/src/videokit/tools/video_generation/inputs.py` (data-model.md validation rules, research V7, V14)
  - `prompt`: "required, trimmed, 1–2,000 characters"; `duration_s`: "a whole number of seconds from 5 to 10 that the provider supports"; `aspect_ratio`: "`9:16` (default) or `16:9`"; `resolution`: "`1080p` (default) or `720p`"; `pictures`: "0–7 files, each ≤ 10 MB (decimal), detected by content as JPEG, PNG, WebP or HEIC/HEIF".
  - Errors: `422 invalid_request`, `422 too_many_pictures`, `413 picture_too_large`, `422 invalid_picture`.
  - Writes `pictures/<n>.jpg` via `ctx.media.normalize_picture` and `inputs.json` atomically (tmp + `os.replace`) last.
  - Tests: `backend/tests/unit/test_video_generation_inputs.py` (each rule and boundary: 0 and 2,000/2,001 chars, 7/8 pictures, 10,000,000/10,000,001 bytes, magic-byte sniffing ignores the client's content type).
- [x] T016 [P] [US1] Build the provider prompt with picture references in `backend/src/videokit/tools/video_generation/prompt.py` (research V7)
  - No pictures → the user's prompt unchanged. With N pictures → `[# References <IMAGE_REF_0>@Picture1 … <IMAGE_REF_{N-1}>@PictureN]` + newline + the user's prompt unchanged.
  - Tests: `backend/tests/unit/test_video_generation_prompt.py` (0, 1, 7 pictures; non-Latin prompt untouched).
- [x] T017 [P] [US1] Derive the download name in `backend/src/videokit/tools/video_generation/naming.py` (research V13)
  - "First 60 characters of the prompt (whitespace collapsed), cut at a word boundary", file-unsafe characters replaced, non-Latin kept, `.mp4`; fallback `clip.mp4`.
  - Tests: `backend/tests/unit/test_video_generation_naming.py` (long prompt, Cyrillic, only symbols → `clip.mp4`, slashes/quotes).
- [x] T018 [US1] Implement the Omni adapter in `backend/src/videokit/core/videogen/omni.py` and register it in `core/providers.py` (research V2, V8, V9, V10)
  - `check()`: local only — key present, `detail="model=<OMNI_MODEL>"`; never a network call.
  - `capabilities()`: `durations_s=[5..10]`, `max_pictures=7`, `video_tokens_per_second={"720p": 5792, "1080p": 8688}`, `tokens_per_image=1120`.
  - `submit()`: lazily created `genai.Client`; `client.interactions.create(model, input=[text, *inline base64 JPEG images], response_format={"type": "video", "aspect_ratio", "resolution", "duration"}, background=True, store=True)` → interaction id. The `duration` string format lives here only.
  - `poll(ref, out_dir)`: `interactions.get`; `in_progress` → not done; `completed` → decode the video to `out_dir/raw.mp4`, usage from `usage.output_tokens_by_modality.video` (else derived: duration × tokens/s, pictures × 1120); `failed`/`incomplete`/no video → mapped `VideoError`.
  - Error mapping table of research V10 for both exception families (`google.genai._interactions.APIStatusError` subclasses and `google.genai.errors.APIError`); log exception types only.
  - `cancel(ref)`, `forget(ref)` (`interactions.cancel` / `interactions.delete`), best-effort.
  - Tests: `backend/tests/unit/test_omni_mapping.py` with a stubbed SDK client (request shape incl. image order and `background/store`; each row of the V10 table; usage present vs absent; no network).
  - Live (opt-in, quickstart §3): `backend/tests/live/test_omni_live.py` behind the `live` marker and `GEMINI_API_KEY` — one 5 s 720p clip from a short prompt and one PNG: done, usage reported or derived, the accepted `duration` format recorded in research.md V9, a deliberately refused prompt surfaces as `content_blocked`, `forget` succeeds.
- [x] T019 [US1] Implement the tool and its generation pipeline in `backend/src/videokit/tools/video_generation/{__init__,pipeline}.py` (research V3, V5, V6)
  - `TOOL`: `id = "video_generation"`, `name = "Video Generation"`, `description = "Generate a short silent clip from a prompt and pictures."`, `concurrency = 3`, `resume_after_restart = True`, `enabled(settings)` = `VIDEO_PROVIDERS` non-empty, `accept()` = `inputs.json` present and complete.
  - `run`: `set_state("generating")`; write `submit.json` atomically before `ctx.video.submit`; write `operation.json` `{provider, ref, submitted_at}` atomically right after; record `providers={"video": accepted.provider}`.
  - Poll every 10 s with `asyncio.sleep` (no thread held), `check_cancelled` each round (on cancel: `ctx.video.cancel`, best-effort); cap 20 minutes → `AppError("provider_timeout", …)`; `VideoError` → `AppError(code, catalog-safe message)`.
  - On completion: `add_usage` with the pricing estimate; `ctx.media.finalize_clip`; `result.json` `{file, thumbnail, download_name, duration_s, width, height, aspect_ratio, resolution, size_bytes}` atomically; `ctx.video.forget`, best-effort.
  - Tests: `backend/tests/integration/test_video_generation.py` with the fake provider and a fast poll interval (happy path with and without pictures; result fields; clip has no audio stream; usage and cost recorded; each VideoError → its failure code; timeout; cancel while generating calls `cancel` and deletes the job data), `backend/tests/integration/test_log_hygiene.py` (a marker prompt, marker picture bytes and fake provider messages appear in no log record across success, every failure code and cancel).
- [x] T020 [US1] Add the creation, options and file routes in `backend/src/videokit/tools/video_generation/routes.py` (contracts/http-api.md)
  - `GET /options` (durations, aspect ratios, resolutions, limits, `price_per_second_usd` per resolution and `price_per_picture_usd` from capabilities × price table, `null` when unknown).
  - `POST /jobs` multipart behind a body-size guard (7 × 10 MB + 1 MB) using `request.form(max_files=7, max_fields=8, max_part_size=10_000_000)`: `access.create(TOOL_ID)` → `inputs.store(...)` → `access.complete(job_id)`; on validation error `access.discard(job_id)` then raise. `201 {"job_id"}`.
  - `GET|HEAD /jobs/{id}/clip` (attachment via `core.downloads.content_disposition`, byte ranges), `GET /jobs/{id}/thumbnail`; `404 job_not_found` for unknown/expired/other tool, `409 job_not_done`.
  - Tests: `backend/tests/integration/test_video_generation.py` (create → queued without upload; 413 before the whole oversized body is read; validation errors leave no job row or dir; options with and without known price; clip download name and ranges; 404/409 cases).
- [x] T021 [P] [US1] Add a multipart helper to `frontend/src/api/client.ts` and the tool's catalog strings to `frontend/src/i18n/en.json`
  - `postForm<T>(path, form: FormData)` with the same error envelope handling as `postJson`.
  - Strings: `tools.video_generation.{name,description}`, `videoGeneration.*` (form labels, defaults, estimate incl. "cost unknown", generate, cancel warning "the provider may still charge for this attempt", result labels, retention note), `job.states.generating` if missing, `job.failures.{provider_out_of_credit,content_blocked,provider_timeout,invalid_picture,provider_unavailable,resume_impossible,video_failed}`, `errors.{too_many_pictures,picture_too_large,invalid_picture}`.
  - Tests: `frontend/tests/unit/client.spec.ts` (`postForm` success and error envelope), the existing catalog-completeness test covers the new failure codes.
- [x] T022 [US1] Build the form page with picture picker and estimate in `frontend/src/tools/video-generation/{index,types,estimate}.ts`, `VideoGenerationPage.vue` and `PicturePicker.vue` (research V12)
  - `index.ts`: `id = 'video_generation'`, routes `/tools/video-generation` and `/tools/video-generation/jobs/:id`, labels `tools.video_generation.name/description`.
  - Picker: add from gallery/files (`accept` = `options.picture_types`), thumbnails in order with position numbers, move up/down and remove buttons ≥ 44×44 px, client-side limits from `/options` with catalog messages.
  - Form: prompt (counter, max 2,000), duration select from `options.durations_s` (default 8), orientation 9:16 default / 16:9, resolution 1080p default / 720p; live estimate `duration × price_per_second[resolution] + pictures × price_per_picture`, "cost unknown" when null; Generate disabled for an empty prompt; submit via `postForm`, then navigate to the job page.
  - Tests: `frontend/tests/unit/video-generation.spec.ts` (reorder/remove keeps order sent in FormData; limits; estimate math and unknown price; disabled Generate), `frontend/tests/unit/estimate.spec.ts`.
- [x] T023 [US1] Build the job page in `frontend/src/tools/video-generation/JobPage.vue`
  - Stage via the existing `JobStatus` component (queued with position, generating, done, failed with catalog message, cancelled); Cancel with the provider-charge warning while generating.
  - Done: `<video playsinline muted controls>` preview from `…/clip`, duration/orientation/resolution/actual cost, Download (`…/clip`), shared `ShareButton` (MP4, `download_name`).
  - Tests: `frontend/tests/unit/video-generation-job.spec.ts` (each state; failure codes map to catalog keys, never server text; Share hidden when unsupported).
- [x] T024 [US1] Cover the flow end-to-end in `frontend/tests/e2e/video-generation.spec.ts` and `backend/tests/e2e_app.py`
  - `e2e_app.py` enables the tool with the fake video provider (fast polling).
  - Pick two pictures, reorder, remove one, generate, see `done`, download; no horizontal scroll at 360 px and targets ≥ 44×44 px on all four projects.

**Checkpoint**: US1 works on its own via `/tools/video-generation`.

---

## Phase 3: User Story 2 - Leave and come back to finished clips (Priority: P2)

**Goal**: Recent clips on the device, and generations that survive a server restart without a
second paid request.

**Independent Test**: Start two jobs, reload the page: both listed with status; restart during
generation: the job completes and the fake provider counted one `submit` (quickstart §1).

- [x] T025 [US2] Resume interrupted generations in `backend/src/videokit/tools/video_generation/pipeline.py` (data-model.md "Resume rules", research V3)
  - On `run`: `operation.json` present → poll it, never submit; `submit.json` only → `AppError("resume_impossible", …)`; neither → fresh submit.
  - Tests: `backend/tests/integration/test_video_generation.py` (restart the app between submit and completion with `operation.json` → done with exactly one submit; only `submit.json` → `resume_impossible`; a queued video job survives restart and runs; YouTube Prep jobs in the same restart still fail `server_restarted`), `backend/tests/integration/test_log_hygiene.py` (the resume path logs no prompt), `backend/tests/integration/test_purge.py` (SC-007: after a video job's TTL the purge removes `inputs.json`, `pictures/`, `submit.json`, `operation.json`, `clip.mp4`, `thumbnail.jpg` and `result.json` together with the row).
- [x] T026 [P] [US2] Keep the device-local list in `frontend/src/tools/video-generation/recentClips.ts` (data-model.md "Recent clip entry")
  - Key `videokit.video_generation.recent`; entries `{job_id, created_at, prompt (first 80 characters)}`, newest first, "≤ 20 entries" (oldest dropped); prune entries older than `job_ttl_hours` from `/api/config`; every storage access wrapped so unavailable storage yields an empty list; `add`, `remove`, `list`.
  - Tests: `frontend/tests/unit/recent-clips.spec.ts` (cap, ordering, age prune with a fake clock, throwing `localStorage`).
- [x] T027 [US2] Show Recent clips on the tool page in `frontend/src/tools/video-generation/VideoGenerationPage.vue`
  - Add an entry after a successful create; on load, fetch `GET /api/jobs/{id}` per entry: `404 job_not_found` → remove; show status, thumbnail for `done` (`…/thumbnail`), prompt snippet; tap → job page; retention note "clips are deleted after N hours" from `job_ttl_hours`.
  - Tests: `frontend/tests/unit/video-generation.spec.ts` (list rendering per state, 404 entry removed silently, empty list when storage throws); extend `frontend/tests/e2e/video-generation.spec.ts` (reload → clip in Recent clips).

**Checkpoint**: US1 + US2 work independently.

---

## Phase 4: User Story 3 - Try again with the same inputs (Priority: P3)

**Goal**: One tap reopens the form with the job's pictures, prompt and options.

**Independent Test**: From a done job, Try again shows the same pictures (order), prompt and
options; submitting creates a new job and leaves the original unchanged.

- [x] T028 [US3] Serve a job's inputs in `backend/src/videokit/tools/video_generation/routes.py` (contracts/http-api.md "inputs", "pictures/{n}")
  - `GET /jobs/{id}/inputs` (any state, not expired) → `{prompt, duration_s, aspect_ratio, resolution, pictures: [urls in order]}`; `GET /jobs/{id}/pictures/{n}` → `image/jpeg`, `404` out of range/expired/other tool.
  - Tests: `backend/tests/integration/test_video_generation.py` (done, failed and cancelled-before-delete jobs; expired → 404; prompt never in logs).
- [x] T029 [US3] Add Try again in `frontend/src/tools/video-generation/JobPage.vue` and `VideoGenerationPage.vue`
  - Try again on done/failed jobs (and cancelled ones whose inputs still exist): navigate to the form with `?from=<id>`; the form loads `…/inputs`, fetches pictures as blobs into the picker in order, fills prompt and options; submit creates a new job and a new Recent clips entry.
  - Tests: `frontend/tests/unit/video-generation.spec.ts` (prefill order and options; original untouched), extend `frontend/tests/e2e/video-generation.spec.ts`.

---

## Phase 5: User Story 4 - Home page card and readiness (Priority: P4)

**Goal**: The card appears when the tool is configured; otherwise the tool is hidden and VideoKit
starts normally.

**Independent Test**: With a fake video provider the card is shown and opens the tool; with
`VIDEO_PROVIDERS` empty startup succeeds, no card, the tool's link shows not-found.

- [x] T030 [US4] Show the card and the hidden state in `frontend/src/tools/video-generation/VideoGenerationPage.vue` and `frontend/src/pages/HomePage.vue`
  - Home lists the card from `/api/tools` (existing logic; verify ordering with three tools); the tool page and job page render the existing `NotFoundPage` when `/api/tools` does not list `video_generation` or `/options` answers 404.
  - Tests: `frontend/tests/unit/home.spec.ts` (three cards), `frontend/tests/unit/video-generation.spec.ts` (hidden → not-found), `backend/tests/integration/test_video_generation.py` (`VIDEO_PROVIDERS` empty: not listed, every tool route 404, startup OK).

---

## Phase 6: Polish & Cross-Cutting Concerns

- [x] T031 [P] Check the finished clip path in the image in `scripts/docker_smoke.sh`
  - Inside the image: `finalize_clip` turns a generated H.264+AAC clip into an MP4 with no audio stream and a thumbnail; `task docker:size` stays under 1.25 GB.
- [x] T032 [P] Document the tool in `README.md`, `.env.example` and `.claude/CLAUDE.md`
  - README: tool section, `VIDEO_PROVIDERS`/`GEMINI_API_KEY`/`OMNI_MODEL`, cost per clip (≈ $0.10/s 720p, ≈ $0.15/s 1080p), prepaid billing and Tier 1 spend cap, provider retention and that VideoKit deletes each stored request, the endpoint-protection reminder; breaking change: `POST /api/jobs` replaced by `POST /api/tools/<id>/jobs`.
  - `.env.example`: placeholders only (`GEMINI_API_KEY=<your-api-key>`).
  - `CLAUDE.md`: tools list, layout (`core/videogen/`, `tools/video_generation/`, `frontend/src/tools/video-generation/`), architecture rules for tool-owned creation, lane width, restart behaviour, `enabled()`.


---

## Dependencies & Execution Order

- **Phase 1 (Foundational)** blocks every story. Inside it: T001 → T002 → T003 → T005 → T006 → T007
  → T009 → T010 → T011 (all touch `worker.py`, `registry.py`, `jobs.py` or `media.py`); T004 after
  T003; T010 after T008; T014 (constitution) together with T013 (google-genai). T008, T012, T013
  are independent of each other and of the sequential chain.
- **US1 (Phase 2)** needs Phase 1. T015–T017 in parallel; T018 needs T008–T010, T013; T019 needs
  T015–T018; T020 needs T019; T021 in parallel with backend work; T022 → T023 → T024.
- **US2 (Phase 3)** needs US1 (it resumes and lists US1's jobs). T026 in parallel with T025.
- **US3 (Phase 4)** needs US1; independent of US2 except sharing `VideoGenerationPage.vue`.
- **US4 (Phase 5)** needs US1 (T007 already hides the backend).
- **Polish (Phase 6)** after the stories it documents; T031 and T032 in parallel.

## Parallel Examples

```text
Phase 1:  T008 videogen seam | T012 shared helpers | T013 + T014 deps and constitution  (beside the T001→T011 chain)
US1:      T015 inputs | T016 prompt | T017 naming | T021 client + catalog
US2:      T025 resume (backend) | T026 recentClips.ts (frontend)
Polish:   T031 docker smoke | T032 docs
```

## Implementation Strategy

1. **Foundation first**: finish Phase 1 and confirm the two existing tools are unchanged for users
   (`task test`, `task e2e`); the image decodes HEIC (T011) before any UI is built.
2. **MVP = US1**: generate, preview, download and share a clip via the direct link; validate with
   the fake provider, then once with the live test (≈ $0.60).
3. **Increment**: US2 (Recent clips + restart resume) → US3 (Try again) → US4 (home card / hidden).
4. **Polish**: clip check and image size, docs; `task changelog` notes the breaking
   `POST /api/jobs` removal.

## Phase 7: Convergence

- [x] T033 Report the finished clip's actual orientation and resolution, derived from the probed width/height in `backend/src/videokit/tools/video_generation/pipeline.py`, and show them in `frontend/src/tools/video-generation/JobPage.vue`, with tests in `backend/tests/integration/test_video_generation.py` and `frontend/tests/unit/video-generation-job.spec.ts` for a provider clip that differs from the request, per spec US1 AC5 / Edge Cases (partial)
- [x] T034 Force 8-bit 4:2:0 output (`-pix_fmt yuv420p`) in the libx264 re-encode branch of `finalize_clip` in `backend/src/videokit/core/media.py`, with a 10-bit or yuv444p source case in `backend/tests/integration/test_media.py`, per spec FR-009 / research V6 (partial)
- [x] T035 Add native drag-and-drop reordering for pointer devices next to the move up/down buttons in `frontend/src/tools/video-generation/PicturePicker.vue`, with a reorder-by-drop case in `frontend/tests/unit/video-generation.spec.ts`, per research V12 (missing)
- [x] T036 Either map a provider-side `cancelled` interaction to a cancelled job in `backend/src/videokit/core/videogen/omni.py` and `backend/src/videokit/tools/video_generation/pipeline.py` (tests in `backend/tests/unit/test_omni_mapping.py` and `backend/tests/integration/test_video_generation.py`), or record the `video_failed` mapping as the decision in `specs/004-video-generation/research.md` V5, per research V5 (contradicts)
