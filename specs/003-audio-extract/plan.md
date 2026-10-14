# Implementation Plan: Audio Extract Tool

**Branch**: `003-audio-extract` | **Date**: 2026-10-14 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/003-audio-extract/spec.md`

## Summary

Add the second VideoKit tool, Audio Extract: upload one video, get its main audio track as a
192 kbps MP3 named after the video, downloadable (and shareable to apps on phones) until the job
expires. The tool is a new `tools/audio_extract` package plus a `frontend/src/tools/audio-extract`
module. It needs four generic platform capabilities that core lacks today: an MP3 encode in
`MediaService` (ffmpeg stays in core), single-stage jobs (`extracting → done`), the original file
name kept with the job, and one queue lane per tool so an MP3 never waits behind a YouTube Prep
job. Downloads are served by a tool route through the existing `JobAccess` seam. Decisions:
[research.md](research.md) A1–A12.

## Technical Context

**Language/Version**: Python 3.14 (backend); TypeScript 6 / Vue 3 (frontend) — unchanged

**Primary Dependencies**: FastAPI/Starlette 1.x (`FileResponse` with range support), Pydantic;
Vue 3, vue-router, vue-i18n, tus-js-client; ffmpeg/ffprobe with libmp3lame (already in the image).
No new dependencies.

**Storage**: SQLite `jobs` table gains nullable `source_name`; per-job files `audio.mp3`,
`result.json` under `DATA_DIR/jobs/<id>/`

**Testing**: pytest (+ ffmpeg-generated fixtures via `make_video`), Vitest (stubbed
`navigator.share`/`canShare`), Playwright e2e on the four existing projects; 80 % coverage per stack

**Target Platform**: Linux container (amd64), CPU-only is enough for this tool; clients: desktop
browsers, iOS Safari, Android Chrome ≥ 360 px

**Project Type**: Web application (backend + SPA in one container)

**Performance Goals**: 30-minute video → MP3 ready < 1 min after upload on a typical CPU-only host,
also while a YouTube Prep job runs (SC-002)

**Constraints**: no AI provider use and no cost (FR-007); source deleted right after encoding
(FR-012); original file name never logged (FR-014); no horizontal scroll at 360 px, ≥ 44×44 px
targets (FR-016)

**Scale/Scope**: single operator, small group; one Audio Extract job and one YouTube Prep job at a
time; MP3 up to ~130 MB for a 90-minute video

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Open-Source Hygiene | PASS | Fixtures generated with ffmpeg `lavfi`; examples use `trip.mp4`, `localhost`. |
| II. Simplicity First | PASS | No new dependency or infrastructure; lanes reuse the in-process worker; download via `FileResponse`. |
| III. Multi-Tool Modularity | PASS with note | Core still imports no tool and the tool uses only `Tool`/`CoreContext`/`JobHandle`/`JobAccess`. This feature does change core, adding four generic capabilities (see Complexity Tracking); after it, a similar file-producing tool needs no core change. ffmpeg stays in core (`MediaService.extract_mp3`). |
| IV. Provider Abstraction | PASS | The tool uses no provider; startup validation unchanged (clarification 2026-10-14). |
| V. Test-First | PASS | Every task test-first; fixtures need no keys/GPU/network. |
| VI. Privacy & Data Lifecycle | PASS | Source deleted after encoding; MP3 purged with the job TTL; metadata stripped from the MP3 (A3); file name stored only with the job and never logged. |
| VII. Security Boundary | PASS | Same tus chunked upload; download is a plain GET with no auth, like the job link. |

Post-design re-check (after Phase 1): unchanged — PASS.

## Project Structure

### Documentation (this feature)

```text
specs/003-audio-extract/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── http-api.md
│   └── internal-interfaces.md
├── checklists/requirements.md
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
backend/src/videokit/
  core/
    db.py                # + source_name column (in-place migration)
    jobs.py              # Job.source_name; create(source_name); extracting→done; per-tool next_queued/queue_position
    worker.py            # one loop per tool; _current set; WorkerJobHandle.source_name
    registry.py          # JobHandle.source_name in the Protocol
    api.py               # POST /api/jobs file_name (optional, sanitized)
    media.py             # AudioTrack, extract_mp3 (track choice, channels, rate, no metadata)
  tools/audio_extract/
    __init__.py          # TOOL = AudioExtractTool()
    naming.py            # download_name()
    routes.py            # GET/HEAD /jobs/{job_id}/audio
backend/tests/
  conftest.py            # make_video: no audio, multi-track, 5.1, 96 kHz, metadata tag
  unit/test_jobs.py, unit/test_audio_extract_naming.py, unit/test_registry.py
  integration/test_media.py, test_worker.py, test_api_platform.py,
              test_audio_extract.py (end-to-end through upload → download), test_log_hygiene.py
frontend/src/
  components/UploadPanel.vue          # send file_name
  i18n/en.json                        # audioExtract.*, tools.audio_extract.*, job.failures.no_audio
  tools/audio-extract/
    index.ts, types.ts
    AudioExtractPage.vue              # heading, intro, UploadPanel
    JobPage.vue                       # status, duration/size, Download, Share
    ShareButton.vue                   # Web Share with files (A9)
frontend/tests/
  unit/audio-extract.spec.ts, unit/share.spec.ts, unit/upload.spec.ts
  e2e/audio-extract.spec.ts
README.md, .claude/CLAUDE.md          # list the tool; layout
```

**Structure Decision**: Existing web-application layout (backend + frontend in one container).
The tool follows the YouTube Prep pattern: backend package under `videokit/tools/<id>/` exposing
`TOOL`, frontend module under `frontend/src/tools/<kebab-id>/` auto-discovered by
`import.meta.glob`.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| Adding this tool changes core (Constitution III: "adding a tool MUST NOT require changes to core") | The platform lacks generic capabilities the spec requires: MP3 encoding (ffmpeg is core-owned), a single-stage state path, the original file name (FR-005), per-tool queue lanes (FR-018). Each is tool-agnostic and reusable; no tool-specific code enters core. | Doing them inside the tool would break other rules: tools may not run ffmpeg or touch the job store; skipping them would fail FR-005/FR-018. |
