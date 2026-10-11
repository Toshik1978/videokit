---

description: "Task list for videokit platform and YouTube Prep tool"
---

# Tasks: videokit Platform and YouTube Prep Tool

**Input**: Design documents from `specs/001-videokit-youtube-prep/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Every task is implemented test-first (Constitution V): each task writes its own failing
tests in the listed test file(s) before the implementation; configuration-only tasks are verified
by the listed commands. There are no standalone test tasks. Default tests must pass without API
keys, network access to AI providers, or a GPU, and keep line coverage ≥ 80 % per stack.

**Organization**: Tasks are grouped by user story so each story is an independently testable
increment. Each task is one checklist line (what + main files) followed by indented detail
bullets that are part of the task.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: User story the task belongs to (US1–US6 from spec.md)
- Paths: backend `backend/src/videokit/`, backend tests `backend/tests/`, frontend `frontend/src/`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Repository hygiene, toolchain, linters from the first commit, hooks, CI, dependency updates

- [x] T001 Create root hygiene files `LICENSE`, `.gitignore`, `.editorconfig`, `.env.example`
  - `LICENSE`: MIT, copyright "videokit contributors".
  - `.gitignore`: `.env`, `.denylist`, `data/`, `.venv/`, `__pycache__/`, `.ruff_cache/`, `.mypy_cache/`, `.pytest_cache/`, `.coverage`, `coverage/`, `coverage.xml`, `node_modules/`, `frontend/dist/`, `backend/src/videokit/static/`, Playwright output (`test-results/`, `playwright-report/`).
  - `.env.example`: every variable from `contracts/configuration.md` with its default or a placeholder (`<your-anthropic-api-key>`, `<your-openai-api-key>`); no real hosts, names or keys (Constitution I).
  - Verify: `git check-ignore .env .denylist` succeeds.
- [x] T002 [P] Add toolchain pinning and task runner in `.mise.toml` and `Taskfile.yml`
  - `.mise.toml` `[tools]`: `python = "3.13"`, `uv = "latest"`, `node = "24"`, `task`, `gitleaks`, `hadolint`, `pre-commit` (each `"latest"`) so hooks and CI use the same tools (research R15).
  - `Taskfile.yml` (v3) tasks: `setup` (`uv sync` in `backend/`, `npm install` in `frontend/`), `setup:ci` (`uv sync --locked`, `npm ci`), `lint:backend` (`ruff check`, `ruff format --check`, `mypy`), `lint:frontend` (`npm run typecheck`, `npm run lint`, `npm run format:check`), `lint` (both), `test:backend` (`pytest` with coverage), `test:frontend` (`npm test`), `test:backend:ci` / `test:frontend:ci` (coverage reports: Cobertura XML + JSON summary), `test` (both), `format` (`ruff format`, `ruff check --fix`, `npm run format`), `build:frontend` (`npm run build`), `e2e` (`npm run e2e`).
  - Verify: `task --list` shows all tasks.
- [x] T003 [P] Initialize the backend uv project with linters in `backend/pyproject.toml`, `backend/uv.lock` and package skeleton `backend/src/videokit/`
  - Python 3.13; deps: fastapi, uvicorn[standard], pydantic, pydantic-settings, anthropic, openai, faster-whisper; dev group: pytest, pytest-asyncio, pytest-cov, httpx, ruff, mypy.
  - `[tool.ruff]` `line-length = 120`; `[tool.ruff.lint]` `select = ["E", "F", "I", "UP", "B", "ASYNC"]` (research R13).
  - `[tool.mypy]` `strict = true` for package `videokit`.
  - `[tool.pytest.ini_options]` `testpaths = ["tests"]`, `asyncio_mode = "auto"`, marker `live`, `addopts = "-m 'not live' --cov=videokit --cov-report=term-missing"`.
  - `[tool.coverage.report]` `fail_under = 80`; `[tool.coverage.run]` `source = ["videokit"]`, `branch = true`.
  - Packages `videokit/__init__.py` (exports `__version__`), `videokit/core/__init__.py`, `videokit/tools/__init__.py`; commit `uv.lock`.
  - Tests: `backend/tests/unit/test_package.py` (imports, `__version__`). Verify: `task lint:backend` and `task test:backend` pass.
- [x] T004 [P] Initialize the frontend with linters in `frontend/package.json`, `frontend/eslint.config.ts`, `frontend/.prettierrc`, `frontend/tsconfig.json`, `frontend/vite.config.ts`
  - Vite + Vue 3 + TypeScript; deps: vue, vue-router, vue-i18n, tus-js-client; dev: vite, @vitejs/plugin-vue, typescript, vue-tsc, @vue/tsconfig, eslint, @eslint/js, typescript-eslint, eslint-plugin-vue, eslint-plugin-simple-import-sort, eslint-config-prettier, prettier, vitest, @vitest/coverage-v8, @vue/test-utils, happy-dom, @playwright/test, @types/node.
  - Node 24: `.nvmrc` `24`, `"engines": {"node": ">=24 <25"}`, `.npmrc` `engine-strict=true`.
  - Scripts: `dev`, `typecheck` (`vue-tsc --noEmit`), `build` (`npm run typecheck && vite build` → `frontend/dist`), `lint`, `lint:fix`, `format`, `format:check` (Prettier over `src/**/*.{ts,vue,css}` and `tests/**/*.ts`), `test` (`vitest run --coverage`), `test:ci` (coverage + JSON reporter), `e2e` (`playwright test`).
  - `eslint.config.ts` exactly per research R13 (recommended sets, simple-import-sort errors, prettier config, `vue/component-api-style: script-setup`, type-based props/emits, `vue/block-lang` ts, `vue/next-tick-style: promise`, `vue/html-button-has-type`, `vue/multi-word-component-names: off`, `no-undef: off` for ts/vue, unused vars `argsIgnorePattern: ^_`); `vue/no-v-html` stays enabled.
  - `.prettierrc`: `semi: true`, `singleQuote: true`, `trailingComma: "all"`, `printWidth: 100`, `tabWidth: 2`.
  - `tsconfig.json`: `strict`, `noUnusedLocals`, `noUnusedParameters`, `noImplicitReturns`, `noFallthroughCasesInSwitch`, `moduleResolution: bundler`, `paths` `@/*` → `./src/*`.
  - `vite.config.ts`: dev proxy `/api` → `http://localhost:8000`; Vitest `environment: 'happy-dom'`, coverage provider `v8`, reporters `text`, `json-summary`, `cobertura`, include `src/**/*.{ts,vue}`, exclude specs and `src/main.ts`, `thresholds: { lines: 80 }`.
  - Commit `package-lock.json`. Tests: `frontend/tests/unit/smoke.spec.ts`. Verify: `task lint:frontend` and `task test:frontend` pass.
- [x] T005 [P] Add the personal-data guard `scripts/check_denylist.py` with tests in `backend/tests/unit/test_check_denylist.py`
  - Reads optional gitignored `.denylist` (one case-insensitive string per line; blank lines and `#` comments ignored); exits 0 when the file is absent.
  - Scans added lines of `git diff --cached`; on a hit exits 1 naming file and line number but never printing the matched string.
- [x] T006 Add git hooks in `.pre-commit-config.yaml`, `tools/beads/check-setup.sh`, `tools/beads/follow-branch.sh`
  - `default_install_hook_types: [pre-commit, pre-push, commit-msg, post-checkout, post-merge, post-rewrite]` with a comment explaining one `pre-commit install` wires all stages.
  - commit-msg: `compilerla/conventional-pre-commit` (pinned rev), stage `commit-msg`.
  - pre-commit stage (fast only): `ruff check` and `ruff format --check` on staged `\.py$` files (system hooks via `uv run` in `backend/`); `gitleaks protect --staged`; local `check-denylist` → `python scripts/check_denylist.py`, `always_run`, `pass_filenames: false`.
  - pre-push stage: `task lint` and `task test`, `pass_filenames: false`, not type-filtered.
  - pre-push: `beads-setup` → `tools/beads/check-setup.sh`: fails unless `br` ≥ 1.9.0 is installed and `git config --get merge.beads.driver` equals `br merge-driver %O %A %B`, printing the exact fix command.
  - post-checkout/post-merge/post-rewrite: `beads-follow-branch` → `tools/beads/follow-branch.sh`: exit 0 for file checkouts (`PRE_COMMIT_CHECKOUT_TYPE` ≠ 1) or no `.beads/`; warn and skip when `br sync --status` reports dirty issues; else `br sync --import-only --rebuild`; never fails the git command.
  - Verify: `pre-commit run --all-files` passes; a non-conventional message is rejected by `pre-commit run conventional-pre-commit --hook-stage commit-msg --commit-msg-filename <file>`.
- [x] T007 [P] Add CI workflow `.github/workflows/ci.yml` (lint, test with 80 % coverage gate, secret scan)
  - Triggers: push to `main`, pull_request. Actions pinned to moving major tags (`@vN`).
  - Setup via `jdx/mise-action` (tools from `.mise.toml`) + `task setup:ci`; ffmpeg via apt for backend tests.
  - `lint` job: `task lint`.
  - `test` job: `task test:backend:ci` and `task test:frontend:ci` without failing the step immediately (capture exit codes); summarize both Cobertura reports as a sticky PR comment; on `main` update test-count and coverage badges in a gist (secret `GIST_ID`, token `GIST_TOKEN`; color green ≥ 80, yellow ≥ 50, red otherwise); final gate step fails if either suite failed or either stack's line coverage < 80 % (runs after badge publishing).
  - `secrets` job: gitleaks over full history (`fetch-depth: 0`). No other secrets required.
- [x] T008 [P] Add Conventional Commits CI in `.github/workflows/commit-lint.yml` and `.github/scripts/check-commit-messages.sh`
  - Script `check-commit-messages.sh BASE HEAD`: walks `git rev-list --no-merges BASE..HEAD` (tip commit only when BASE is missing or all zeros), runs `pre-commit run conventional-pre-commit --hook-stage commit-msg --commit-msg-filename <tmp>` per commit, prints the short sha of each failure, exits 1 with a hint to run `pre-commit install`.
  - Workflow: push to `main` and pull_request, `fetch-depth: 0`, `permissions: contents: read`, concurrency cancel-in-progress, pipx `pre-commit`, cache `~/.cache/pre-commit` keyed on `.pre-commit-config.yaml`; BASE = PR base sha or `github.event.before`.
  - Tests: `backend/tests/unit/test_check_commit_messages.py` runs the script against a temp git repo with good and bad messages.
- [x] T009 [P] Add Dependabot config `.github/dependabot.yml`
  - All entries `schedule.interval: monthly`, `open-pull-requests-limit: 10`, `commit-message.include: scope`.
  - `uv` at `/backend` and `npm` at `/frontend`: prefix `build`; group `*` for `minor` + `patch` (majors separate PRs).
  - `docker` at `/`: prefix `build`.
  - `github-actions` at `/`: prefix `ci`; ignore `version-update:semver-minor` and `version-update:semver-patch` for `*` (every action is on a moving `@vN` tag) with the explanatory comment from research R15 incl. "replace the wildcard with an explicit list if an action is ever pinned to an exact version"; group `*` for `major`.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Config, persistence, job model, registry, API skeleton, SPA shell

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [x] T010 Implement `Settings` and `validate()` in `backend/src/videokit/core/config.py`
  - All variables and defaults exactly per `contracts/configuration.md`; `MAX_UPLOAD_GB` → `max_upload_bytes`; ordered comma-separated `TRANSCRIBE_PROVIDERS` ∈ {`local`, `openai`} and `LLM_PROVIDERS` ∈ {`anthropic`, `openai`}; `MAX_FRAMES` in 1–100; `PRICING_OVERRIDES` parsed as JSON.
  - `validate(settings) -> list[str]`, one clear line per problem: unknown provider name; listed provider missing its key (`ANTHROPIC_API_KEY` / `OPENAI_API_KEY`) or required model id (`OPENAI_MODEL` when `openai` ∈ `LLM_PROVIDERS`); `WHISPER_DEVICE=cuda` without a usable GPU; `MAX_FRAMES` out of range; `DATA_DIR` not writable. Messages name the variable, never its value.
  - Tests: `backend/tests/unit/test_config.py`.
- [x] T011 [P] Implement user-safe errors and logging in `backend/src/videokit/core/errors.py`
  - `AppError(code, message, status)`; handlers returning `{"error": {"code": "<snake_case>", "message": "<user-safe text>"}}`; catch-all 500 with code `internal_error` and no traceback in the body.
  - Logging at `LOG_LEVEL`; never logs request bodies, API keys or transcript text (FR-016).
  - Tests: `backend/tests/unit/test_errors.py`.
- [x] T012 Implement SQLite persistence and the job model in `backend/src/videokit/core/db.py` and `backend/src/videokit/core/jobs.py`
  - `jobs` table (WAL) with fields per data-model.md: `id` "128-bit random, URL-safe" (`secrets.token_urlsafe(16)`), `tool_id` "must exist in registry", `state`, `failure_reason` "short, user-safe (FR-016); set iff `failed`", `cancel_requested`, `upload_length` "declared size; ≤ `MAX_UPLOAD_BYTES`", `upload_offset` "0 ≤ offset ≤ length", `created_at`, `queued_at`, `updated_at`, `expires_at` "`created_at + JOB_TTL_HOURS`", `duration_s`, `language`, `no_speech`, `providers` JSON, `usage` JSON.
  - States `uploading|queued|extracting|transcribing|generating|done|failed|cancelled`; only transitions in the data-model state machine are allowed, others raise.
  - Derived `queue_position` = "1 + count of `queued` jobs with earlier `queued_at` (0 when running)".
  - `add_usage()` accumulation; per-job directory helper `DATA_DIR/jobs/<id>/`.
  - `recover_after_restart()`: `queued|extracting|transcribing|generating` → `failed` with "Server restarted, please upload again" (FR-019).
  - Tests: `backend/tests/unit/test_jobs.py`.
- [x] T013 [P] Implement the tool registry in `backend/src/videokit/core/registry.py`
  - `Tool`, `CoreContext`, `JobHandle` (`set_state`, `check_cancelled` raising `JobCancelled`) per `contracts/internal-interfaces.md`.
  - Discovery imports every subpackage of `videokit.tools` exposing module-level `TOOL`; id must match `^[a-z][a-z0-9_]*$` and be unique.
  - Tests: `backend/tests/unit/test_registry.py` with a dummy tool package in `backend/tests/dummy_tools/`, proving a new tool needs no core changes (FR-002).
- [x] T014 Implement the app factory and platform API in `backend/src/videokit/main.py` and `backend/src/videokit/core/api.py`
  - `create_app(settings, providers=None)` (providers injectable for tests).
  - Lifespan: `validate()` → exit non-zero printing each problem (FR-012); open DB; `recover_after_restart()`; discover tools and mount each `router` at `/api/tools/{id}`.
  - `GET /api/health`, `GET /api/config`, `GET /api/tools`, `GET /api/jobs/{job_id}` (`404 job_not_found`) with shapes exactly per `contracts/http-api.md`.
  - Serve the built SPA from `backend/src/videokit/static/` with `index.html` fallback for non-`/api` paths.
  - Tests: `backend/tests/integration/test_api_platform.py`.
- [x] T015 [P] Build the SPA shell in `frontend/src/` (`main.ts`, `App.vue`, `router.ts`, `i18n/`, `styles/base.css`, `api/client.ts`, `tools/index.ts`)
  - `i18n/index.ts` + `i18n/en.json`: every user-facing string comes from the catalog (FR-036).
  - `styles/base.css`: mobile-first single column, fluid from 360 px, touch targets ≥ 44×44 px, safe-area insets, light/dark via `prefers-color-scheme` (FR-038); viewport meta in `frontend/index.html`.
  - `api/client.ts`: typed fetch wrapper parsing `{"error": {...}}`.
  - `pages/NotFoundPage.vue`; `tools/index.ts` discovers `./*/index.ts` via `import.meta.glob(..., { eager: true })` exporting `{ id, routes }`.
  - Tests: `frontend/tests/unit/shell.spec.ts` (router, i18n lookup, tool discovery with a test tool).

**Checkpoint**: Foundation ready — app starts, health/config/tools respond, SPA shell renders

---

## Phase 3: User Story 1 - Get YouTube metadata for a video (Priority: P1) 🎯 MVP

**Goal**: Upload a video and get six copyable, editable result sections in the spoken language,
with exact chapter formatting, queueing and cancel.

**Independent Test**: With fake providers, upload a generated sample video through the UI and see
stages progress and six sections; chapters follow `m:ss "Title"` and all YouTube rules; cancel
works for queued and running jobs.

- [x] T016 [US1] Implement job creation and tus core uploads in `backend/src/videokit/core/api.py` and `backend/src/videokit/core/uploads.py`
  - `POST /api/jobs` `{tool_id, size}` → `201 {job_id, upload_url: "/api/uploads/<id>"}`; `404 unknown_tool`; `413 file_too_large` when size > `max_upload_bytes` (FR-004); `422 invalid_request`.
  - tus 1.0.0: `OPTIONS` (`204`, `Tus-Version`, `Tus-Max-Size`); `HEAD` (`Upload-Offset`, `Upload-Length`, `Cache-Control: no-store`; `404` if not `uploading`); `PATCH` (`Content-Type: application/offset+octet-stream`, `Upload-Offset` must equal stored offset else `409`, body > 50 MB → `413`, append to `source.bin`, `204` with new offset).
  - `Tus-Resumable: 1.0.0` on all responses; `412` when missing on requests; completion callback when offset reaches length.
  - Tests: `backend/tests/integration/test_uploads.py`.
- [x] T017 [P] [US1] Implement media extraction in `backend/src/videokit/core/media.py` with the `make_video` fixture in `backend/tests/conftest.py`
  - `probe()` via ffprobe: duration, has_audio; non-video → `AppError("not_a_video")`.
  - `extract()`: `audio.wav` (16 kHz mono) and `audio.mp3` (32 kbps mono) when audio exists; frames: scene candidates from `scale=320:-2,select='gt(scene,0.3)',showinfo` + uniform baseline every `duration / MAX_FRAMES`, drop candidates < 2 s apart, evenly downsample to `MAX_FRAMES`, extract JPEG at `FRAME_WIDTH` to `frames/<ms>.jpg` (research R6).
  - Write `media.json` per data-model: "segments sorted by `start`, `0 ≤ start < end ≤ duration_s`; frames sorted by `t`, unique, `len(frames) ≤ MAX_FRAMES`"; delete `source.bin` (FR-014).
  - `make_video` fixture: tiny ffmpeg-generated videos (color segments for known scene changes, optional sine audio, configurable duration).
  - Tests: `backend/tests/integration/test_media.py`.
- [x] T018 [P] [US1] Implement transcription interfaces and local Whisper in `backend/src/videokit/core/transcribe/` (`base.py`, `chain.py`, `local_whisper.py`)
  - `base.py`: `Transcriber`, `Transcript` (language, language_probability, segments `{start, end, text}`, audio_minutes), `ProviderStatus`, `AllProvidersFailed(stage)` per `contracts/internal-interfaces.md`.
  - `chain.py`: ordered fallback; records the provider used.
  - `local_whisper.py` (research R7): `WHISPER_MODEL`; `WHISPER_DEVICE=auto` → CUDA `float16` when available else CPU `int8`; `download_root=DATA_DIR/models`; VAD on; empty transcript → `no_speech`.
  - `FakeTranscriber` in `backend/tests/fakes_transcribe.py`. Tests: `backend/tests/unit/test_transcribe.py` with `faster_whisper` mocked.
- [x] T019 [P] [US1] Implement LLM interfaces and the Anthropic provider in `backend/src/videokit/core/llm/` (`base.py`, `chain.py`, `anthropic.py`)
  - `base.py`: `LLMProvider`, `GenerationRequest` (system, context_text, frames, schema, turns), `GenerationResponse`, `Usage` incl. cache read/write tokens.
  - `chain.py`: ordered fallback on non-retryable or exhausted-retry errors; raises `AllProvidersFailed("generation")`.
  - `anthropic.py` (research R9; read the claude-api skill's Python docs before writing SDK calls): streaming with the final-message helper, adaptive thinking, `output_config.effort` from `ANTHROPIC_EFFORT`, `output_config.format` JSON schema, server-side refusal fallback enabled, each frame preceded by text `[frame m:ss]`, `cache_control` breakpoint after the frames + transcript prefix, `stop_reason` refusal/max_tokens → provider error.
  - `FakeLLM` (scripted responses incl. invalid ones) in `backend/tests/fakes_llm.py`. Tests: `backend/tests/unit/test_llm.py` asserting request shape with the SDK client mocked.
- [x] T020 [P] [US1] Implement cost estimation in `backend/src/videokit/core/pricing.py`
  - Default $/MTok (input/output/cache_read/cache_write): `claude-opus-5-5` 4/20/0.20/5.00, `claude-sonnet-5-5` 2/10/0.20/2.50, `claude-haiku-5-5` 0.10/0.50/0.01/0.125; `whisper-1` $0.006/min.
  - Merge `PRICING_OVERRIDES`; `estimate(usage, model) -> float | None` (None for unknown models).
  - Tests: `backend/tests/unit/test_pricing.py`.
- [x] T021 [US1] Implement the worker and cancellation in `backend/src/videokit/core/worker.py` and `backend/src/videokit/core/api.py`
  - One asyncio worker started/stopped in the lifespan, processing `queued` jobs by `queued_at` one at a time (FR-005); blocking work via `asyncio.to_thread`.
  - Upload completion → `probe()` → `queued`, or `failed` (`not_a_video`, source deleted).
  - Runs `tool.run(job, ctx)`; `JobCancelled` → `cancelled` + delete job directory; `AllProvidersFailed`/`AppError` → `failed` with a user-safe reason naming the stage; other exceptions → `failed` "Internal error".
  - `POST /api/jobs/{id}/cancel` per contract: `202`; uploading/queued cancelled immediately; running sets `cancel_requested`, honored at the next stage boundary; `409 not_cancellable` for terminal states (FR-037).
  - Tests: `backend/tests/integration/test_worker.py` with a dummy tool.
- [x] T022 [P] [US1] Implement TTL purge in `backend/src/videokit/core/purge.py`
  - Periodic task every 10 min, started in the lifespan: delete rows and directories where `expires_at < now`, including stale `uploading` jobs (FR-015); cancelled rows kept until expiry with directory already removed.
  - Tests: `backend/tests/unit/test_purge.py` with an injectable clock.
- [x] T023 [P] [US1] Implement output rules in `backend/src/videokit/tools/youtube_prep/rules.py`
  - Pure normalize → validate → render functions per data-model "YouTubePrepResult".
  - Normalize: cover titles "trim, collapse spaces"; title "trim"; description "trim, `\n\n` paragraph breaks"; tags "trim, dedupe, drop trailing until joined ≤ 500 chars"; hashtags "add `#`, remove spaces, dedupe, keep first 5"; chapters "strip dashes, emojis, decorative symbols, `\"`; collapse spaces".
  - Violations: cover_titles "count ≠ 3; any > 5 words"; title "> 100 chars"; description "< 2 or > 4 paragraphs; > 5000 chars; contains timestamp lines"; tags "empty"; hashtags "< 3"; chapters "first ≠ 0; < 3; gap < 10 s; not ascending; ≥ duration; empty title".
  - `chapters` is `[]` with notice `too_short_for_chapters` when `duration_s < 30`.
  - Render: time "`m:ss` when < 3600 s, else `h:mm:ss`"; line `<time> "<title>"`; tags joined `, `; hashtags joined by a space; cover titles one per line.
  - Tests: exhaustive `backend/tests/unit/test_youtube_prep_rules.py`.
- [x] T024 [P] [US1] Implement the model contract in `backend/src/videokit/tools/youtube_prep/schema.py` and `backend/src/videokit/tools/youtube_prep/prompt.py`
  - `schema.py`: output JSON schema (`language`, `cover_titles`, `title`, `description`, `tags`, `hashtags`, `chapters[{start_seconds, title}]`); Pydantic result model matching data-model incl. `rendered`, `notices`, `regenerations`.
  - `prompt.py`: system prompt — write everything in the detected spoken language, YouTube best practices, cover titles ≤ 5 words, no chapters in the description, short plain chapter titles; transcript lines `[m:ss] text`; builders for the generate turn, a repair turn listing violations, and a regenerate turn with section, optional hint and current result.
  - Tests: `backend/tests/unit/test_youtube_prep_prompt.py`.
- [x] T025 [US1] Implement the YouTube Prep pipeline in `backend/src/videokit/tools/youtube_prep/pipeline.py` and `backend/src/videokit/tools/youtube_prep/__init__.py`
  - `TOOL` with id `youtube_prep`, name "YouTube Prep".
  - Stages extracting → transcribing (skipped with `no_speech` when no audio) → generating; no speech → output language defaults to English (`en`).
  - Normalize → validate → at most one repair call → still invalid → `failed` with a readable reason (FR-029); render; write `result.json`; record providers, language, duration and accumulated usage/cost; `check_cancelled()` at each stage boundary.
  - Tests: `backend/tests/integration/test_youtube_prep_pipeline.py` with fakes: happy path, repair success, repair failure, no speech notice with language `en`, too short for chapters, cancel mid-pipeline, and repair reusing the identical request prefix (system, transcript, frames) so it can be served from cache (SC-004).
- [x] T026 [US1] Implement the upload UI in `frontend/src/tools/youtube-prep/index.ts`, `frontend/src/tools/youtube-prep/YouTubePrepPage.vue`, `frontend/src/components/UploadPanel.vue`
  - `<input type="file" accept="video/*">`; size check against `GET /api/config` before upload with a message stating the limit.
  - `POST /api/jobs`; tus-js-client with `uploadUrl`, `chunkSize` from config, retry delays; progress percentage; then navigate to `/tools/youtube-prep/jobs/:id`. Strings via i18n.
  - Tests: `frontend/tests/unit/upload.spec.ts` with tus and fetch mocked.
- [x] T027 [US1] Implement the job UI in `frontend/src/tools/youtube-prep/JobPage.vue` and `frontend/src/components/` (`JobStatus.vue`, `ResultCard.vue`, `CopyButton.vue`)
  - Poll `GET /api/jobs/{id}` every 2 s until terminal; show stage, upload %, queue position, failure reason, "expired" on 404; cancel button for non-terminal states.
  - Six result cards (cover titles, title, description, tags, hashtags, chapters) with editable textareas holding rendered text; edits local only (FR-030).
  - Copy uses `navigator.clipboard.writeText`, falling back to selecting the text with a "press copy" hint (FR-040); notices for no speech / too short.
  - Tests: `frontend/tests/unit/job.spec.ts`.

**Checkpoint**: MVP — full flow works end-to-end with fakes and with real providers

---

## Phase 4: User Story 2 - Refine a single section (Priority: P2)

**Goal**: Regenerate one section with an optional hint, cheaply, leaving others unchanged.

**Independent Test**: On a done job, regenerate `title` with a hint; only the title changes, usage
increases, other sections are untouched; expired job returns 404.

- [x] T028 [US2] Implement section regeneration in `backend/src/videokit/tools/youtube_prep/routes.py`
  - `POST /api/tools/youtube_prep/jobs/{job_id}/regenerate` per `contracts/http-api.md`; body `{section ∈ cover_titles|title|description|tags|hashtags|chapters, hint ≤ 500 chars}`.
  - Reuses stored `media.json`/frames and the same stable request prefix plus a regenerate turn; validates and repairs only that section (one repair); returns `{result, usage}`; increments `regenerations[section]`; accumulates usage.
  - Errors: `409 job_not_done`, `409 regeneration_in_progress` (per-job `asyncio.Lock`), `404 job_not_found`, `422 invalid_section`, `502 generation_failed` keeping the previous section.
  - Tests: `backend/tests/integration/test_regenerate.py` incl. the request prefix being byte-identical to the original generation and usage accumulation (SC-004).
- [x] T029 [US2] Add Regenerate to `frontend/src/components/ResultCard.vue` and `frontend/src/tools/youtube-prep/JobPage.vue`
  - Button + optional hint field (≤ 500 chars), loading state; replaces only that card's text, discarding local edits (FR-031); readable errors incl. expired job.
  - Tests: `frontend/tests/unit/regenerate.spec.ts`.

**Checkpoint**: US1 + US2 work independently

---

## Phase 5: User Story 3 - Upload large videos reliably through a proxy (Priority: P2)

**Goal**: Multi-GB uploads succeed behind 100 MB request limits and resume after interruptions.

**Independent Test**: Upload a > 100 MB synthetic file in 50 MB chunks; interrupt mid-PATCH; HEAD
returns the persisted offset and the upload completes from there.

- [x] T030 [US3] Harden uploads in `backend/src/videokit/core/uploads.py`
  - Stream PATCH bodies to disk without buffering whole chunks; enforce the 50 MB cap while streaming; persist `upload_offset` after each write (partial bodies count up to bytes actually written); serialize concurrent PATCHes per job; reject PATCH when the job is not `uploading`.
  - Tests: `backend/tests/integration/test_uploads_resume.py` (interrupted PATCH, offset conflict, > 100 MB total in chunks, concurrent PATCH).
- [x] T031 [US3] Make the client upload resumable in `frontend/src/components/UploadPanel.vue`
  - Store `{fingerprint → job_id, upload_url}` in `localStorage`; re-selecting the same file after reload resumes when `GET /api/jobs/{id}` still says `uploading`, else start fresh; pause on `offline`, resume on `online`; clear the entry on completion or cancel.
  - Tests: `frontend/tests/unit/upload-resume.spec.ts`.

**Checkpoint**: Large and interrupted uploads work

---

## Phase 6: User Story 6 - Use videokit from a phone (Priority: P2)

**Goal**: Full flow on 360 px phones in iOS Safari and Android Chrome; installable to home screen.

**Independent Test**: Playwright mobile emulation runs the full flow with no horizontal scroll and
≥ 44 px touch targets; manifest is valid.

- [x] T032 [P] [US6] Make videokit installable via `frontend/public/manifest.webmanifest`, `frontend/public/icons/` and `frontend/index.html`
  - Manifest: name "videokit", short_name, `display: standalone`, theme/background colors, icons 192 and 512 px generated from a simple original SVG.
  - `index.html`: manifest link, `apple-touch-icon`, `apple-mobile-web-app-capable` meta.
  - Tests: `frontend/tests/unit/manifest.spec.ts` (required fields, icon files exist).
- [x] T033 [US6] Add Playwright e2e in `frontend/playwright.config.ts`, `frontend/tests/e2e/flow.spec.ts`, `backend/tests/e2e_app.py`
  - Projects: Desktop Chrome, iPhone (WebKit), Pixel (Chromium).
  - `backend/tests/e2e_app.py`: `create_app` with `FakeTranscriber`/`FakeLLM` from `backend/tests/fakes_transcribe.py` and `backend/tests/fakes_llm.py`, temp `DATA_DIR`, built SPA; tiny ffmpeg-generated video.
  - Assert: upload → done → edit → copy (fallback over HTTP); no horizontal overflow at 360 px on every page; interactive elements ≥ 44×44 px; home page to all six sections copied in ≤ 9 clicks excluding file picker and waiting (SC-005, SC-010).
  - Add an `e2e` job to `.github/workflows/ci.yml` running `task e2e`.

**Checkpoint**: Mobile flow verified in CI

---

## Phase 7: User Story 4 - Operator deploys and configures providers (Priority: P3)

**Goal**: OpenAI providers, ordered fallback, readiness reporting, startup validation, cost footer.

**Independent Test**: Primary fake fails → fallback completes and is recorded; missing key → app
refuses to start naming the variable; health lists providers; footer shows providers and cost.

- [ ] T034 [P] [US4] Implement `OpenAITranscriber` in `backend/src/videokit/core/transcribe/openai.py`
  - Research R8: `OPENAI_TRANSCRIBE_MODEL` (default `whisper-1`), `response_format=verbose_json` with segment timestamps, uploads `audio.mp3`, splits into time chunks when > 24 MB and restores segment offsets, reports `audio_minutes`; `check()` requires `OPENAI_API_KEY`.
  - Tests: `backend/tests/unit/test_transcribe_openai.py` with the SDK mocked (incl. chunk offset restoration).
- [ ] T035 [P] [US4] Implement `OpenAIProvider` in `backend/src/videokit/core/llm/openai.py`
  - Research R9: Responses API; `input_image` base64 JPEG blocks each preceded by `[frame m:ss]`; `text.format` strict JSON schema; stable prefix first for automatic caching; usage incl. cached input tokens; `check()` requires `OPENAI_API_KEY` and `OPENAI_MODEL`.
  - Tests: `backend/tests/unit/test_llm_openai.py` with the SDK mocked.
- [ ] T036 [US4] Wire providers in `backend/src/videokit/core/providers.py` and `backend/src/videokit/main.py`
  - Build transcription and LLM chains in `TRANSCRIBE_PROVIDERS` / `LLM_PROVIDERS` order; run each provider's `check()` at startup, exiting non-zero naming the problem (FR-012).
  - `/api/health` reports real per-provider `ready`/`detail` (e.g. `device=cuda`, `model=…`, local model downloading) without secrets (FR-013).
  - Tests: `backend/tests/integration/test_providers.py` (failing primary → fallback used and recorded on the job, SC-006; startup failure messages; health shape).
- [ ] T037 [US4] Add the usage footer `frontend/src/components/UsageFooter.vue` to `frontend/src/tools/youtube-prep/JobPage.vue`
  - Providers used, input/output/cache tokens, audio minutes, estimated cost in USD or "n/a" when null; updated after regenerations (FR-032).
  - Tests: `frontend/tests/unit/usage-footer.spec.ts`.

**Checkpoint**: Provider configuration and fallback complete

---

## Phase 8: User Story 5 - Toolbox home page (Priority: P3)

**Goal**: Home page lists all tools as cards that open the tool.

**Independent Test**: Home shows the YouTube Prep card from `GET /api/tools`; a test-only tool also
appears without shared-code changes.

- [ ] T038 [US5] Implement the home page in `frontend/src/pages/HomePage.vue` and `frontend/src/components/ToolCard.vue`
  - Fetch `GET /api/tools`; render a card (name, description) per tool that has a matching frontend tool module, linking to its route; empty and error states; route `/`.
  - Tests: `frontend/tests/unit/home.spec.ts` incl. a test-only tool appearing.

**Checkpoint**: All user stories complete

---

## Phase 9: Polish & Cross-Cutting Concerns

- [ ] T039 Package the app in `Dockerfile`, `.dockerignore`, `compose.yaml`, `scripts/docker_smoke.sh` with hadolint and image CI
  - Research R12: multi-stage — Node 24 stage builds the SPA; runtime stage on an NVIDIA CUDA runtime + cuDNN Ubuntu base with apt `ffmpeg`, `uv sync --locked` backend env, SPA copied to `backend/src/videokit/static/`, non-root user, `DATA_DIR=/data` volume, port 8000, `HEALTHCHECK` on `/api/health`.
  - `compose.yaml`: `env_file: .env`, named data volume, commented optional GPU `deploy.resources.reservations.devices` block.
  - `scripts/docker_smoke.sh` per quickstart V14.
  - CI jobs in `.github/workflows/ci.yml`: `hadolint` on `Dockerfile`; build the image and run the smoke script. Add `task lint:docker` (hadolint) to `Taskfile.yml` and include it in `task lint`.
- [ ] T040 [P] Add real-provider smoke tests in `backend/tests/live/test_live_providers.py`
  - Marked `live`, skipped unless the relevant key/GPU is present: Anthropic generation on a tiny fixture, OpenAI generation, OpenAI transcription of a short speech-free clip (asserts no_speech handling), local Whisper on CUDA.
- [ ] T041 [P] Write `README.md`
  - Purpose and YouTube Prep features; quickstart (`cp .env.example .env`, `docker compose up`); configuration (link `specs/001-videokit-youtube-prep/contracts/configuration.md`); GPU optional; cost per 30-min video; mobile use and add-to-home-screen.
  - Prominent "No authentication — protect it yourself" section with generic reverse-proxy/tunnel/auth-gateway guidance (FR-017); data lifecycle and privacy.
  - Development: `mise install`, `task setup`, `pre-commit install`, `git config merge.beads.driver "br merge-driver %O %A %B"`, `task lint`, `task test`, Conventional Commits; test/coverage badges; MIT. Placeholders only.
- [ ] T042 Run quickstart validation and add log hygiene test in `backend/tests/integration/test_log_hygiene.py`
  - Run V1–V4, V8, V14 from `specs/001-videokit-youtube-prep/quickstart.md` and fix any drift.
  - Log-hygiene test: a full fake run never logs transcript text, frame data or API key values (FR-016).

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)** → **Foundational (Phase 2)** → user story phases → **Polish (Phase 9)**
- **US1 (Phase 3)**: depends on Foundational only — the MVP
- **US2 (Phase 4)**: depends on US1 (needs a done job and result cards)
- **US3 (Phase 5)**: depends on US1 uploads (T016, T026)
- **US6 (Phase 6)**: depends on US1 UI
- **US4 (Phase 7)**: depends on US1 interfaces (T018, T019); independent of US2/US3/US6
- **US5 (Phase 8)**: depends on Foundational (T014, T015) only
- **Polish**: after all desired stories

### Within Each Story

Tests first (failing) → implementation → refactor; backend before the frontend that consumes it.

### Parallel Opportunities

- Phase 1: T002, T003, T004, T005 in parallel after T001; T006 after them; T007, T008, T009 in parallel after T006
- Phase 2: T011, T013, T015 in parallel with the T010 → T012 → T014 chain
- Phase 3: T017, T018, T019, T020 in parallel after T016; T022, T023, T024 in parallel after T021
- Phase 7: T034 and T035 in parallel
- US5 (T038) in parallel with any story after Phase 2

## Parallel Example: User Story 1

```text
After T016:  T017 media.py | T018 transcribe/ | T019 llm/ | T020 pricing.py
After T021:  T022 purge.py | T023 rules.py | T024 schema.py + prompt.py
Then:        T025 pipeline → T026 upload UI → T027 job UI
```

## Implementation Strategy

### MVP First (User Story 1 only)

1. Phase 1 Setup → Phase 2 Foundational
2. Phase 3 (US1) → validate with fakes, then once with real Anthropic + local Whisper
3. Stop and use it: upload a real video, paste results into YouTube

### Incremental Delivery

US1 (MVP) → US2 regenerate → US3 large uploads → US6 mobile → US4 OpenAI + fallback → US5 home →
Polish (Docker, README, live tests). Each phase is one commit and leaves the app working.
