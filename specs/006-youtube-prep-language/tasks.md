---

description: "Task list for the YouTube Prep output language"
---

# Tasks: YouTube Prep Output Language

**Input**: Design documents from `specs/006-youtube-prep-language/`

**Prerequisites**: plan.md, spec.md, research.md (L1–L8), data-model.md, contracts/, quickstart.md

**Tests**: Every task is implemented test-first (Constitution V). Each task writes its own failing
tests in the listed test files before the implementation; there are no standalone test tasks.
Default tests must pass without API keys or network access to any provider, and line coverage must
stay at or above 80 % per stack.

**Organization**: Tasks are grouped by user story, so each story is an independently testable
increment. Each task is one checklist line (what, plus the main files) followed by indented detail
bullets that are part of the task.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: User story the task belongs to (US1–US3 from spec.md)
- Paths: backend `backend/src/videokit/`, backend tests `backend/tests/`, frontend `frontend/`

There is no setup or foundational phase: the project, the toolchain and tool-owned job creation
already exist.

---

## Phase 1: User Story 1 - Pick the output language once and keep it (Priority: P1) 🎯 MVP

**Goal**: a language picked on the upload page decides every output section, the device remembers
it, regeneration keeps it, and the result page shows it by its own name. In this phase "Same as the
speech" keeps today's English fallback for silent videos; Phase 2 replaces it.

**Independent Test**: with fake providers, a job created with `language: "de"` for a video with
speech in another language, and one for a silent video, both produce `result.language == "de"`.
The request turns tell the model to write in German, and the prefix is byte-identical to that of
an `auto` job. Regenerate keeps `de`. In the UI, the picker restores the stored choice after a
reload.

- [x] T001 [US1] Add the supported-language table and the job language setting in `backend/src/videokit/tools/youtube_prep/languages.py` (research L2, L4; data-model.md; contracts/internal-interfaces.md)
  - `LANGUAGES`: the 58 ISO 639-1 codes of research L2 mapped to English names. Also `AUTO = "auto"`, `LANGUAGE_JSON = "language.json"`, and the frozen `LanguageSetting(choice="auto", fallback="en")`.
  - `write_setting(job_dir, setting)` writes the file atomically (temp file plus replace). `read_setting(job_dir)` returns the default setting for a missing file and validates `choice ∈ {auto} ∪ LANGUAGES` and `fallback ∈ LANGUAGES`.
  - `instruction(code)` returns `"Write all output in <English name> (language code <code>)."`, using the code as the name when it is not in the table.
  - Tests: `backend/tests/unit/test_youtube_prep_languages.py`:
    - the table has exactly the research L2 codes, all two lower-case letters;
    - read/write round-trip, and a missing file gives the defaults;
    - the instruction text, including a code outside the table.
- [x] T002 [US1] Accept `language` on job creation and add `GET /options` in `backend/src/videokit/tools/youtube_prep/routes.py` (contracts/http-api.md; research L1)
  - `CreateJobRequest(uploads.CreateUploadRequest)` adds `language: str = "auto"`.
  - A value outside `{auto} ∪ LANGUAGES` gets `422 invalid_language` ("This language is not supported."), checked before any job exists.
  - After `uploads.create_upload_job`, write `language.json` in a worker thread. If the write fails, `JobAccess.discard` the job and re-raise.
  - `GET /api/tools/youtube_prep/options` returns `{"languages": sorted(LANGUAGES)}`.
  - Tests: `backend/tests/integration/test_youtube_prep_language_api.py`:
    - create with no language, with `auto` and with a code: 201, and the file holds the choice;
    - an unknown, upper-case or empty code gets 422 and no job row exists afterwards;
    - an older body without the field still works;
    - `/options` lists the codes;
    - Audio Extract's create route is unaffected.
- [x] T003 [US1] Resolve and use the job's output language in `backend/src/videokit/tools/youtube_prep/pipeline.py` (research L4; data-model.md table)
  - `output_language(setting, media)`: a picked code wins. Otherwise use the detected language, or the setting's fallback when there is no speech.
  - `turn_notes(media, language)` builds the duration note plus `languages.instruction(language)`.
  - `run` reads the setting once, and `build_result(normalized, media, language)` stores it as `result.language`.
  - `regenerate` uses `current.language`.
  - Remove `DEFAULT_LANGUAGE` from the output path and `ENGLISH_INSTRUCTION`. `_transcribe` keeps writing the detected language (`en` without speech) to `media.json` and the job row.
  - Tests: `backend/tests/integration/test_youtube_prep_pipeline.py`, `backend/tests/integration/test_regenerate.py`:
    - the resolution table, including a missing `language.json`;
    - generate, repair and regenerate requests share a byte-identical prefix (system, context text, frames) whatever the language;
    - the turns carry the instruction;
    - regenerate keeps `result.language` for a picked language and for an `auto` job.
- [x] T004 [P] [US1] Let the shared upload panel send extra create fields in `frontend/src/components/UploadPanel.vue` (research L7)
  - An optional prop `createFields?: Record<string, unknown>`, merged into the `POST /api/tools/<id>/jobs` body when creating a job. A resumed upload reuses its job and sends nothing new.
  - Tests: `frontend/tests/unit/upload.spec.ts` and `frontend/tests/unit/upload-resume.spec.ts`: the fields appear in the create body; without the prop the body is unchanged (`size`, `file_name`); a resume makes no create call.
- [x] T005 [P] [US1] Add language helpers in `frontend/src/tools/youtube-prep/language.ts` (research L3, L6)
  - `endonym(code)` uses `Intl.DisplayNames([code], {type: 'language'})` and falls back to the code when it is missing or throws.
  - `loadChoice(supported)` and `saveChoice(value)` use the `localStorage` key `videokit.youtube_prep.language`, with every access in try/catch. A missing, unsupported or unreadable value gives `"auto"`.
  - `sortedLanguages(codes)` sorts the codes by endonym with `localeCompare`.
  - Tests: `frontend/tests/unit/youtube-prep-language.spec.ts`:
    - endonym for a known code, and the fallback when `DisplayNames` throws;
    - load/save round-trip;
    - storage that throws on read and on write;
    - an unsupported stored value.
- [x] T006 [US1] Add the language picker to `frontend/src/tools/youtube-prep/YouTubePrepPage.vue` and `frontend/src/i18n/en.json` (FR-001, FR-003, FR-013)
  - Fetch `/api/tools/youtube_prep/options` on mount. Show a labelled `<select>` with "Same as the speech" first, then the endonyms in sorted order, pre-selected from `loadChoice`.
  - Save on change. Pass `{language}` to `UploadPanel` as `createFields`.
  - If the options request fails, show only "Same as the speech".
  - Map `invalid_language` to a catalog error key.
  - New `en.json` keys: picker label, "Same as the speech", and `errors.invalid_language`.
  - Tests: `frontend/tests/unit/youtube-prep-page.spec.ts`:
    - renders the options;
    - restores and saves the choice;
    - sends the language in the create fields;
    - degrades to auto-only when the options request fails.
- [x] T007 [US1] Show the output language on the result page in `frontend/src/tools/youtube-prep/JobPage.vue` and `frontend/src/i18n/en.json` (FR-008; clarification A)
  - Add an `Output language: {language}` line, using the endonym of `result.language`.
  - The `no_speech` notice text takes `{language}` and names the same endonym.
  - Tests: `frontend/tests/unit/job.spec.ts`: the line appears for every done job, and the no-speech notice names the language.

**Checkpoint**: US1 works on its own. A picked language decides the output, is remembered and
survives regeneration.

---

## Phase 2: User Story 2 - Keep "Same as the speech", with a sensible silent-video fallback (Priority: P2)

**Goal**: on "Same as the speech", a silent video comes out in the uploading browser's language
when that language is supported, else in English. Videos with speech behave exactly as before.

**Independent Test**: an `auto` job created with `fallback_language: "pt-BR"` for a silent video
produces `result.language == "pt"`. With `"xx"` or no value it produces `en`. An `auto` job with
speech produces the detected language, whatever the fallback.

- [ ] T008 [US2] Accept and normalize `fallback_language` in `backend/src/videokit/tools/youtube_prep/languages.py` and `backend/src/videokit/tools/youtube_prep/routes.py` (FR-007; research L5)
  - `normalize_fallback(value)` trims the value, splits it on `-` or `_`, lower-cases the base subtag and returns it if supported, else `"en"`. `None` gives `"en"`.
  - `CreateJobRequest.fallback_language: str | None = Field(None, max_length=35)`; longer values get `422 invalid_request`, and anything else never refuses.
  - Store the normalized value in `language.json`.
  - Tests:
    - `backend/tests/unit/test_youtube_prep_languages.py`: `pt-BR`, `PT_br`, ` de `, `xx`, empty and `None`;
    - `backend/tests/integration/test_youtube_prep_language_api.py`: the stored fallback, the too-long case, and a full silent-video job with fake providers getting the fallback language, while a job with speech ignores it.
- [ ] T009 [US2] Send the browser's language from `frontend/src/tools/youtube-prep/language.ts` and `frontend/src/tools/youtube-prep/YouTubePrepPage.vue`
  - `browserLanguage()` returns `navigator.language`, or `undefined` when there is none. The page passes `{language, fallback_language}` as `createFields`.
  - Tests: `frontend/tests/unit/youtube-prep-language.spec.ts`, `frontend/tests/unit/youtube-prep-page.spec.ts` (the create fields include `fallback_language`, and it is omitted when the browser reports none).

**Checkpoint**: US1 and US2 work together. Nobody gets English for a silent video unless English
is their language or no better choice is known.

---

## Phase 3: User Story 3 - A one-off language for a single video (Priority: P3)

**Goal**: each job keeps the language it was created with, even when the device's choice changes
afterwards or an interrupted upload is resumed.

**Independent Test**:

- Create job A with `de`, change the stored choice to `fr`, and create job B. A's result and its
  regenerations stay `de`, and B's are `fr`.
- Resume A's interrupted upload after the change. No new job is created, and A stays `de`.

- [ ] T010 [US3] Pin the per-job language through resume and later choice changes in `frontend/src/components/UploadPanel.vue`, with tests in `frontend/tests/unit/upload-resume.spec.ts` and `backend/tests/integration/test_youtube_prep_language_api.py` (FR-004; spec edge cases)
  - Frontend: a resume after the `createFields` prop changes still makes no create call and keeps the original job.
  - Backend: two jobs with different languages processed in sequence each keep their own `result.language` through a regenerate.
  - Fix any path where the current choice could leak into an existing job.

**Checkpoint**: all three stories work independently.

---

## Phase 4: Polish & Cross-Cutting Concerns

- [ ] T011 [P] Keep the mobile rules and log hygiene with the picker in `frontend/tests/e2e/` and `backend/tests/integration/test_log_hygiene.py` (FR-013, SC-005)
  - e2e: the YouTube Prep page with the picker has no horizontal scroll at 360 px, and the select is at least 44×44 px on every device profile (desktop-chrome, iphone, pixel, mobile-360).
  - Log hygiene: a job created with a language and a fallback logs neither the uploaded content nor the turns.
- [ ] T012 Update the output-language documentation in `README.md`, `.claude/CLAUDE.md` and `specs/001-videokit-youtube-prep/contracts/http-api.md`
  - README YouTube Prep section: the language choice, that the device remembers it, and the silent-video fallback.
  - CLAUDE.md: the "Output language" rule now reads: the picked language, else the detected one, else the browser's language for silent videos, else English. Add `specs/006-youtube-prep-language/` to the specs list.
  - 001 http-api: a one-line pointer from the YouTube Prep create route to 006's delta.
  - Tests: `backend/tests/unit/test_upgrade_docs.py` (or a new doc test) asserts that the README mentions the language choice and that CLAUDE.md no longer says "no speech → English".

---

## Dependencies & Execution Order

- Phase 1 (US1), then Phase 2 (US2), then Phase 3 (US3), then Phase 4. US2 extends US1's setting
  and request; US3 checks behaviour that US1 builds.
- Within Phase 1: T001, then T002, then T003. T004 [P] and T005 [P] run alongside the backend
  tasks. T006 comes after T004 and T005, and T007 after T005.
- Within Phase 2: T008, then T009. Phase 3: T010. Phase 4: T011 [P] alongside T012.

## Parallel Examples

- Phase 1: T004 (UploadPanel) and T005 (language helpers) alongside T001–T003 (backend).
- Phase 4: T011 (e2e and log hygiene) alongside T012 (docs).

## Implementation Strategy

- **MVP**: Phase 1 alone lets a creator pick and keep an output language.
- **Release**: Phases 1–4 ship together as a minor release (`feat:` commits, no breaking change).
  Older clients and jobs behave exactly as in v0.4.0.
