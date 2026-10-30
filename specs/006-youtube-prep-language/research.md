# Research: YouTube Prep Output Language

Decisions for `plan.md`. Approach A (a per-job language input owned by YouTube Prep) was chosen in
brainstorming; B (a server-wide `YOUTUBE_PREP_LANGUAGE`) and C (switching a finished job's language)
were rejected.

## L1. Where the choice enters the job

- **Decision**: YouTube Prep's own `POST /api/tools/youtube_prep/jobs` accepts two optional fields,
  `language` and `fallback_language`. The route validates them, calls the existing core helper
  `uploads.create_upload_job`, then writes `language.json` into the new job's folder
  (`JobAccess.job_dir`). If that write fails, the job is discarded and the request fails.
- **Rationale**: tool-owned job creation (004 research V1) exists for exactly this. The tool's
  request model subclasses core's `CreateUploadRequest`, so core and the database stay untouched
  (Principle III), and Audio Extract is unaffected.
- **Alternatives**: a new `jobs` column (core and migration change for one tool's input); an extra
  tus metadata field (couples the choice to the upload protocol, and resumed uploads would need it
  again); a separate `PUT .../language` call (a second request that can be lost between creation
  and upload).

## L2. Supported languages

- **Decision**: a fixed, tool-owned table in `tools/youtube_prep/languages.py` mapping ISO 639-1 codes
  to English names (the English name goes into the model instruction). It holds 58 widely used
  languages that Whisper transcribes and current LLMs write well: `af ar az be bg bn ca cs cy da de el
  en es et eu fa fi fr gl gu he hi hr hu hy id is it ja ka kk kn ko lt lv mk ml mr ms nl no pa pl pt ro
  ru sk sl sr sv sw ta te th tl tr uk ur vi zh`. `GET /api/tools/youtube_prep/options` returns the
  codes, and the UI renders each by its own name.
- **Rationale**: one source of truth on the server, a list that is generic and not tailored to any
  operator (FR-002), and a stable set that a test can pin.
- **Alternatives**: Whisper's full list of about 99 (includes languages the LLMs write poorly); a
  free-text code (no validation and no endonym list); a frontend-only list (duplicated, and the
  server could not validate it).

## L3. Showing languages by their own names

- **Decision**: the frontend uses `Intl.DisplayNames([code], {type: 'language'}).of(code)` (the
  endonym, for example "Deutsch") and falls back to the code when the browser cannot name it. The
  picker sorts entries by endonym (`localeCompare`), with "Same as the speech" first.
- **Rationale**: every supported browser (Chrome, Safari ≥ 14.1, Firefox) has it, and it avoids
  shipping a 58-entry translation table.
- **Alternatives**: a hand-written endonym table (maintenance, typos); English names (fails FR-001).

## L4. Resolving the output language

- **Decision**: a pure function `output_language(setting, media)`:
  1. A picked code returns that code.
  2. Otherwise, with no speech, it returns `setting.fallback` (already normalized to a supported
     code, else `en`).
  3. Otherwise it returns the detected language (`media.language`, else `en`), unchanged from today.

  The function is called once per run. Its result goes into the per-call turn as
  `Write all output in <English name> (language code xx)` (the code stands in for the name when a
  detected language is outside the table). It is stored as `result.language`. Regeneration takes the
  language from the stored `result.language`, so it never re-resolves.
- **Rationale**: the language only ever appears in turns, so the cached prefix stays byte-identical
  (SC-004). Using the stored result for regenerate makes FR-004 and FR-009 hold even for jobs from
  older releases. `language.json` missing (an older job, or an older client) means auto + `en`,
  which is exactly today's behaviour.
- **Alternatives**: putting the language into the system prompt (breaks the cache); re-reading
  `language.json` during regenerate (a second source that could diverge from the result).

## L5. Browser-language fallback

- **Decision**: the client sends `fallback_language` = the base subtag of `navigator.language`,
  lower-cased (for example `pt-BR` becomes `pt`). The server is lenient: it trims the value, takes
  the part before `-` or `_` and lower-cases it, and stores it if supported, else `en`. It never
  refuses the request over it.
- **Rationale**: an unusual browser setting must not block an upload (spec edge cases). `language`
  is strict (FR-010) because it comes from the server's own list.
- **Alternatives**: the `Accept-Language` header (proxies may rewrite or drop it, and the page
  already knows `navigator.language`).

## L6. Remembering the choice per device

- **Decision**: `localStorage` key `videokit.youtube_prep.language` holds `"auto"` or a code. Every
  read and write is wrapped in try/catch. A missing or unsupported value (checked against the
  options list) means `"auto"`.
- **Rationale**: no accounts exist; this follows the pattern of `video-generation/recentClips.ts`.

## L7. Shared upload component

- **Decision**: `UploadPanel.vue` gains an optional prop `createFields` (a plain JSON object) that is
  merged into its create-job request body. Audio Extract does not pass it. A resumed upload reuses
  the existing job, so the language chosen at creation sticks (spec edge case).
- **Rationale**: the smallest generic hook, with nothing YouTube-Prep-specific in the shared
  component.

## L8. Showing the language in results

- **Decision**: the job page shows `Output language: <endonym>` above the sections, using
  `result.language`. The `no_speech` notice text takes a `{language}` parameter. Both strings come
  from `en.json`.
- **Rationale**: the clarification answer (A), FR-008, FR-013.
