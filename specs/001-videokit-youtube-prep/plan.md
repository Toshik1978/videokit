# Implementation Plan: videokit Platform and YouTube Prep Tool

**Branch**: `001-videokit-youtube-prep` | **Date**: 2026-10-11 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/001-videokit-youtube-prep/spec.md`

## Summary

Build videokit as a single-container web app: a Python/FastAPI backend with a shared `core`
(tus-based resumable uploads, SQLite-backed job queue with one in-process worker, ffmpeg media
extraction, transcription and LLM provider chains with fallback, TTL purge) and self-registering
tools, plus a mobile-first Vue 3 SPA. The first tool, YouTube Prep, extracts a timestamped
transcript and up to 80 scene-aware frames, asks the configured LLM (Claude Opus 5.5 by default,
OpenAI as alternative/fallback) for structured metadata, then normalizes, validates and renders it
deterministically in code — including the exact quoted chapter format — with one repair attempt.
Results are shown as editable cards with Copy and per-section Regenerate (cheap via prompt
caching). See [research.md](research.md) for decisions R1–R14.

## Technical Context

**Language/Version**: Python 3.14 (backend); TypeScript 6 / Vue 3 (frontend)

**Primary Dependencies**: FastAPI, Uvicorn, `anthropic`, `openai`, `faster-whisper`, Pydantic
(settings + schemas); Vue 3, vue-router, vue-i18n, tus-js-client, Vite; ffmpeg/ffprobe (system)

**Storage**: SQLite (job index, WAL) + per-job files under `DATA_DIR/jobs/<id>/`

**Testing**: pytest + pytest-asyncio + pytest-cov (fakes for providers, ffmpeg-generated
fixtures), ruff (+`ASYNC`), mypy strict; Vitest (happy-dom, v8 coverage), ESLint flat config,
Prettier, vue-tsc; Playwright e2e with mobile emulation; hadolint; gitleaks; 80 % coverage floor
per stack (research R13)

**Tooling**: mise (tool versions), Taskfile (single lint/test entry point for hooks and CI),
pre-commit (Conventional Commits, fast staged checks, beads hooks), Dependabot (research R15)

**Target Platform**: Linux container (amd64), NVIDIA GPU optional; clients: current desktop
browsers, iOS Safari, Android Chrome (≥ 360 px wide)

**Project Type**: Web application (backend + SPA in one container)

**Performance Goals**: 30-min video → results ≤ 10 min after upload on GPU or cloud
transcription (SC-001); regenerate ≤ 60 s (SC-004); status polling every 2 s

**Constraints**: request bodies ≤ 50 MB (proxy-friendly); one job at a time; uploads ≤ 10 GB
(configurable); LLM cost < $0.50 per 30-min video with defaults (SC-009); no auth; no personal
data in repo

**Scale/Scope**: single operator, a handful of users, ~tens of videos/day; 1 tool now, more later;
~6 screens/states in the SPA

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Evidence |
|---|---|---|
| I. Open-Source Hygiene | PASS | env-only secrets, `.env.example` placeholders, gitleaks in CI, pre-commit gitleaks + denylist from a gitignored file (R15), MIT |
| II. Simplicity First | PASS | one container, one backend language, SQLite + files, in-process worker, tus core implemented in-house instead of a sidecar (R3/R4/R5) |
| III. Multi-Tool Modularity | PASS | `core` vs `tools/<id>`, auto-discovered registries on both backend and frontend; tools use `CoreContext` only ([internal-interfaces](contracts/internal-interfaces.md)) |
| IV. Provider Abstraction with Fallback | PASS | `Transcriber`/`LLMProvider` + chains; GPU optional via `WHISPER_DEVICE=auto`; startup validation (FR-012) |
| V. Test-First | PASS | fakes + generated fixtures; CI without keys/GPU; `-m live` opt-in; deterministic rules in code; 80 % coverage floor (R10, R13, R14) |
| VI. Privacy & Data Lifecycle | PASS | source deleted after extraction, TTL purge, cancel deletes immediately, user-safe errors |
| VII. Security Boundary | PASS | no auth, README statement, tus chunking, unguessable job ids, no host assumptions |

Post-design re-check (after Phase 1): PASS — no new dependencies or services beyond those listed;
Playwright is a dev-only dependency justified by FR-038/SC-010.

## Project Structure

### Documentation (this feature)

```text
specs/001-videokit-youtube-prep/
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
backend/
├── pyproject.toml                    # uv project, ruff/mypy/pytest config
├── src/videokit/
│   ├── main.py                       # app factory: settings, registry, routers, static SPA
│   ├── core/
│   │   ├── config.py                 # Settings (env), startup validation
│   │   ├── db.py                     # SQLite access for jobs
│   │   ├── jobs.py                   # Job model, state machine, JobHandle, queue position
│   │   ├── worker.py                 # in-process FIFO worker, cancel, restart recovery
│   │   ├── purge.py                  # TTL purge task
│   │   ├── uploads.py                # tus core endpoints
│   │   ├── api.py                    # /api/health, /api/config, /api/tools, /api/jobs*
│   │   ├── media.py                  # ffprobe, audio, scene-aware frames
│   │   ├── registry.py               # tool discovery
│   │   ├── pricing.py                # price table + cost estimation
│   │   ├── errors.py                 # user-safe error types/codes
│   │   ├── transcribe/{base,chain,local_whisper,openai}.py
│   │   └── llm/{base,chain,anthropic,openai}.py
│   └── tools/
│       └── youtube_prep/
│           ├── __init__.py           # TOOL
│           ├── schema.py             # output JSON schema + result model
│           ├── prompt.py             # system prompt, context builders
│           ├── rules.py              # normalize, validate, render (chapters etc.)
│           ├── pipeline.py           # run(): extract → transcribe → generate (+repair)
│           └── routes.py             # regenerate endpoint
└── tests/
    ├── conftest.py                   # tmp DATA_DIR, fakes, ffmpeg fixture generator
    ├── fakes_transcribe.py           # FakeTranscriber
    ├── fakes_llm.py                  # FakeLLM
    ├── unit/                         # rules, pricing, config, state machine, chains
    ├── integration/                  # tus, jobs API, worker, pipeline, purge, regenerate
    └── live/                         # -m live: real providers (skipped by default)

frontend/
├── package.json, vite.config.ts, tsconfig.json, eslint config, playwright.config.ts
├── public/manifest.webmanifest, icons/
├── src/
│   ├── main.ts, App.vue, router.ts
│   ├── i18n/{index.ts,en.json}       # single UI string catalog (FR-036)
│   ├── api/client.ts                 # typed API calls, polling
│   ├── styles/base.css               # mobile-first, light/dark
│   ├── components/{ToolCard,UploadPanel,JobStatus,ResultCard,CopyButton}.vue
│   ├── pages/{HomePage,NotFoundPage}.vue
│   └── tools/
│       ├── index.ts                  # import.meta.glob discovery of tools/*/index.ts
│       └── youtube-prep/{index.ts,YouTubePrepPage.vue,JobPage.vue}
└── tests/{unit,e2e}/

Dockerfile                            # node build → CUDA runtime + ffmpeg + uv env + SPA
compose.yaml                          # example, optional GPU block
.env.example                          # placeholders only
.mise.toml                            # python, uv, node 24, task
Taskfile.yml                          # setup/lint/test/format/build entry points
.pre-commit-config.yaml               # commit-msg, fast staged checks, pre-push gates, beads hooks
scripts/check_denylist.py             # personal-data guard (reads gitignored .denylist)
tools/beads/{check-setup.sh,follow-branch.sh}
.github/
├── dependabot.yml                    # uv, npm, docker, github-actions
├── scripts/check-commit-messages.sh
└── workflows/{ci.yml,commit-lint.yml}  # ci: lint, test+coverage gate, e2e, hadolint, docker, gitleaks
README.md, LICENSE (MIT)
```

**Structure Decision**: Web application layout with `backend/` and `frontend/` at the root and a
single Dockerfile producing one image. Backend uses a `src/` layout; tool code is isolated under
`videokit/tools/<id>` and `frontend/src/tools/<id>` per Constitution III.

## Implementation Notes (ordering hints for /speckit-tasks)

1. Repo scaffolding & hygiene first: license, gitignore, `.env.example`, mise + Taskfile, linters and
   formatters configured from the first commit, pre-commit (Conventional Commits, staged checks,
   beads hooks), CI with coverage gate, commit-lint, Dependabot.
2. Core foundations: config + validation, db, jobs state machine, registry, API skeleton, SPA shell
   with i18n and mobile-first base styles.
3. US1 slice: uploads (tus) → worker → media → transcription chain → LLM chain → YouTube Prep
   rules/pipeline → result UI with copy (fallback) and editing.
4. US3: proxy/large-upload hardening (offset conflicts, resume, size rejection).
5. US2: regenerate (cache-friendly turns, per-job lock).
6. US6: manifest/installability, Playwright mobile e2e.
7. US4: OpenAI providers, fallback, health details, startup failure paths, usage/cost footer;
   US5: home page tool cards.
8. Packaging: Dockerfile, compose, README (no-auth statement, GPU optional), live smoke tests.

## Complexity Tracking

No constitution violations; nothing to justify.
