# Configuration Contract (environment variables)

All configuration is via environment variables (FR-018). `.env.example` mirrors this table with
placeholder values only.

| Variable | Default | Meaning |
|---|---|---|
| `DATA_DIR` | `/data` | jobs, SQLite, model cache |
| `PORT` | `8000` | HTTP port |
| `MAX_UPLOAD_GB` | `10` | max upload size (FR-004) |
| `JOB_TTL_HOURS` | `24` | job retention (FR-015) |
| `MAX_FRAMES` | `80` | frames per video (1–100) |
| `FRAME_WIDTH` | `768` | frame width in px |
| `TRANSCRIBE_PROVIDERS` | `local` | ordered, comma-separated: `local`, `openai` |
| `WHISPER_MODEL` | `large-v3-turbo` | local model name |
| `WHISPER_DEVICE` | `auto` | `auto` \| `cuda` \| `cpu` |
| `OPENAI_TRANSCRIBE_MODEL` | `whisper-1` | must support segment timestamps |
| `LLM_PROVIDERS` | `anthropic` | ordered, comma-separated: `anthropic`, `openai` |
| `ANTHROPIC_API_KEY` | — | required if `anthropic` listed |
| `ANTHROPIC_MODEL` | `claude-opus-5-5` | |
| `ANTHROPIC_EFFORT` | `medium` | `low` \| `medium` \| `high` |
| `OPENAI_API_KEY` | — | required if any `openai` provider listed |
| `OPENAI_MODEL` | — | LLM model id; required if `openai` is in `LLM_PROVIDERS` (no default, to avoid pinning a stale id) |
| `PRICING_OVERRIDES` | — | JSON: `{"<model>": {"input": 4.0, "output": 20.0, "cache_read": 0.2, "cache_write": 5.0}}` $/MTok |
| `LOG_LEVEL` | `INFO` | |

Startup fails (non-zero exit, one clear line per problem) when: a provider name is unknown, a
listed provider lacks its key or required model id, `WHISPER_DEVICE=cuda` without a usable GPU, `MAX_FRAMES` out of
range, or `DATA_DIR` is not writable (FR-012).
