# Data Model: videokit Platform and YouTube Prep Tool

Persistent state = one SQLite table (`jobs`) + one directory per job under `DATA_DIR/jobs/<id>/`.
Tools and providers are runtime registries, not stored.

## Tool (runtime registry)

| Field | Type | Rules |
|---|---|---|
| `id` | str | `^[a-z][a-z0-9_]*$`, unique; e.g. `youtube_prep` |
| `name` | str | display name |
| `description` | str | one sentence |
| `router` | API router | mounted at `/api/tools/<id>` |
| `pipeline` | callable | `run(job, ctx) -> None`, writes tool result |

Discovered automatically from the `videokit.tools` package at startup (FR-002). Frontend tools are
discovered from `frontend/src/tools/*/index.ts`.

## Job (SQLite `jobs`)

| Field | Type | Rules |
|---|---|---|
| `id` | text PK | 128-bit random, URL-safe (unguessable link, FR-007) |
| `tool_id` | text | must exist in registry |
| `state` | text | see state machine |
| `failure_reason` | text? | short, user-safe (FR-016); set iff `failed` |
| `cancel_requested` | bool | set by cancel while running (FR-037) |
| `upload_length` | int | declared size; ≤ `MAX_UPLOAD_BYTES` (FR-004) |
| `upload_offset` | int | bytes received; 0 ≤ offset ≤ length |
| `created_at` | timestamp | |
| `queued_at` | timestamp? | FIFO ordering + queue position (FR-005) |
| `updated_at` | timestamp | |
| `expires_at` | timestamp | `created_at + JOB_TTL_HOURS`; purge after (FR-015) |
| `duration_s` | real? | from ffprobe |
| `language` | text? | detected spoken language (ISO 639-1) |
| `no_speech` | bool | FR-033 |
| `providers` | JSON | `{"transcription": "local", "llm": "anthropic:claude-opus-5-5"}` |
| `usage` | JSON | `{"input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "audio_minutes", "cost_usd": number|null}` accumulated incl. regenerations (FR-032) |

`queue_position` is derived, not stored: 1 + count of `queued` jobs with earlier `queued_at`
(0 when running).

### State machine

```text
uploading ──(offset == length)──► queued ──► extracting ──► transcribing ──► generating ──► done
    │                               │            │               │               │
    └──────── cancel ───────────────┴────────────┴── cancel (at stage boundary) ─┴──► cancelled
                                    any running stage ── error / all providers fail ──► failed
startup: queued | extracting | transcribing | generating ──► failed ("server restarted")
```

- `cancelled`: job directory deleted immediately; row kept until `expires_at` so the link shows
  "cancelled".
- `failed`: source video deleted; row and directory purged at `expires_at`.
- `done`: only state where regenerate is allowed.
- Purge task (every 10 min): delete rows + directories where `expires_at < now`, including stale
  `uploading` jobs (abandoned uploads, FR-015).

## Job directory (`DATA_DIR/jobs/<id>/`)

| File | Lifetime | Content |
|---|---|---|
| `source.bin` | until extraction ends or job fails/cancels (FR-014) | uploaded bytes |
| `audio.wav` / `audio.mp3` | job TTL | 16 kHz mono / 32 kbps mono |
| `frames/<ms>.jpg` | job TTL | ≤ `MAX_FRAMES`, width `FRAME_WIDTH` |
| `media.json` | job TTL | `MediaArtifacts` |
| `result.json` | job TTL | tool result (`YouTubePrepResult`) |

## MediaArtifacts (`media.json`)

```json
{
  "duration_s": 1234.5,
  "has_audio": true,
  "language": "en",
  "language_probability": 0.98,
  "no_speech": false,
  "segments": [{"start": 0.0, "end": 4.2, "text": "Welcome to ..."}],
  "frames": [{"t": 0.0, "file": "frames/0.jpg"}, {"t": 12.48, "file": "frames/12480.jpg"}]
}
```

Rules: segments sorted by `start`, `0 ≤ start < end ≤ duration_s`; frames sorted by `t`, unique,
`len(frames) ≤ MAX_FRAMES`.

## YouTubePrepResult (`result.json`)

```json
{
  "language": "en",
  "notices": ["no_speech" | "too_short_for_chapters"],
  "cover_titles": ["Hidden Gem Resort", "Worth The Price?", "Best Pool Ever"],
  "title": "...",
  "description": "Paragraph one.\n\nParagraph two.",
  "tags": ["hotel review", "..."],
  "hashtags": ["#travel", "#hotel", "#review"],
  "chapters": [{"start_seconds": 0, "title": "Arrival and lobby"}],
  "rendered": {
    "cover_titles": "Hidden Gem Resort\nWorth The Price?\nBest Pool Ever",
    "title": "...",
    "description": "...",
    "tags": "hotel review, ...",
    "hashtags": "#travel #hotel #review",
    "chapters": "0:00 \"Arrival and lobby\"\n1:42 \"Standard room\""
  },
  "regenerations": {"title": 1}
}
```

Validation (code, FR-021–FR-028; normalize first, then validate, one repair call):

| Section | Normalized by code | Violations requiring repair |
|---|---|---|
| `cover_titles` | trim, collapse spaces | count ≠ 3; any > 5 words |
| `title` | trim | > 100 chars |
| `description` | trim, `\n\n` paragraph breaks | < 2 or > 4 paragraphs; > 5000 chars; contains timestamp lines |
| `tags` | trim, dedupe, drop trailing until joined ≤ 500 chars | empty |
| `hashtags` | add `#`, remove spaces, dedupe, keep first 5 | < 3 |
| `chapters` | strip dashes, emojis, decorative symbols, `"`; collapse spaces | first ≠ 0; < 3; gap < 10 s; not ascending; ≥ duration; empty title |

`chapters` is `[]` with notice `too_short_for_chapters` when `duration_s < 30`.

Rendering: time `m:ss` when < 3600 s, else `h:mm:ss`; line `<time> "<title>"`; lines joined by `\n`.

Sections (regenerate unit): `cover_titles`, `title`, `description`, `tags`, `hashtags`, `chapters`.

## Provider (runtime)

| Field | Type | Rules |
|---|---|---|
| `kind` | `transcription` \| `llm` | |
| `name` | str | `local`, `openai` (transcription); `anthropic`, `openai` (llm) |
| `order` | int | position in `TRANSCRIBE_PROVIDERS` / `LLM_PROVIDERS` |
| `ready` | bool | startup validation result (FR-012/FR-013) |
| `detail` | str | e.g. `device=cuda`, `model=claude-opus-5-5`, or why not ready (no secrets) |
