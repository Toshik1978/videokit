---

description: "Task list for the Audio Extract tool"
---

# Tasks: Audio Extract Tool

**Input**: Design documents from `specs/003-audio-extract/`

**Prerequisites**: plan.md, spec.md, research.md (A1–A12), data-model.md, contracts/, quickstart.md

**Tests**: Every task is implemented test-first (Constitution V): each task writes its own failing
tests in the listed test file(s) before the implementation. There are no standalone test tasks.
Default tests must pass without API keys, network access to AI providers, or a GPU, and keep line
coverage ≥ 80 % per stack.

**Organization**: Tasks are grouped by user story so each story is an independently testable
increment. Each task is one checklist line (what + main files) followed by indented detail
bullets that are part of the task.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: User story the task belongs to (US1–US3 from spec.md)
- Paths: backend `backend/src/videokit/`, backend tests `backend/tests/`, frontend `frontend/src/`

---

## Phase 1: Foundational (platform capabilities, blocking all stories)

**Purpose**: The generic core capabilities the tool needs (plan.md Complexity Tracking). No setup
phase: the project, toolchain and CI already exist.

- [x] T001 Allow single-stage jobs (`extracting → done`) in `backend/src/videokit/core/jobs.py`
  - Add `S.DONE` to `TRANSITIONS[S.EXTRACTING]` (data-model.md state table); all other edges unchanged.
  - The worker's existing `_finish` then moves a job that is still `extracting` after `Tool.run` returns to `done`.
  - Tests: `backend/tests/unit/test_jobs.py` (the new edge is allowed; `extracting → generating` and other undeclared edges still raise `InvalidTransition`), `backend/tests/integration/test_worker.py` (a fake tool that only sets `extracting` ends `done`).
- [x] T002 Run one FIFO lane per tool in `backend/src/videokit/core/jobs.py` and `backend/src/videokit/core/worker.py` (research A1, FR-018)
  - `JobStore.next_queued(tool_id: str) -> str | None`: oldest `queued` job of that tool by `queued_at, rowid`.
  - `JobStore.queue_position(job_id)`: "1 + queued jobs of the **same tool** ahead of it"; 0 when not queued. `queue_length()` stays the total over all lanes (`/api/health`).
  - `Worker.start()` starts one loop task per registered tool id; each loop runs its tool's jobs one at a time; `notify()` wakes all loops; `stop()` cancels all of them. Replace `_current: str | None` with a set of job ids being started so `cancel(job_id)` keeps its `active=` race protection per job.
  - YouTube Prep behaviour is unchanged: two YouTube Prep jobs never run at the same time.
  - Tests: `backend/tests/unit/test_jobs.py` (lane ordering, per-tool positions, total length), `backend/tests/integration/test_worker.py` (with two fake tools: a blocked job of tool A does not delay tool B's job; a second job of tool A waits for the first; cancel of a queued job in each lane; worker stop cancels every loop).
- [x] T003 Keep the original upload file name with the job in `backend/src/videokit/core/db.py`, `jobs.py`, `api.py`, `registry.py` and `worker.py` (research A5)
  - `db.py`: add `("source_name", "TEXT")` to `_ADDED_COLUMNS` and to `SCHEMA` (nullable).
  - `jobs.py`: `Job.source_name: str | None`; `JobStore.create(tool_id, upload_length, source_name: str | None = None)`.
  - `api.py`: `CreateJobRequest.file_name: str | None` — optional, "at most 1024 characters" (longer → `422 invalid_request`); keep only the last path component (split on `/` and `\`), remove control characters, trim surrounding whitespace, cut to 255 characters; empty → `None`. Not returned by `GET /api/jobs/{id}`.
  - `registry.py`: `JobHandle.source_name` read-only property in the Protocol; `worker.py`: `WorkerJobHandle.source_name` reads it from the store.
  - Update `specs/001-videokit-youtube-prep/contracts/http-api.md` `POST /api/jobs` and `contracts/internal-interfaces.md` `JobHandle` to match `specs/003-audio-extract/contracts/`.
  - Tests: `backend/tests/unit/test_jobs.py` (stored and read back; migration adds the column to a database created without it), `backend/tests/integration/test_api_platform.py` (sanitizing table incl. `C:\\Users\\x\\trip.mp4` → `trip.mp4`, non-Latin names kept, 1025 chars → 422, absent → `None`), `backend/tests/integration/test_log_hygiene.py` (a job created with a marker file name and run to completion: the marker appears in no log record).
- [x] T004 [P] Encode a video's main audio track to MP3 in `backend/src/videokit/core/media.py` with fixture extensions in `backend/tests/conftest.py` (research A3, A4, A12)
  - `AudioTrack(file: str, duration_s: float, size_bytes: int)` dataclass; `MediaService.extract_mp3(job_dir) -> AudioTrack`.
  - Probe the source (unplayable → `not_a_video`); choose the first audio stream with `disposition.default == 1`, else the first audio stream; none → `AppError("no_audio", "The video has no audio track.", 422)`.
  - `ffmpeg -v error -y -i source.bin -map 0:<idx> -vn -sn -dn -map_metadata -1 -map_chapters -1 -c:a libmp3lame -b:a 192k [-ac 2 when channels > 2] [-ar 48000 when sample rate > 48000] audio.mp3`; ffmpeg failure → `AppError("extraction_failed", "The video could not be processed.", 500)`.
  - Probe the produced MP3 for `duration_s`; `size_bytes` from the file; delete `source.bin` on success.
  - `make_video` gains options for: two audio tracks (first mono, second stereo and flagged default), a 6-channel track, a 96 kHz track, a `location` metadata tag; existing callers unchanged.
  - Tests: `backend/tests/integration/test_media.py` — codec `mp3`, bitrate ≈ 192 kb/s, mono stays mono, default stereo track chosen, 6 channels → 2, 96 kHz → 48 kHz, no `location` tag in the output, duration within 1 s of the video, `source.bin` deleted, no-audio → `no_audio`, text file → `not_a_video`.
- [x] T005 [P] Send the picked file's name when creating a job in `frontend/src/components/UploadPanel.vue`
  - `POST /api/jobs` body gains `file_name: file.name` for every tool; the resume path (stored entry) is unchanged.
  - Tests: `frontend/tests/unit/upload.spec.ts` asserts the request body contains `file_name`.

**Checkpoint**: Core supports single-stage jobs, per-tool lanes, file names and MP3 encoding; YouTube Prep still passes its suites.

---

## Phase 2: User Story 1 - Get a video's audio as an MP3 (Priority: P1) 🎯 MVP

**Goal**: Upload one video, download (or share) `<name>.mp3`.

**Independent Test**: Upload a short video with audio; the job reaches `done`; Download saves `<video name>.mp3`, a playable 192 kbps MP3 whose duration matches the video's.

- [x] T006 [P] [US1] Implement download naming in `backend/src/videokit/tools/audio_extract/naming.py` (research A6, FR-005)
  - `download_name(source_name: str | None) -> str`: drop the last extension when the text after the last dot is 1–8 letters or digits and the stem before it is not empty; replace `/ \ : * ? " < > |` and control characters with `_`; strip leading/trailing spaces and dots; cut the stem to 150 characters; empty stem → `audio`; append `.mp3`; keep non-Latin characters.
  - Tests: `backend/tests/unit/test_audio_extract_naming.py` — table incl. `trip.mp4` → `trip.mp3`, `Поездка в горы.final.mov` → `Поездка в горы.final.mp3`, `clip` → `clip.mp3`, `v1.2 final` → `v1.2 final.mp3`, `???.mp4` → `___.mp3`, `None`/`""`/`.mp4` → `audio.mp3`, a 300-character name.
- [x] T007 [US1] Implement the Audio Extract tool and its download route in `backend/src/videokit/tools/audio_extract/__init__.py` and `backend/src/videokit/tools/audio_extract/routes.py` (contracts/, research A7, A10)
  - `AudioExtractTool`: `id = "audio_extract"`, `name = "Audio Extract"`, `description = "Get a video's audio as an MP3."`, `router`, `run`: `set_state("extracting")`, `check_cancelled()`, `ctx.media.extract_mp3(job.dir)` in a thread, write `result.json` `{file, download_name, duration_s, size_bytes, bitrate_kbps: 192}` with `download_name(job.source_name)`. No provider or usage calls.
  - `GET` and `HEAD /api/tools/audio_extract/jobs/{job_id}/audio`: `JobAccess.get` → `404 job_not_found` when unknown, expired or `tool_id != "audio_extract"`; `409 job_not_done` when not `done`; `404 job_not_found` when `audio.mp3` is missing; else `FileResponse(media_type="audio/mpeg", filename=download_name)` (attachment; `filename*` for non-ASCII; range → `206`).
  - Tests: `backend/tests/integration/test_audio_extract.py` — full flow through `POST /api/jobs` (with `file_name`) + tus upload + worker → `done`, `result` matches contract, `usage.cost_usd == 0`, `providers == {}`; download headers incl. a non-ASCII name, a `Range` request → 206; 404 for unknown/expired/youtube_prep job; 409 while queued; an Audio Extract job finishes while a fake YouTube Prep job is blocked (FR-018); cancel during `extracting` → `cancelled` and directory removed; the tool works with fake providers that report not-ready.
- [x] T008 [P] [US1] Add the Audio Extract pages in `frontend/src/tools/audio-extract/` (`index.ts`, `types.ts`, `AudioExtractPage.vue`, `JobPage.vue`) and `frontend/src/i18n/en.json` (research A8)
  - `index.ts`: `id = 'audio_extract'`, routes `/tools/audio-extract` and `/tools/audio-extract/jobs/:id`.
  - `AudioExtractPage.vue`: heading, one-line intro, `UploadPanel tool-id="audio_extract"`, navigate to the job page on upload.
  - `JobPage.vue`: polls `GET /api/jobs/{id}` every 2 s until terminal (as YouTube Prep's page); `JobStatus` for stages and cancel; a job of another tool or 404 → `job.expired`; when `done`: duration (`m:ss`/`h:mm:ss`) and size (MB) and a Download link (`<a href download>`, ≥ 44×44 px) whose click re-checks the job and shows `job.expired` on 404, else `window.location.assign(url)`. No usage/cost footer.
  - All strings under `audioExtract.*` in `en.json`.
  - Tests: `frontend/tests/unit/audio-extract.spec.ts` (stages, done view with duration/size, download href, expired-on-click path, other-tool job → expired).
- [x] T009 [US1] Add the Share button and the end-to-end flow in `frontend/src/tools/audio-extract/ShareButton.vue`, `frontend/src/tools/audio-extract/JobPage.vue` and `frontend/tests/e2e/audio-extract.spec.ts` (research A9, FR-017)
  - Shown only when `navigator.canShare?.({ files: [new File([], 'a.mp3', { type: 'audio/mpeg' })] })` is true. Tap: fetch the MP3 (label "Preparing…"), `new File([blob], result.download_name, { type: 'audio/mpeg' })`, `navigator.share({ files })`. `NotAllowedError` → keep the file, show "Ready — tap Share again", next tap shares at once; `AbortError` silent; other errors → `errors.generic`. Strings in `en.json`.
  - Tests: `frontend/tests/unit/share.spec.ts` with stubbed `navigator` (hidden without support, shares a file with the download name and type, second-tap path, abort is silent, fetch failure → generic error); `frontend/tests/e2e/audio-extract.spec.ts` on the four Playwright projects (upload a generated video → done → the download's suggested file name is `<name>.mp3`; no horizontal scroll at 360 px; Download ≥ 44×44 px).

**Checkpoint**: US1 is usable end to end: upload, download, share on phones.

---

## Phase 3: User Story 2 - Clear outcome for videos without usable audio (Priority: P2)

**Goal**: No-audio and non-video uploads end with their own readable message and no file.

**Independent Test**: Upload a no-audio video and a text file; each ends `failed` with its catalog message and no Download button.

- [x] T010 [US2] Show the "no audio" failure in `frontend/src/i18n/en.json` and `frontend/src/tools/audio-extract/JobPage.vue`, with the backend path covered in `backend/tests/integration/test_audio_extract.py`
  - Catalog: `job.failures.no_audio` (e.g. "This video has no audio track, so there is nothing to extract."). `not_a_video` reuses the existing entry.
  - The failed page shows the catalog failure (via `JobStatus`/`useMessages`) and no Download or Share button.
  - Add `no_audio` to the failure code list in `specs/001-videokit-youtube-prep/contracts/http-api.md`.
  - Tests: `backend/tests/integration/test_audio_extract.py` (no-audio video → `failed`, `failure_code == "no_audio"`, `source.bin` deleted, download → 409; text file → `not_a_video`; multi-track video → MP3 has the default track's channel count), `frontend/tests/unit/audio-extract.spec.ts` (failed `no_audio` job shows the catalog text, no Download/Share).

**Checkpoint**: US1 and US2 both work independently.

---

## Phase 4: User Story 3 - Audio Extract on the home page (Priority: P3)

**Goal**: The home page shows Audio Extract next to YouTube Prep.

**Independent Test**: Open `/`; both cards are shown; the Audio Extract card opens `/tools/audio-extract`.

- [x] T011 [US3] Add the home card labels in `frontend/src/tools/audio-extract/index.ts` and `frontend/src/i18n/en.json`
  - `labels = { name: 'tools.audio_extract.name', description: 'tools.audio_extract.description' }` with catalog entries "Audio Extract" / "Get a video's audio as an MP3.".
  - Tests: `frontend/tests/unit/home.spec.ts` (both cards from the real tool modules, Audio Extract card links to `/tools/audio-extract`), `frontend/tests/e2e/audio-extract.spec.ts` (home → Audio Extract card → upload page).

**Checkpoint**: All stories work independently.

---

## Phase 5: Polish & Cross-Cutting Concerns

- [x] T012 [P] Check MP3 encoding in the image smoke test in `scripts/docker_smoke.sh`
  - Inside the image, encode a generated `lavfi` tone to MP3 with `libmp3lame` at 192k and probe it (fails the smoke run if the encoder is missing).
  - Tests: `backend/tests/unit/test_docker_smoke.py` asserts the script contains the MP3 encode check.
- [x] T013 [P] Document the tool in `README.md` and `.claude/CLAUDE.md`
  - README: a short Audio Extract section (what it does, MP3 192 kbps, Share on phones, retention like YouTube Prep, no AI or cost).
  - CLAUDE.md: layout (`tools/audio_extract/`, `frontend/src/tools/audio-extract/`), and architecture notes: per-tool queue lanes, `extracting → done` for single-stage tools, `source_name` (never logged), `MediaService.extract_mp3`, specs list incl. `specs/003-audio-extract/`.
  - Verify: `task lint` (prettier/markdown) and `scripts/check_denylist.py` pass.

---

## Dependencies & Execution Order

- T001 → T002 → T003 (shared `jobs.py` / `worker.py`); T004 and T005 run in parallel with them.
- US1: T006 any time; T007 after T001, T003, T004, T006; T008 after T005; T009 after T007 and T008.
- US2: T010 after T007 and T008. US3: T011 after T008 (and T009 for the shared e2e file).
- Polish: T012, T013 after the stories they describe.

```text
Phase 1:  T001 → T002 → T003   |   T004 media.py   |   T005 UploadPanel
Phase 2:  T006 naming.py  →  T007 tool + route   |   T008 pages  →  T009 share + e2e
Phase 3:  T010            Phase 4: T011            Phase 5: T012 | T013
```

## Implementation Strategy

### MVP First (User Story 1 only)

1. Phase 1 Foundational → Phase 2 (US1)
2. Validate with `quickstart.md` §1–§2 steps 1–5: upload, download, reopen, lanes
3. Use it: extract a real video's audio and send it to a messenger from a phone

### Incremental Delivery

Foundational → US1 (MVP) → US2 no-audio message → US3 home card → Polish (smoke check, docs).
Each phase is one commit and leaves the app working.

## Phase 6: Convergence

- [x] T014 Keep the MP3 at 192 kbps for sources sampled below 32 kHz in `backend/src/videokit/core/media.py` per FR-002 / SC-003 (partial)
  - libmp3lame silently lowers the bitrate to 160 kbps for 16, 22.05 and 24 kHz input while `result.json` still reports `bitrate_kbps: 192`. In `MediaService.extract_mp3`, add `-ar 44100` when the chosen track's sample rate is below 32000 (keep `-ar 48000` above 48000; leave 32–48 kHz unchanged).
  - Tests: `backend/tests/integration/test_media.py` — extend `make_video` in `backend/tests/conftest.py` with an audio sample-rate option; a 16 kHz source yields an MP3 at 44.1 kHz with bitrate ≈ 192 kb/s.
- [x] T015 Fall back to `audio.mp3` when a file name has no letter or digit in `backend/src/videokit/tools/audio_extract/naming.py` per spec Edge Cases (contradicts)
  - Spec: a name "made only of characters unsafe in file names" falls back to `audio.mp3`; today `???.mp4` becomes `___.mp3`. After replacement and stripping, an empty stem or a stem with no Unicode letter or digit becomes `audio`.
  - Tests: `backend/tests/unit/test_audio_extract_naming.py` — change the `???.mp4` row to `audio.mp3`; add `___.mov` → `audio.mp3` and `a?b.mp4` → `a_b.mp3`.
- [x] T016 Make the shared extracting stage label and extraction failure message tool-neutral in `frontend/src/i18n/en.json` per FR-009 (partial)
  - `job.state.extracting` ("Extracting audio and frames") and `job.failures.extraction_failed` ("Audio and frames could not be extracted from the video.") are shown on Audio Extract jobs, which extract no frames. Reword both so they fit both tools (e.g. "Extracting" / "The video could not be processed.").
  - Tests: `frontend/tests/unit/audio-extract.spec.ts` (extracting stage and a failed `extraction_failed` job show the new catalog text) and update any YouTube Prep test asserting the old wording.
