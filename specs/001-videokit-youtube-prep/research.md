# Research: videokit Platform and YouTube Prep Tool

All Technical Context items are resolved; no NEEDS CLARIFICATION remains. Each entry records the
decision, why, and what else was considered.

## R1. Backend language and framework

- **Decision**: Python 3.14, FastAPI, Uvicorn, dependencies managed with `uv`.
- **Rationale**: Local GPU transcription (`faster-whisper`) is native to Python; official Anthropic
  and OpenAI SDKs are first-class; one language for the whole backend (Constitution II).
- **Alternatives**: Go (great single binary, but local Whisper needs CGO + CUDA builds or a Python
  sidecar → two languages); Node.js (same Whisper problem).

## R2. Frontend

- **Decision**: Vue 3 + Vite + TypeScript, `vue-router`, `vue-i18n` with a single `en` catalog,
  `tus-js-client` for uploads, hand-written CSS with `prefers-color-scheme` light/dark. Built to
  static files and served by FastAPI.
- **Rationale**: Upload progress, polling, editable cards and per-section regenerate need client
  state; Vue is light and familiar. `vue-i18n` satisfies FR-036 (catalog-only language additions).
- **Mobile (FR-038–FR-040)**: mobile-first responsive CSS (single column, fluid width from 360 px,
  44 px touch targets, `viewport` meta, safe-area insets); `<input type="file" accept="video/*">`
  opens the phone gallery; a web app manifest (name, icons, `display: standalone`) makes it
  installable — no service worker or offline mode (nothing useful works offline). tus-js-client
  stores upload fingerprints in `localStorage`, so re-selecting the same file after a reload
  resumes. Copy uses `navigator.clipboard.writeText` and falls back to selecting the text
  (Clipboard API requires a secure context; plain-HTTP LAN access is common for self-hosting).
  Verified with Playwright mobile emulation (iPhone/Pixel profiles) in CI.
- **Alternatives**: HTMX + server templates (no build step, but resumable uploads and editable
  state need JS anyway); React/Svelte (no advantage here); UI kits (extra weight, Constitution II).

## R3. Resumable uploads behind body-size-limited proxies

- **Decision**: Implement the tus 1.0 **core** protocol (`OPTIONS`, `HEAD`, `PATCH`) in
  `core/uploads` (~150 lines). The upload resource is created server-side together with the job
  (`POST /api/jobs` with the declared size), so the tus *creation* extension is unnecessary; the
  client uses `tus-js-client` with the returned `uploadUrl`. Chunk size 50 MB (FR-003). Termination
  is done through job cancellation.
- **Rationale**: Keeps the server tiny and dependency-free; tus-js-client provides retries, resume
  and progress out of the box; 50 MB chunks pass common 100 MB proxy limits.
- **Alternatives**: `tusd` sidecar (second process/container, Constitution II); third-party Python
  tus server packages (thin maintenance, still need integration with job lifecycle); plain
  multipart POST (fails behind 100 MB limits).

## R4. Job execution model

- **Decision**: One in-process `asyncio` worker consuming a FIFO of job ids; blocking work (ffmpeg,
  Whisper) runs via `asyncio.to_thread`. Cancellation is a flag checked at stage boundaries
  (FR-037). Queue position derives from the ordering of `queued` jobs in SQLite. On startup,
  jobs in `queued`/running stages are marked `failed` ("server restarted") (FR-019). Regeneration
  does not use the queue (LLM-only, short), guarded by a per-job lock.
- **Rationale**: Single job at a time matches GPU and spec (FR-005); zero infrastructure.
- **Alternatives**: Celery/RQ + Redis (infrastructure, Constitution II); process pool (no benefit
  with one worker).

## R5. Storage

- **Decision**: SQLite (stdlib `sqlite3`, WAL mode) for the job index; per-job directory under
  `DATA_DIR/jobs/<job_id>/` for media and JSON artifacts. Job ids are 128-bit random URL-safe
  tokens, so job links are unguessable (FR-007, no auth).
- **Rationale**: Durable enough for restart handling, no server, trivially purged.
- **Alternatives**: JSON files only (racy queue position/status updates); an ORM (unneeded).

## R6. Media extraction

- **Decision**: `ffprobe` validates the file is a playable video and reads duration/audio presence
  (FR-004). Audio: 16 kHz mono WAV for local Whisper; 32 kbps mono MP3 for cloud upload. Frames:
  (1) scene candidates from a downscaled decode pass `scale=320:-2,select='gt(scene,0.3)',showinfo`;
  (2) merge with a uniform baseline of one frame per `duration / MAX_FRAMES`; (3) drop candidates
  closer than 2 s; (4) evenly sample down to `MAX_FRAMES` (default 80); (5) extract chosen
  timestamps with fast seeks at `FRAME_WIDTH` (default 768) as JPEG. Source video is deleted after
  extraction (FR-014).
- **Rationale**: Mirrors the proven approach of scene-aware sampling with a uniform fallback;
  the low-res pass keeps scene detection fast on 90-minute videos.
- **Alternatives**: PySceneDetect (extra dependency, slower); uniform-only sampling (misses short
  scenes, e.g. a quick room tour cut).

## R7. Local transcription

- **Decision**: `faster-whisper` with `large-v3-turbo`; `WHISPER_DEVICE=auto` picks CUDA
  (`float16`) when available, else CPU (`int8`). VAD filter on. Model downloaded on first use into
  `DATA_DIR/models` (persisted volume). Output: segments `{start, end, text}` + detected language
  and probability; empty/near-empty transcript → `no_speech` (FR-033).
- **Rationale**: CTranslate2 is fast and fits in ~6 GB VRAM for turbo; works on CPU (slowly).
- **Alternatives**: WhisperX (alignment/diarization not needed); openai-whisper (slower, PyTorch).

## R8. Cloud transcription

- **Decision**: OpenAI `whisper-1` with `response_format=verbose_json` and segment timestamps.
  Audio is MP3 32 kbps mono (~14 MB/hour); if the file exceeds 24 MB it is split into time chunks
  and segment offsets are restored.
- **Rationale**: Chapters require timestamps; per OpenAI's API reference, `gpt-4o-transcribe` and
  `gpt-4o-mini-transcribe` return only `json`/`text` without segment timing, while `whisper-1`
  supports `verbose_json` with `timestamp_granularities`. Price ≈ $0.006/min.
- **Alternatives**: `gpt-4o-transcribe` (no timestamps); `gpt-4o-transcribe-diarize` (segments
  with times, but diarization is unneeded and the format differs). Model is configurable
  (`OPENAI_TRANSCRIBE_MODEL`) but the adapter requires a timestamped format.

## R9. LLM providers and structured output

- **Decision**: `LLMProvider` interface: `generate(request) -> (json, usage)` where the request
  carries system prompt, transcript text, timestamped frames, a JSON schema and optional follow-up
  messages (repair/regenerate).
  - **Anthropic** (default `claude-opus-5-5`): Messages API via official SDK, streaming with
    `get_final_message()`, adaptive thinking, `output_config.effort` = `medium` (configurable),
    `output_config.format` JSON schema, refusal-fallback (`fallbacks: "default"`) enabled, and a
    `cache_control` breakpoint after the frames+transcript block so regenerate/repair calls read
    the cached prefix. Each frame image is preceded by a text label `[frame mm:ss]`.
  - **OpenAI** (`OPENAI_MODEL` required when listed; no default to avoid a stale id): Responses API via
    official SDK, `input_image` (base64 JPEG) blocks with labels, `text.format` JSON schema
    (strict).
  - Fallback across providers on any non-retryable or exhausted-retry error (FR-010).
- **Rationale**: Neither vendor ingests video directly; frames + transcript is the common
  denominator. Structured output removes JSON parsing failures. Caching makes regenerate cheap
  (SC-004).
- **Cache window**: the breakpoint uses the default ephemeral cache, which lives ~5 minutes and is
  refreshed by each use, so regenerations within that window read the prefix from cache; a later
  regeneration re-pays the full prefix. A 1 h TTL was rejected: its cache writes cost 2× the base
  input price instead of 1.25×, which raises every first run and endangers SC-009 (< US$0.50 per
  30-minute video).
- **Alternatives**: Gemini native video (not in scope of chosen vendors); free-text parsing
  (fragile).

## R10. Deterministic output rules (Constitution III/V)

- **Decision**: The model returns `chapters: [{start_seconds, title}]`; code renders and enforces
  everything deterministic:
  - Normalize: strip dashes/emojis/decorative symbols/double quotes from chapter titles; join tags
    with `, ` and drop trailing tags until ≤ 500 chars; normalize hashtags (`#` prefix, no spaces,
    dedupe, keep first 5).
  - Validate (repairable by the model only): cover options = 3 and ≤ 5 words each; title ≤ 100;
    description 2–4 paragraphs and ≤ 5000; hashtags ≥ 3; chapters first at 0, ≥ 3, gaps ≥ 10 s,
    strictly ascending, < duration, last one ≥ 10 s before the end (YouTube disables chapters otherwise).
  - One repair call with the list of violations; still invalid → job fails with a readable
    reason (FR-029). Videos < 30 s: chapters omitted with a notice.
  - Render time as `m:ss` below 1 h, `h:mm:ss` from 1 h on; line = `<time> "<title>"`.
- **Rationale**: Exact format guarantees (SC-002) cannot rely on a prompt.

## R11. Cost reporting

- **Decision**: A pricing table in code maps known model ids to $/MTok (input, output, cache read,
  cache write) and transcription $/min; usage from each call is accumulated on the job (including
  regenerations). Unknown model → tokens shown, cost "n/a". Overridable via `PRICING_OVERRIDES`
  JSON env var.
- **Rationale**: FR-032 needs an estimate; prices change, so overrides avoid a release.

## R12. Packaging

- **Decision**: Multi-stage Dockerfile: stage 1 `node` builds the SPA; stage 2 an NVIDIA CUDA
  runtime + cuDNN Ubuntu base, apt `ffmpeg`, `uv` installs the locked Python env, copies SPA into
  `static/`. Runs as non-root, `DATA_DIR=/data` volume, port 8000. `compose.yaml` example with an
  optional `deploy.resources.reservations.devices` GPU block (commented for CPU-only hosts).
- **Rationale**: One image works on GPU and CPU hosts (Constitution IV).
- **Alternatives**: separate CPU/GPU images (more CI and docs; revisit if image size is a
  complaint).

## R13. Linters, formatters and quality gates

- **Decision (backend)**: `ruff` (line length 120; rules `E`, `F`, `I`, `UP`, `B` plus `ASYNC`
  because the worker is asyncio and blocking calls in async code are the main bug class) and
  `ruff format`; `mypy --strict` on `videokit`; `pytest` + `pytest-asyncio` (`asyncio_mode = auto`)
  + `pytest-cov`. Real-provider tests behind `-m live`, skipped by default.
- **Decision (frontend)**: ESLint flat config `eslint.config.ts`: `@eslint/js` recommended,
  `typescript-eslint` recommended, `eslint-plugin-vue` `flat/recommended`,
  `eslint-plugin-simple-import-sort` (imports + exports = error), `eslint-config-prettier`; rules:
  `vue/component-api-style: script-setup`, `vue/define-props-declaration: type-based`,
  `vue/define-emits-declaration: type-based`, `vue/block-lang: ts`, `vue/next-tick-style: promise`,
  `vue/html-button-has-type`, `vue/multi-word-component-names: off`, `no-undef: off` for TS/Vue,
  `@typescript-eslint/no-unused-vars` with `argsIgnorePattern: ^_`. `vue/no-v-html` stays **on**
  (model-generated text must never be rendered as HTML). Prettier: `semi`, `singleQuote`,
  `trailingComma: all`, `printWidth: 100`, `tabWidth: 2`, with `format`/`format:check` scripts.
  `tsconfig`: `strict`, `noUnusedLocals`, `noUnusedParameters`, `noImplicitReturns`,
  `noFallthroughCasesInSwitch`, bundler resolution, `@/*` alias. `vue-tsc --noEmit` type-check.
  Vitest with `happy-dom` and v8 coverage (`text`, `json-summary`, `cobertura`). Node 24 pinned by
  `.nvmrc`, `package.json` `engines` (`>=24 <25`) and `.npmrc` `engine-strict=true`.
- **Decision (other)**: `hadolint` for the Dockerfile (CI); `gitleaks` for secrets.
- **Coverage gate**: hard floor **80 %** line coverage per stack (backend `pytest-cov`
  `--cov-fail-under=80`; frontend Vitest `coverage.thresholds` 80). CI publishes a Cobertura summary
  as a sticky PR comment and updates test/coverage badges (stored in a gist; secret `GIST_ID`) on
  `main`; the gate step runs after badge publishing so red badges still publish.
- **Rationale**: same conventions the maintainer uses across projects; strictness from day one is
  cheaper than retrofitting (Constitution V).
- **Alternatives**: flake8/black/isort (ruff covers all); Tailwind/daisyUI (Constitution II).

## R14. Test fixtures

- **Decision**: Generate media in tests with ffmpeg: concatenated solid-color/`testsrc2` segments
  (known scene changes) with a sine tone or silence; tiny (≤ 20 s, low res). Fake transcriber and
  fake LLM return scripted outputs, including invalid ones to exercise repair and failure paths.
- **Rationale**: Deterministic, no binaries committed, no keys or GPU (FR-035).

## R15. Developer workflow tooling

- **Decision**: `.mise.toml` pins `python`, `uv`, `node` (24), `task`, `gitleaks`, `hadolint` and `pre-commit`; `Taskfile.yml` is the
  single entry point (`task setup`, `task lint`, `task test`, `task format`, `task build`, with
  `:backend`/`:frontend` variants and `test:*:ci` coverage variants) used identically by git hooks
  and CI so they cannot drift. Lockfiles `backend/uv.lock` and `frontend/package-lock.json` are
  committed; CI uses `uv sync --locked` and `npm ci`.
- **Decision (pre-commit)**: `default_install_hook_types: [pre-commit, pre-push, commit-msg,
  post-checkout, post-merge, post-rewrite]` so one `pre-commit install` wires everything.
  - commit-msg: `compilerla/conventional-pre-commit` (Conventional Commits).
  - pre-commit (seconds only): `ruff check` + `ruff format --check` on staged Python;
    `gitleaks protect --staged`; the `.denylist` personal-data guard. Secrets and personal strings
    must be stopped before they enter history, so these cannot wait for pre-push.
  - pre-push: `task lint`, `task test` (full gates; keeping them off pre-commit avoids the
    `--no-verify` habit).
  - pre-push: `tools/beads/check-setup.sh` — fails unless `br` (≥ 1.9.0, which ships
    `merge-driver`) is installed and `git config merge.beads.driver` equals
    `br merge-driver %O %A %B`, since git silently falls back to a line merge that corrupts
    `.beads/issues.jsonl`.
  - post-checkout/post-merge/post-rewrite: `tools/beads/follow-branch.sh` — on branch changes runs
    `br sync --import-only --rebuild` so the per-clone DB follows the branch's `issues.jsonl`;
    skips with a warning when the DB has unflushed changes; never fails the git command.
- **Decision (CI)**: `commit-lint.yml` runs `.github/scripts/check-commit-messages.sh BASE HEAD`,
  which replays the repo's own commit-msg hook over each non-merge commit in the PR (or push) range
  — one rule definition, no third-party lint action; falls back to the tip commit when `BASE` is
  all zeros.
- **Decision (Dependabot)**: `.github/dependabot.yml`, monthly, `open-pull-requests-limit: 10`:
  `uv` (`/backend`) and `npm` (`/frontend`) with commit prefix `build` + scope, grouping
  minor+patch into one PR (majors separate); `docker` (`/`) prefix `build`; `github-actions` (`/`)
  prefix `ci`, ignoring minor/patch for all actions because every action is pinned to a moving
  major tag (`@vN`) — Dependabot would otherwise "upgrade" `@v4` to a frozen `@v4.x.y` — grouping
  majors. If an action is ever pinned to an exact version, replace the wildcard with an explicit
  list.
- **Not adopted**: changelog/release automation, per-language guards with no counterpart here.

Sources consulted for R8: OpenAI API reference — audio transcriptions
(https://developers.openai.com/api/reference/resources/audio/index.html) and speech-to-text guide
(https://developers.openai.com/docs/guides/speech-to-text).
