# VideoKit — guide for Claude

Open-source, self-hosted toolbox of video tools served as one Docker container. Tools:
**YouTube Prep** — upload a video, get cover titles, title/description, tags/hashtags and chapter
timings (`0:00 "Arrival and lobby"`) in the video's spoken language, with per-section regenerate.
**Audio Extract** — upload a video, download or share its audio as a 192 kbps MP3 named after the
video (no AI, no cost).

Specs are the source of truth: `specs/001-videokit-youtube-prep/` (`spec.md`, `plan.md`,
`research.md` R1–R15, `data-model.md`, `contracts/`, `quickstart.md`, `tasks.md`) and
`specs/002-packaging-releases/` (slim image, identity, release flow) and
`specs/003-audio-extract/` (second tool, per-tool queue lanes) under the
constitution `.specify/memory/constitution.md`.

## Non-negotiables

- **Public repo — zero personal or environment data anywhere**: code, tests, fixtures, docs,
  specs, beads (`.beads/`), commit messages. No real hostnames, domains, site names, IPs,
  usernames other than the author's, emails, absolute paths from a developer machine, tunnel/proxy
  names or hardware details. Exceptions: the author's name, Anton Krivenko, MAY appear where
  authorship is stated (LICENSE, package metadata, image labels, documentation bylines), and the
  repository's own public addresses (`github.com/toshik1978/videokit`,
  `ghcr.io/toshik1978/videokit`) and its CI badge gist
  (`gist.githubusercontent.com/toshik1978/3efce57fa029fdddf6aa3c3faefa8a70`) MAY appear in links
  and configuration. Nothing else about the
  author (email, other accounts, hosts, hardware) may appear. Use
  placeholders (`example.com`, `<your-api-key>`, `/data`). Secrets only via env vars; `.env` is
  gitignored, `.env.example` holds placeholders. A gitignored `.denylist` (one string per line)
  is checked by `scripts/check_denylist.py` in pre-commit.
- **Test-first** for every change (failing test → fix → refactor). The default suites run without
  API keys, network access to AI providers or a GPU (fakes + tiny ffmpeg-generated fixtures).
  Real-provider tests live in `backend/tests/live/` behind the `live` marker.
- **Logs and UI never expose** API keys, uploaded content, transcripts, frames or stack traces.
  Log exception *types* only. `backend/tests/integration/test_log_hygiene.py` guards this,
  including real-uvicorn startup/shutdown failures.
- **Conventional Commits** (`feat:`, `fix:`, `build:`, `ci:`, `test:`, `docs:`, `chore:` …),
  enforced by the commit-msg hook and the commit-lint workflow.
- **No authentication by design** — protecting the endpoint is the operator's job (README says so).

## Commands (Taskfile is the single entry point; tools pinned in `.mise.toml`)

```bash
mise install && task setup      # uv sync (backend) + npm install (frontend)
task lint                       # ruff, ruff format --check, mypy strict; vue-tsc, eslint, prettier; hadolint,
                                # actionlint
task test                       # pytest (+coverage, fail_under 80) and vitest (+coverage, lines 80)
task e2e                        # Playwright: desktop-chrome, iphone (WebKit), pixel, mobile-360
task format                     # auto-format both stacks
cd backend && uv run pytest -m live   # opt-in real-provider smoke tests (needs keys / GPU)
gitleaks detect --redact        # full-history secret scan (CI does the same; see .gitleaks.toml)
docker build -t videokit:ci . && scripts/docker_smoke.sh videokit:ci   # incl. codec decode check
task docker:build && task docker:run   # run the image directly with ./.data bind-mounted
task docker:size                # compressed image size vs the limit (scripts/image_size_guard.sh)
task docker:gpu-check           # Whisper on the GPU inside videokit:local (needs a GPU host)
task changelog [TAG=vX.Y.Z]     # prepend the next release section to CHANGELOG.md (cliff)
task release:verify TAG=vX.Y.Z  # version matches newest changelog entry, notes extract
```

Git hooks: `pre-commit install` (one call wires commit-msg, pre-commit, pre-push and the beads
post-checkout/merge/rewrite hooks). Fast hooks (ruff on staged files, gitleaks, denylist) run at
pre-commit only; `task lint` / `task test` / beads setup check run at pre-push. Beads merge driver:
`git config merge.beads.driver "br merge-driver %O %A %B"`.

## Layout

```text
backend/src/videokit/
  main.py                  create_app(settings, providers=None), app_from_env (uvicorn --factory entry)
  core/                    config, errors, db, jobs, registry, api, uploads (tus), worker, purge,
                           media (ffmpeg), pricing, providers, transcribe/{local_whisper,openai,chain},
                           llm/{anthropic,openai,chain}
  tools/youtube_prep/      TOOL, schema, prompt, rules, pipeline, routes (regenerate)
  tools/audio_extract/     TOOL, naming (download name), routes (GET/HEAD .../jobs/{id}/audio)
backend/tests/             unit/, integration/, live/, fakes_transcribe.py, fakes_llm.py,
                           conftest.py (make_video fixture, autouse env isolation), e2e_app.py
frontend/src/              App, router, i18n/{en.json,messages.ts}, api/client.ts, components/,
                           pages/, tools/<kebab-id>/ (auto-discovered via import.meta.glob;
                           youtube-prep, audio-extract)
Dockerfile, compose.yaml, scripts/{docker_smoke.sh,image_size_guard.sh,gpu_check.py,changelog.sh}
.cliff.toml, CHANGELOG.md, CONTRIBUTING.md, docs/RELEASING.md, .github/scripts/*
.github/workflows/{ci,commit-lint,release}.yml
```

## Architecture rules that are easy to break

- **Core never imports tools.** Tools use core only via `Tool`, `CoreContext`, `JobHandle` and
  `JobAccess`/`job_access(request)` (`core/registry.py`, documented in
  `contracts/internal-interfaces.md`). Tools never import vendor SDKs. A new tool is a package
  under `videokit/tools/<id>/` exposing `TOOL` plus `frontend/src/tools/<kebab-id>/index.ts`
  whose `id` equals the backend id — no core changes.
- **Providers are ordered chains with fallback** (`TRANSCRIBE_PROVIDERS`, `LLM_PROVIDERS`), built
  in `core/providers.py`. Every provider's `check()` is cheap: no paid calls, no model loading — it
  runs at startup (startup fails with one `Configuration error:` line per problem) and on every
  `/api/health` request.
- **Prompt-cache safety (SC-004).** The request prefix (system prompt + `[m:ss]` transcript +
  `[frame m:ss]` images) must be byte-identical across generate, repair and regenerate within a
  job. Anything job- or call-specific (duration, detected language, no-speech English rule, repair
  violations, regenerate hint) goes in the **turns**, never the prefix. Regenerate sends the
  **same full `OUTPUT_SCHEMA`** — changing `output_config.format` / `text.format` invalidates the
  provider cache. The Anthropic cache is the 5-minute ephemeral one by decision (research R9).
- **Deterministic rules live in code**, not prompts (`tools/youtube_prep/rules.py`): normalize →
  validate → at most one repair call → readable failure. Chapters: first at 0:00, ≥ 3, gaps ≥ 10 s,
  ascending, last chapter ≥ 10 s before the end, no dashes/emojis/decorative symbols, never damage
  non-Latin scripts; `m:ss` below 1 h, `h:mm:ss` from 1 h. Videos < 30 s get no chapters + notice.
- **Queue lanes**: one lane per tool — `JobStore.next_queued(tool_id)`, queue position counts only
  the tool's own jobs, one worker loop per registered tool — so a slow tool never blocks another.
- **Single-stage tools** go `extracting → done` (no transcribing/generating). Audio Extract encodes
  via `MediaService.extract_mp3` (main audio track: default-flagged, else first; 192 kbps CBR;
  mono stays mono, >2 channels → stereo, >48 kHz → 48 kHz, <32 kHz → 44.1 kHz so 192 kbps holds;
  metadata stripped; `no_audio` failure).
- **`jobs.source_name`**: the original file name from `POST /api/jobs` `file_name`, sanitized,
  exposed to tools as `JobHandle.source_name`; never logged and never returned by the job API.
- **Output language** = the language detected by transcription (`result.language =
  media.language`); no speech → English.
- **Jobs**: SQLite (WAL) + `DATA_DIR/jobs/<id>/`. The state machine in `core/jobs.py` is enforced
  (`uploading → queued` only when offset == length). `failure_reason` and `failure_code` are set
  iff `failed`. `JobStore.is_expired` is the single expiry predicate shared by purge, the job API,
  regenerate and uploads (running jobs never count as expired). Source video is deleted right
  after extraction and on failure/cancel/restart.
- **Uploads**: tus 1.0.0 core in-house (`core/uploads.py`), 50 MB (decimal) chunks so 100 MB
  proxy limits work; streaming writes, per-job serialization (`409 upload_in_progress`),
  completion is idempotent and re-fired at startup.
- **Errors**: the `{"error": {"code", "message"}}` envelope; an outermost pure-ASGI middleware in
  `core/errors.py` turns unhandled errors into a 500 and sanitizes lifespan failures — keep
  `install_error_handlers` as the last middleware registration.
- **Frontend**: every user-facing string comes from `frontend/src/i18n/en.json`; API error and job
  failure codes map to catalog keys via `useMessages` (`errors.generic` fallback) — never render
  server English as primary UI text. Mobile-first: no horizontal scroll at 360 px, touch targets
  ≥ 44×44 px (checked by the e2e suite).
- **Costs**: `core/pricing.py` (+ `PRICING_OVERRIDES`). Job cost is the sum of known parts; local
  transcription is free; unknown model → `cost_usd: null`. Verify prices against Anthropic's
  official pricing page before changing them.

## Gotchas

- `faster-whisper` is incompatible with `av` 19 (it still passes `metadata_errors` to `av.open()`)
  — keep the `av<19` pin (see `pyproject.toml` comment and `tests/unit/test_whisper_audio_compat.py`)
  until faster-whisper supports it; Dependabot ignores `av` majors.
- The Python version lives in four places — `.mise.toml`, `backend/.python-version`,
  `requires-python` and the Dockerfile's `python:X.Y-slim` tag (now `python:3.14-slim`) — and
  Dependabot bumps none of them (it ignores minor/major of the `python` image).
  Before moving to a new minor, check that `ctranslate2`, `onnxruntime` and `av` ship wheels for it.
  The GPU library package must stay `-cu12` (`nvidia-cublas-cu12`) while `ctranslate2` links
  `libcublas.so.12`.
- TypeScript stays on 6.x: vue-tsc and typescript-eslint use the TypeScript programmatic API,
  which 7.0 dropped (7.1 is expected to restore it); Dependabot ignores `typescript` majors.
- Node stays on an LTS line, pinned in `.mise.toml` and the Dockerfile's `node:N-slim` build stage;
  Dependabot ignores `node` image majors, so move both together by hand to the next LTS.
- The image ships only cuBLAS for the GPU (ctranslate2 loads nothing else — research R2), from pip
  and registered via `ldconfig`. Run `task docker:gpu-check` after any `ctranslate2` /
  `faster-whisper` upgrade. The compressed-size limit (1.25 GB) lives in
  `scripts/image_size_guard.sh` (`task docker:size`, also a CI step).
- The Docker image defaults to uid 10001 with a read-only `/app`; Hugging Face/XDG caches point to
  `/data` (`HF_HOME`, `XDG_CACHE_HOME`, set after the build steps). Compose and `task docker:run`
  instead run it as the host uid:gid with `./.data` (gitignored and in `.dockerignore`) bind
  mounted as `/data`, so the image must work under any uid: keep `/app` and the Python env
  world-readable and never write outside `/data`. `UID` is readonly in bash, so Compose
  uses `PUID`/`PGID` from `.env` (default 1000), and `./.data` must be created (`mkdir -p .data`)
  before `docker compose up`, or Docker creates it as root. The smoke test checks both runs.
- Backend tests are isolated from the developer's shell env by an autouse fixture (except `live`
  tests). Don't rely on exported provider keys in default tests.
- gitleaks false positives are handled with narrow, `condition = "AND"` path+exact-regex
  allowlists in `.gitleaks.toml` — never broad path allowlists.
- Dependabot keeps deps current (monthly; actions on moving `@vN` tags). Lockfiles are committed
  (`backend/uv.lock`, `frontend/package-lock.json`); CI installs with `task setup:ci`.

## Workflow

- Architectural work goes through Spec Kit (`/speckit-specify` → clarify → plan → tasks → analyze)
  and is executed by superpowers (`superpowers:using-spec-kit`); `tasks.md` is the contract —
  never hand-edit or reorder its tasks; new work arrives via `/speckit-tasks` or
  `/speckit-converge`.
- Work tracking uses beads (`br`, prefix `vk`); `.beads/issues.jsonl` is committed. Bead text
  follows the same no-personal-data rule (`.beads/config.yaml` sets a neutral actor).
- CI (`.github/workflows/ci.yml`): lint, tests with an 80 % per-stack coverage gate (sticky PR
  comment, gist badges via `GIST_ID`/`GIST_TOKEN`), e2e, hadolint, image build + smoke + size guard,
  gitleaks.
- Releases are tag-driven (`docs/RELEASING.md`): pushing `vX.Y.Z` runs
  `.github/workflows/release.yml` ("Publish release": checks → ci → publish). It reruns all of CI
  through `workflow_call`, so CI jobs must stay callable from a tag push. The tag is the version;
  the changelog section is generated by `task changelog` and committed before tagging.
  The tag-on-main check runs only in the workflow's `checks` job.
- Every change goes through a pull request, with one exception (constitution 1.3.0): the
  release's `chore(release): vX.Y.Z` changelog commit, which the author pushes straight to `main`
  before tagging.
