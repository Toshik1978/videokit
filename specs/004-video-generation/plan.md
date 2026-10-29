# Implementation Plan: Video Generation Tool

**Branch**: `004-video-generation` | **Date**: 2026-10-29 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/004-video-generation/spec.md`

## Summary

Add the third VideoKit tool, Video Generation: a prompt plus 0–7 ordered pictures produce one
silent 5–10 s clip (9:16 or 16:9, 1080p or 720p) via Google's Gemini Omni 1.1 Flash, with a cost
estimate, Download/Share/Try again, and a device-local Recent clips list. Up to three clips
generate at once and a running generation survives a restart without being paid twice.

The feature first refactors job creation so **every tool owns its creation route** on shared core
building blocks (approach chosen with the user, clarification 2026-10-29): `POST /api/jobs` is
removed, YouTube Prep and Audio Extract move to `POST /api/tools/<id>/jobs` with no user-visible
change, and the upload-complete check becomes a tool hook. Core also gains a video provider chain,
per-tool lane width, per-tool restart behaviour and tool enablement. Decisions:
[research.md](research.md) V1–V14.

### Approaches considered (recorded from brainstorming)

| Approach | Trade-off | Outcome |
|----------|-----------|---------|
| **Each tool decides** — tool-owned creation routes over core building blocks (`create`, tus upload, `complete`, `accept` hook, restart hook) | Refactors the two existing tools' creation path once; afterwards a new input kind needs no core change | **Chosen** |
| Second creation path only for the new tool; existing tools stay on `POST /api/jobs` | Smallest diff, but two ways to create jobs | Rejected by the user |
| Core interprets declared tool inputs (`inputs: [video]`, `[images, text]`) | One endpoint, but core learns every input kind — a core change per new kind | Rejected |
| Bundle pictures through the existing tus upload | No route change, but the video probe rejects it and a format quirk hides in a generic path | Rejected |

## Technical Context

**Language/Version**: Python 3.14 (backend); TypeScript 6 / Vue 3 (frontend) — unchanged

**Primary Dependencies**: FastAPI/Starlette, Pydantic; **new** `google-genai >=2.28` (Omni via the
Interactions API, V2) and `python-multipart` (form parsing, V14); ffmpeg/ffprobe (Debian 7.1 in the
image) for picture normalization and clip finalization; `pillow-heif` for HEIC pictures (tiled
phone photos, V7); Vue 3, vue-router, vue-i18n,
tus-js-client. No new frontend dependency.

**Storage**: SQLite `jobs` (no new columns; `upload_length = 0` allowed; `usage.video_output_tokens`);
per-job files `inputs.json`, `pictures/<n>.jpg`, `submit.json`, `operation.json`, `clip.mp4`,
`thumbnail.jpg`, `result.json` (data-model.md); Recent clips in the browser's `localStorage`

**Testing**: pytest with a fake video provider (`tests/fakes_videogen.py`) and ffmpeg-generated
pictures/clips (incl. a HEIC fixture where ffmpeg can produce one); Vitest; Playwright e2e on the
four existing projects; opt-in `live` test against Omni; 80 % coverage per stack

**Target Platform**: Linux container (amd64), CPU-only is enough for this tool; clients: iOS
Safari and Android Chrome ≥ 360 px first, desktop browsers

**Project Type**: Web application (backend + SPA in one container)

**Performance Goals**: no host-side target beyond keeping requests responsive; picture
normalization and clip remux are short ffmpeg runs next to Google's generation time (minutes)

**Constraints**: no audio in any delivered clip (FR-006); never two paid submissions for one job
(FR-019/FR-020); prompts, pictures and provider messages never logged (FR-025); multipart body
< 75 MB (fits 100 MB proxies); Tier 1 spend cap $10 per rolling 10 min (V4); image ≤ 1.25 GB
compressed

**Scale/Scope**: single operator household; ≤ 3 concurrent generations; clips ≤ ~20 MB each

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Open-Source Hygiene | PASS | Fixtures generated with ffmpeg; examples use `<your-api-key>`, placeholder prompts; no deployment details (the operator's tunnel is never named). |
| II. Simplicity First | PASS with note | Two small dependencies, each for a documented need (V2, V14); no new infrastructure — polling runs in the in-process worker. Lane width and restart behaviour are per-tool attributes, not a framework. |
| III. Multi-Tool Modularity | PASS with note | Core changes (see Complexity Tracking) are tool-agnostic and *restore* the rule: after V1, a tool with a new input kind needs no core change. Core imports no tool; the tool uses only `Tool`/`CoreContext`/`JobHandle`/`JobAccess`; ffmpeg stays in `MediaService`; the vendor SDK stays in `core/videogen`. |
| IV. Provider Abstraction | PASS with amendment | Video generation sits behind an ordered `VIDEO_PROVIDERS` chain with fallback before acceptance; provider used is recorded; startup validates configured providers. The constitution names only LLM and transcription providers — a MINOR amendment adds video generation to Principle IV and the Technology list (task in this feature). |
| V. Test-First | PASS | Every task test-first; fake provider, no keys/GPU/network; live test opt-in. |
| VI. Privacy & Data Lifecycle | PASS with note | Pictures are kept until the job's TTL for Try again (clarification 2026-10-29), then purged with the job; EXIF/GPS stripped before anything leaves the host; the stored provider interaction is deleted after download (V6); media go only to the operator-configured provider. |
| VII. Security Boundary | PASS with note | No auth added. Pictures use a capped multipart request (< 75 MB) instead of resumable upload — within 100 MB proxy limits, which is what the principle protects; video uploads stay on tus. |

Post-design re-check (after Phase 1): unchanged — PASS with the notes above.

## Project Structure

### Documentation (this feature)

```text
specs/004-video-generation/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── http-api.md
│   ├── internal-interfaces.md
│   └── configuration.md
├── checklists/requirements.md
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
backend/src/videokit/
  main.py                  # mount/list/lane only enabled tools; build video chain; remove POST /api/jobs wiring
  core/
    api.py                 # drop POST /api/jobs; /api/tools filters enabled tools; health lists video providers
    registry.py            # Tool: concurrency, resume_after_restart, enabled(), accept(); JobAccess: create/job_dir/complete/discard; CoreContext.video
    jobs.py                # create(upload_length >= 0); QUEUED→GENERATING; recover_after_restart(resumable)
    worker.py              # lane slots per tool.concurrency; complete_inputs → tool.accept; resumable re-run first
    uploads.py             # create_upload_job helper; completion calls complete_inputs
    media.py               # probe_source, normalize_picture, finalize_clip (+ Clip)
    downloads.py           # content_disposition (moved from audio_extract)
    pricing.py             # video_output field; gemini-omni-1.1-flash prices
    config.py              # VIDEO_PROVIDERS, GEMINI_API_KEY, OMNI_MODEL + validation
    providers.py           # VIDEOS registry; build/status/problems include "video"
    llm/base.py            # Usage.video_output_tokens
    videogen/
      __init__.py, base.py # VideoRequest, Picture, Capabilities, Accepted, PollResult, VideoError, VideoGenerator
      chain.py             # VideoChain (fallback before acceptance; poll routes to acceptor)
      omni.py              # google-genai Interactions adapter, error mapping (V10), usage mapping (V8)
  tools/
    youtube_prep/          # + POST /jobs route, accept() = probe_source; concurrency 1; resume False
    audio_extract/         # same; routes use core.downloads.content_disposition
    video_generation/
      __init__.py          # TOOL (concurrency 3, resume_after_restart True, enabled iff video chain)
      routes.py            # POST /jobs (multipart), GET /options, /jobs/{id}/clip|thumbnail|inputs|pictures/{n}
      inputs.py            # validation + picture sniffing + inputs.json
      prompt.py            # reference declaration prefix (V7)
      pipeline.py          # submit/resume/poll/finalize/forget (V3, V5, V6)
      naming.py            # download_name from prompt (V13)
backend/tests/
  fakes_videogen.py
  unit/        test_registry.py, test_jobs.py, test_pricing.py, test_config.py,
               test_video_generation_{inputs,prompt,naming}.py, test_videogen_chain.py, test_omni_mapping.py
  integration/ test_worker.py, test_api_platform.py, test_uploads.py, test_media.py,
               test_youtube_prep_*.py / test_audio_extract.py (creation via tool route),
               test_video_generation.py (create → done, resume, concurrency, failures, hidden),
               test_log_hygiene.py
  live/        test_omni_live.py
frontend/src/
  api/client.ts                        # postForm helper
  components/UploadPanel.vue           # POST /api/tools/<id>/jobs
  components/ShareButton.vue           # moved from tools/audio-extract
  i18n/en.json                         # videoGeneration.*, tools.video_generation.*, job.failures.*, job.states.generating
  tools/video-generation/
    index.ts, types.ts, recentClips.ts, estimate.ts
    VideoGenerationPage.vue, PicturePicker.vue, JobPage.vue
frontend/tests/
  unit/video-generation*.spec.ts, unit/recent-clips.spec.ts, unit/upload.spec.ts
  e2e/video-generation.spec.ts
scripts/docker_smoke.sh                # HEIC decode + silent remux check
.specify/memory/constitution.md        # MINOR amendment (Principle IV, Technology)
README.md, .env.example, .claude/CLAUDE.md, CHANGELOG (via task changelog)
```

**Structure Decision**: Existing web-application layout. The tool follows the established pattern
(backend package `videokit/tools/video_generation/` exposing `TOOL`, frontend module
`frontend/src/tools/video-generation/` auto-discovered); core changes are generic building blocks
listed above.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| Adding this tool changes core (Constitution III) | Core lacks: upload-less job creation, a tool-owned completion check, a video provider seam, per-tool lane width and restart behaviour, tool enablement. All are tool-agnostic; V1 makes future input kinds core-free. | Doing them in the tool would mean the tool touching the job store, worker or vendor SDK directly, which the seam forbids. |
| Breaking API change: `POST /api/jobs` removed | One job-creation flow for all tools (user decision). | Keeping it as an alias leaves two creation paths, which the user rejected. Frontend and backend ship together, so no external client breaks silently; release notes say so. |
| Pictures retained until TTL (Constitution VI "delete as soon as no longer needed") | Try again must work after the user returns later (clarification 2026-10-29). | Deleting after generation makes Try again re-pick pictures from the gallery, the pain the user wants removed. |
| Non-resumable multipart upload for pictures (Constitution VII "large uploads use resumable transfer") | ≤ 75 MB per request fits the 100 MB proxy limits the principle targets; resumability buys little for ≤ 10 MB images. | tus per picture means 7 uploads per job and a multi-file job model (approach rejected above). |
| Constitution amendment (IV, Technology) | Video generation is a new provider kind with a new vendor SDK. | Leaving the constitution silent would make the new SDK an undocumented exception. |
