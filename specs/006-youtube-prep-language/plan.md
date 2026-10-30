# Implementation Plan: YouTube Prep Output Language

**Branch**: `006-youtube-prep-language` | **Date**: 2026-10-30 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/006-youtube-prep-language/spec.md`

## Summary

YouTube Prep gets a per-job output language. The upload page offers "Same as the speech" plus 61
supported languages, each shown by its own name. The device remembers the last choice. The choice,
plus the browser's language as a fallback for silent videos, goes into YouTube Prep's own job
creation request. The tool stores it in `language.json` in the job folder, and the pipeline
resolves the output language once per run:

- a picked language wins;
- otherwise the detected speech decides;
- otherwise, with no speech, the browser's language is used, else English.

The language goes only into the per-call turns, so prompt caching is untouched. It is stored as
`result.language`, which regeneration reuses and the result page shows. Core, the database and
Audio Extract are unchanged.

**Approach (from brainstorming)**: the user chose **A**, a per-job language input owned by YouTube
Prep. Its trade-off is that the language is fixed at upload, so changing it means uploading again.
Two approaches were rejected:

- **B**, a server-wide `YOUTUBE_PREP_LANGUAGE`: the same language for everyone and no per-video
  override.
- **C**, switching a finished job's language: an extra paid re-run path for a rare case. It could
  be added later on top of A.

Nothing names any particular operator's languages. Details are in research.md L1–L8.

## Technical Context

**Language/Version**: Python 3.14 (backend); TypeScript 6 / Vue 3 (frontend)

**Primary Dependencies**: FastAPI, pydantic (backend); Vue, vue-i18n, tus-js-client (frontend). No
new dependencies. `Intl.DisplayNames` is built into the browser.

**Storage**: `DATA_DIR/jobs/<id>/language.json` (new, tool-owned); `result.json` (`language` changes
meaning); no database change

**Testing**: pytest with fake transcriber and LLM providers; vitest component tests; Playwright e2e
(mobile checks)

**Target Platform**: Linux container, CPU only; modern mobile and desktop browsers

**Project Type**: web service (backend and SPA in one image)

**Performance Goals**: no extra provider calls and no extra cost per job; `/options` is static

**Constraints**:

- Byte-identical request prefix across generate, repair and regenerate (SC-004).
- Core never imports tools, and no core change is needed.
- Every UI string comes from `en.json`.
- No horizontal scroll at 360 px, and touch targets of at least 44×44 px.
- No personal data anywhere.

**Scale/Scope**: about 12 files across both stacks; one new GET route and two optional request
fields

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Open-source hygiene | Pass | A generic 61-language list; examples use neutral codes; nothing names an operator's languages. |
| II. Simplicity | Pass | One small module, one GET route, one optional prop on a shared component; no dependency, setting or migration. |
| III. Multi-tool modularity | Pass | All backend changes are in `tools/youtube_prep/`; the tool uses core only through `uploads.create_upload_job` and `JobAccess`. The frontend shared `UploadPanel` gets a generic `createFields` prop. |
| IV. Provider abstraction | Pass | No provider change; the language is a prompt-turn instruction. |
| V. Test-first | Pass | Each task is test-first; suites stay offline. |
| VI. Privacy & data lifecycle | Pass | `language.json` holds a two-letter choice only and is purged with the job; nothing new is logged. |
| VII. Security boundary | Pass | Input validated (strict `language`, length-capped and normalized `fallback_language`); no auth change. |

Post-design re-check: unchanged.

## Project Structure

### Documentation (this feature)

```text
specs/006-youtube-prep-language/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── http-api.md
│   └── internal-interfaces.md
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
backend/src/videokit/tools/youtube_prep/
  languages.py              new: LANGUAGES, LanguageSetting, normalize_fallback, read/write_setting, instruction
  routes.py                 CreateJobRequest (language, fallback_language), invalid_language, GET /options
  pipeline.py               output_language(setting, media), turn_notes(media, language), build_result, regenerate
  prompt.py                 unchanged unless the language wording moves here
backend/tests/              unit (languages, pipeline resolution, prefix identity), integration (create/options routes,
                            regenerate language, log hygiene unchanged), e2e_app fixture if needed
frontend/src/components/UploadPanel.vue      createFields prop
frontend/src/tools/youtube-prep/language.ts  new: endonym, loadChoice/saveChoice, browserLanguage
frontend/src/tools/youtube-prep/YouTubePrepPage.vue   picker
frontend/src/tools/youtube-prep/JobPage.vue           output-language line, notice parameter
frontend/src/i18n/en.json                    new keys; no_speech takes {language}
frontend/tests/                              unit specs; e2e mobile checks include the picker
README.md, .claude/CLAUDE.md                 output-language rule updated (no longer "no speech → English")
```

**Structure Decision**: existing web-service layout; one new backend module and one new frontend
module, both inside YouTube Prep.

## Complexity Tracking

No violations to justify.
