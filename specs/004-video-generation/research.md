# Research: Video Generation Tool

Decisions for [plan.md](plan.md). Numbering is local to this feature (V1–V14); `R*` refers to
`specs/001-videokit-youtube-prep/research.md`, `A*` to `specs/003-audio-extract/research.md`.
Provider facts were checked against Google's official documentation on 2026-10-29; items marked
**live-verify** are not stated there and are confirmed by the opt-in live test before release.

## V1. One job-creation flow for every tool (clarification 2026-10-29)

- **Decision**: Each tool owns its creation route in its own router
  (`POST /api/tools/<id>/jobs`), built on core building blocks exposed through `JobAccess`:
  `create(tool_id, upload_length=…, source_name=…)`, `job_dir`, `complete(job_id)` and
  `discard(job_id)`. Every job starts in `uploading` ("inputs not complete"). Completion is one
  core path — `Worker.complete_inputs(job_id)` — reached either by the last tus byte or by
  `JobAccess.complete`; it calls the tool's new blocking `accept(job, ctx)` hook (raise `AppError`
  to fail) and queues the job. The playable-video probe moves out of the worker into
  `MediaService.probe_source`, called from the two video tools' `accept`. `POST /api/jobs` is
  removed; the frontend `UploadPanel` posts to `/api/tools/<id>/jobs`. A small shared helper
  `core.uploads.create_upload_job` keeps the two video tools' routes to a few lines.
- **Rationale**: The user rejected a second path for the new tool. With the input format owned by
  the tool, a future tool with a new kind of input needs no core change — restoring Constitution
  III instead of bending it again. Jobs keep one store, one state machine, one completion gate.
- **Alternatives considered**: (a) a creation path only for Video Generation, existing tools on
  `POST /api/jobs` — two ways to create jobs, rejected by the user; (b) core interprets declared
  tool inputs (`inputs: [video]`, `[images, text]`) — core must learn every input kind, so each new
  kind is a core change; (c) pictures bundled through the existing tus upload — the video probe
  rejects them and a format quirk hides in a generic path; (d) a new `preparing` state for
  upload-less jobs — `uploading` with `upload_length = 0` already means "inputs not complete" and
  the restart re-fire of finished uploads (`finished_uploads`) covers a crash between writing inputs
  and queueing.

## V2. Provider: Gemini Omni 1.1 Flash via the Interactions API

- **Decision**: Model `gemini-omni-1.1-flash` (GA on the Gemini API since 2026-08-27, paid tier
  only) through the official `google-genai` SDK (`>=2.28`, the first release that knows the model;
  current 2.34). Requests use `client.interactions.create(model=…, input=[text, images…],
  response_format={"type": "video", "aspect_ratio": …, "resolution": …, "duration": …},
  background=True, store=True)`; the returned interaction `id` is the provider reference.
  Configuration: `VIDEO_PROVIDERS` (default empty; allowed `omni`), `GEMINI_API_KEY`,
  `OMNI_MODEL` (default `gemini-omni-1.1-flash`). `check()` is local only — key present, model
  named — like the Anthropic provider; `models.get` is free but a network call on every
  `/api/health`, and Google notes it does not prove billing works anyway.
- **Rationale**: Veo 3.1 preview models on the Gemini API shut down on 2026-10-22; Omni is the only
  video model left there, reachable with the user's Google AI Studio key. The official SDK matches
  how the other providers are integrated and brings retries for 429/5xx.
- **Alternatives considered**: Veo on Google Cloud's enterprise platform (needs a GCP project and
  service account, not an AI Studio key); raw REST over `httpx` (already installed; avoids ~38 MB
  but hand-maintains a young, changing API); aggregators (fal etc.) as primary — kept as a future
  fallback provider (spec Assumptions).
- **Footprint**: ~38 MB site-packages; new transitive packages `google-auth`, `cryptography`
  (~15 MB), `requests`, `tenacity`, `pyasn1`, `distro` (`httpx`, `pydantic`, `anyio`, `websockets`
  are already locked). The `local-tokenizer` extra (torch) MUST NOT be installed. The image size
  guard (`task docker:size`, 1.25 GB) stays the gate.
- **SDK gotcha**: `interactions.*` raises `google.genai._interactions.APIStatusError` subclasses
  (`BadRequestError`, `RateLimitError`, `PermissionDeniedError`, …) which are *not*
  `google.genai.errors.APIError`; the adapter catches both families and maps by HTTP status and
  error `code`, logging the exception type only.

## V3. Resume after restart without paying twice (FR-020, SC-003)

- **Decision**: `Tool.resume_after_restart` (Video Generation `True`, others `False`).
  `JobStore.recover_after_restart(resumable_tools)` fails only the other tools' waiting/running
  jobs. In `run`, the tool writes `submit.json` atomically *before* `submit`, and
  `operation.json` `{provider, ref}` atomically right after the provider accepted. On a re-run:
  `operation.json` present → poll it; only `submit.json` → fail `resume_impossible` (a request may
  have been accepted; never resubmit); neither → fresh submit. Lanes re-run resumable jobs found in
  a running state before taking queued ones.
- **Rationale**: Stored interactions are kept 55 days on the paid tier (operator may shorten to
  7/14/28 days in AI Studio), so polling by id after hours is supported. The two-file protocol
  makes "maybe charged" detectable without a provider-side idempotency key (none is documented).
- **Alternatives considered**: synchronous `background=False` calls (HTTP connections typically
  close after ~60 s; no resume); resubmitting interrupted jobs (could charge twice — spec forbids).

## V4. Concurrency: up to 3 per Video Generation lane (clarification 2026-10-29)

- **Decision**: `Tool.concurrency` (YouTube Prep 1, Audio Extract 1, Video Generation 3). Each lane
  runs `concurrency` slot tasks; a slot claims the oldest queued job of its tool that is not in the
  worker's `_current` set, under the existing worker lock, so two slots never start one job.
  `queue_position` is unchanged (counts queued, i.e. not-yet-claimed, jobs of the tool).
- **Rationale**: Generation runs at Google; the host only polls. Three keeps a burst of taps from
  multiplying the bill and stays inside Tier 1's rolling $10 per 10 minutes spend cap (≈ 6–8 clips
  at 1080p).
- **Alternatives considered**: a global concurrency setting (would let two transcriptions compete
  for the GPU); unlimited (spend-cap 429s and bill surprises).

## V5. Stages and polling

- **Decision**: Single stage `generating` (new edge `QUEUED → GENERATING`). The tool polls
  `interactions.get(id)` every 10 s with `asyncio.sleep` (no thread held), checking cancellation
  each round; terminal statuses `completed` → result, `failed`/`incomplete` → mapped error,
  `cancelled` → cancelled. Wait cap 20 minutes → `provider_timeout`. On user cancel the tool calls
  `interactions.cancel` best-effort; the UI warns the attempt may still be charged (FR-016).
- **Rationale**: Google publishes no latency figure ("varies with duration, resolution and load");
  20 minutes is generous for a ≤ 10 s clip. `generating` is already a catalog stage label.
- **Alternatives considered**: a new `rendering` state (new label, no user value).

## V6. Clip finalization: silent MP4 and thumbnail (FR-006, FR-009)

- **Decision**: Omni always generates audio — no documented switch, and no discount for silence.
  The prompt gets no audio wording; instead `MediaService.finalize_clip` writes `clip.mp4` with
  `ffmpeg -i raw -map 0:v:0 -an -sn -dn -map_metadata -1 -c:v copy -movflags +faststart` (re-encode
  with libx264 only if the stream is not H.264), probes the actual duration/size, and grabs
  `thumbnail.jpg` (frame at 0.5 s, ≤ 480 px). After a successful download the tool deletes the
  stored interaction (`interactions.delete`, best-effort) so Google does not keep the prompt and
  pictures for the retention window.
- **Rationale**: Audio removal must be guaranteed (SC-002), so it is done locally; copy keeps it
  fast and lossless. Deleting the interaction honours Constitution VI on the provider side.
- **Alternatives considered**: "No dialogue, no sound" prompt hints (not a guarantee, still billed).

## V7. Pictures: validation, normalization, references (FR-003, FR-004)

- **Decision**: The creation route streams the multipart body with a hard cap (7 × 10 MB + 1 MB),
  rejects more than 7 files or any file over 10 MB, detects the type by content (JPEG, PNG, WebP,
  HEIC/HEIF magic), and calls `MediaService.normalize_picture` → `pictures/<n>.jpg` (JPEG, all
  metadata incl. GPS stripped, long side ≤ 2048 px). Pictures go to the provider inline as base64
  `image` inputs in the user's order. The tool's prompt builder (deterministic code, tool-side)
  prefixes a declaration that maps the user's 1-based "picture N" to Omni's zero-indexed tags:
  `[# References <IMAGE_REF_0>@Picture1 <IMAGE_REF_1>@Picture2 …]`, followed by the user's prompt
  unchanged. No prefix when there are no pictures.
- **Rationale**: Omni documents `<IMAGE_REF_N>` tags and `@ImageN`-style declarations; mapping in
  code keeps the user's natural "picture 2" wording. Normalizing to JPEG gives one format for the
  provider, removes phone metadata before it leaves the host, and keeps the inline request small
  (well under the 100 MB inline limit; Google Cloud documents ≤ 10 images and 20 MB per inline
  image). HEIC is decoded with `pillow-heif` (libheif) inside `normalize_picture`, detected by the file's
  `ftyp` brand; every other format goes through ffmpeg. The first attempt, ffmpeg alone, was rejected after
  the T011 review: the image's Debian ffmpeg (7.1.5) reads a single-image HEIC, but phones store photos as
  **tile grids**, and the ffmpeg CLI does not compose them — it exposes the grid as a stream group but maps
  and decodes only the first tile (`-map 0:g:0` and `-filter_complex` on the group both bind to stream 0:0,
  checked on 7.1.5 and 8.0.1), so a photo would silently become a small top-left crop with no error. A
  hand-written xstack/crop composition was rejected as fragile (grid sizes, crop and rotation metadata).
  libheif composes the grid, applies rotation and mirroring, and `Image.open` keeps Pillow's decompression-bomb
  guard. Output is a JPEG (quality 92) written without EXIF, so GPS is dropped; the ICC profile is not carried
  over. The dependency is locked in `backend/uv.lock` (wheels bundle libheif; the compressed image stays
  at 0.96 GB of the 1.25 GB limit). The Docker smoke test decodes a single-image and a 2x2 tile-grid HEIC
  through `normalize_picture` and checks the full output size; ffmpeg has no HEIF muxer, so
  `backend/tests/heic_sample.py` assembles the samples (also used by the `make_picture("heic", grid=...)`
  fixture) around libx265 intra frames.
- **Alternatives considered**: sending originals (leaks EXIF/GPS, mixed formats); the Files API
  (48 h expiry, extra calls; only needed above 100 MB).

## V8. Cost: token-based usage and the estimate (FR-007, FR-022, SC-005)

- **Decision**: Price table entry `gemini-omni-1.1-flash`: `input` 1.50, `output` 9.00,
  `video_output` 17.50 ($ per 1M tokens). `Usage` and the job `usage` JSON gain
  `video_output_tokens` (default 0), filled from the interaction's
  `usage.output_tokens_by_modality.video` (other output tokens → `output_tokens`, input →
  `input_tokens`). If the response carries no usage (**live-verify**), the tool falls back to the
  documented rate: `duration × tokens_per_second[resolution]` and `1,120` tokens per input image.
  The provider's `capabilities()` exposes `video_tokens_per_second` = `{720p: 5792, 1080p: 8688}`
  and `tokens_per_image` = 1120; `GET …/options` returns `price_per_second_usd` computed from them
  and the (override-aware) price table, and the form adds pictures × image price.
- **Rationale**: Google bills Omni by token; per-second prices are a derived convenience (≈ $0.101
  /s at 720p, ≈ $0.152/s at 1080p). The same table drives estimate and actual cost, so SC-005 holds
  within rounding. Verify against the official pricing page before merging (CLAUDE.md "Costs").
- **Alternatives considered**: a per-second price field (does not match how Google bills; breaks
  when tokens/second change); no estimate (spec requires one).

## V9. Durations, aspect ratios, resolutions

- **Decision**: Offer whole seconds 5–10 (Omni supports 3–10 s at 24 fps), `9:16`/`16:9`,
  `1080p`/`720p` (1080p is upscaled by Google). The `duration` field is a string whose format
  (`"8s"` vs `"8"`) is undocumented — **live-verify** before release; the adapter owns the format
  (`omni._duration`, currently `"<n>s"`; `tests/live/test_omni_live.py` fails at submit if it is rejected).
  If a value is rejected, `capabilities().durations_s` narrows the list and the form follows it.
- **Rationale**: Matches the spec range; the form reads the list from `/options`, so a provider
  limit never needs a frontend change.

## V10. Failure mapping (FR-011, SC-006)

| Provider signal | `failure_code` | Fallback to next provider? |
|-----------------|----------------|----------------------------|
| HTTP 402 `payment_required`; 429 `quota_exceeded`; 400 `failed_precondition` (billing disabled) | `provider_out_of_credit` | yes, at submit only |
| Safety codes (`safety`, `prohibited_content`, `image_safety`, `image_prohibited_content`, `content_blocked`), as HTTP error or in `Interaction.errors[]` (**live-verify** which) | `content_blocked` | no |
| 400 `invalid_request` about an image | `invalid_picture` | no |
| 429 rate limit after SDK retries, 5xx, network errors, 401/403 | `provider_unavailable` | yes, at submit only |
| Status `failed`/`incomplete` without a safety code; no video in a completed interaction | `video_failed` | no (already accepted) |
| Wait cap reached | `provider_timeout` | no |
| `submit.json` without `operation.json` on resume | `resume_impossible` | no |

Messages are user-safe catalog texts; provider messages are never stored or shown (log hygiene).

## V11. Hidden when not configured (clarification 2026-10-29)

- **Decision**: `Tool.enabled(settings)`; `create_app` mounts the routers, `/api/tools` lists and
  the worker starts lanes only for enabled tools. Video Generation is enabled iff `VIDEO_PROVIDERS`
  is non-empty. The frontend module still ships; its page asks `/api/tools` and shows the existing
  not-found page when the tool is absent. A job left by a now-disabled tool follows today's
  "tool removed across a restart" path (failed, `internal_error`).
- **Rationale**: No dead-end card for operators who don't use it; startup never fails for an
  unused tool. A listed-but-unready provider still fails startup, as for the other chains.

## V12. Frontend

- **Decision**: `frontend/src/tools/video-generation/` with `VideoGenerationPage.vue` (Recent clips
  + form), `JobPage.vue` (status, preview `<video playsinline muted>`, Download, Share, Try again),
  `PicturePicker.vue` (thumbnails, move up/down buttons ≥ 44 px plus drag on desktop, remove),
  `recentClips.ts` (localStorage, data-model rules) and `estimate.ts`. Share reuses Audio Extract's
  Web Share approach — `ShareButton.vue` moves to `frontend/src/components/` so both tools use it.
  Try again reads `GET …/inputs`, fetches the pictures as blobs and opens the form prefilled.
- **Rationale**: Up/down buttons work on touch without a drag library (no new dependency);
  moving shared UI into `components/` mirrors the backend `content_disposition` move.

## V13. Download name

- **Decision**: First 60 characters of the prompt (whitespace collapsed), cut at a word boundary,
  file-unsafe characters replaced like Audio Extract's naming, non-Latin kept, `.mp4`; fallback
  `clip.mp4`. `content_disposition` moves to `videokit.core.downloads` for both tools.

## V14. Multipart parsing dependency

- **Decision**: Add `python-multipart` (FastAPI's form parser; small, pure Python). The route reads
  `await request.form(max_files=7, max_fields=8, max_part_size=10_000_000)` behind a body-size guard
  so oversized requests stop early.
- **Alternatives considered**: tus per picture (resumability not needed for ≤ 10 MB images; N
  uploads per job); base64 in JSON (+33 % size, whole body in memory).
