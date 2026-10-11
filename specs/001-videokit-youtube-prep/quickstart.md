# Quickstart & Validation Guide

Runnable scenarios proving the feature end-to-end. Contracts: [http-api](contracts/http-api.md),
[configuration](contracts/configuration.md); data shapes: [data-model](data-model.md).

## Prerequisites

- Docker (with NVIDIA Container Toolkit for GPU hosts — optional).
- For local development: `uv`, Node.js LTS, `ffmpeg`.
- Provider credentials only for live scenarios (V5–V7).

## Setup

```bash
cp .env.example .env            # fill in <your-anthropic-api-key> etc.; .env is gitignored
docker compose up --build       # GPU block in compose.yaml is optional
open http://localhost:8000      # or any host you expose it on
```

Local dev: `cd backend && uv sync && uv run pytest`; `cd frontend && npm ci && npm test`.

## Automated validation (no keys, no GPU — CI)

| ID | Command | Proves |
|---|---|---|
| V1 | `cd backend && uv run pytest` | chapter rendering/validation (FR-026–FR-029), normalizers, state machine, cancel, restart recovery, TTL purge, provider fallback, tus protocol, pipeline with fakes on generated video |
| V2 | `cd backend && uv run ruff check && uv run mypy` | lint and types |
| V3 | `cd frontend && npm run lint && npm run typecheck && npm test` | components: upload flow, status, editable cards, copy fallback, regenerate |
| V4 | `cd frontend && npm run e2e` | Playwright against the app with fake providers, desktop + iPhone/Pixel emulation: full flow, no horizontal scroll at 360 px, ≤ 9 clicks (FR-038, SC-005, SC-010) |
| V8 | `gitleaks detect` | no secrets in history (SC-008) |

## Manual / live validation

- **V5 – Happy path (US1)**: upload a 10–30 min video with speech. Expect stages to progress and
  six cards. Check chapters: first `0:00`, ≥ 3 lines, each `m:ss "Title"`, no dashes/emojis. Check
  `GET /api/jobs/<id>` shows `usage.cost_usd` < 0.50 with default provider (SC-009) and results
  within 10 min after upload on GPU (SC-001).
- **V6 – Regenerate (US2)**: regenerate `title` with hint "shorter"; only the title changes; response
  < 60 s; `usage` increases by < 20 % of the first run's LLM cost (SC-004).
- **V7 – Fallback (US4)**: set `LLM_PROVIDERS=anthropic,openai` with an invalid Anthropic key that
  passes startup format checks (or block its host); process a video; footer shows `openai`.
- **V9 – Proxy limit (US3)**: run behind a reverse proxy with `client_max_body_size 100m`; upload a
  ≥ 5 GB file; kill network mid-upload, restore; upload resumes (SC-003).
- **V10 – Phone (US6)**: on a real phone (iOS Safari, Android Chrome) pick a gallery video, lock
  the screen during processing, reopen the job link, edit and copy sections; add to home screen.
- **V11 – Cancel (FR-037)**: cancel a queued job and a transcribing job; both show `cancelled`, job
  directories are gone, next job starts.
- **V12 – Startup validation (FR-012)**: `LLM_PROVIDERS=openai` without `OPENAI_API_KEY` → container
  exits with a message naming `OPENAI_API_KEY`.
- **V13 – Cleanup (FR-014/015, SC-007)**: after extraction, `source.bin` is absent; with
  `JOB_TTL_HOURS=1`, the job directory is gone within 1 h after expiry.
- **V14 – Container smoke (CI)**: `scripts/docker_smoke.sh` runs the image with
  `TRANSCRIBE_PROVIDERS=openai`, `LLM_PROVIDERS=anthropic` and placeholder keys (startup checks key
  presence only, no network), waits for `GET /api/health` 200 and fetches `/` (SPA served).
