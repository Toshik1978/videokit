# HTTP API Contract

Base path `/api`. JSON unless noted. Errors: `{"error": {"code": "<snake_case>", "message": "<user-safe text>"}}`
with an appropriate status; messages never include secrets, transcript text or stack traces
(FR-016). FastAPI also serves its generated OpenAPI at `/api/openapi.json`.

## Platform

### `GET /api/health`
`200` always while the process runs (FR-013).
```json
{
  "status": "ok",
  "providers": {
    "transcription": [{"name": "local", "ready": true, "detail": "device=cuda"},
                      {"name": "openai", "ready": true, "detail": "model=whisper-1"}],
    "llm": [{"name": "anthropic", "ready": true, "detail": "model=claude-opus-5-5"},
            {"name": "openai", "ready": false, "detail": "missing OPENAI_API_KEY"}]
  },
  "queue_length": 1
}
```
(A provider that is listed but not ready prevents startup, FR-012; `ready: false` appears only for
transient conditions, e.g. local model still downloading.)

### `GET /api/config`
```json
{"max_upload_bytes": 10737418240, "upload_chunk_bytes": 50000000, "job_ttl_hours": 24}
```

### `GET /api/tools`
```json
[{"id": "youtube_prep", "name": "YouTube Prep", "description": "Title, description, tags and chapters for a video."}]
```

### `POST /api/tools/{tool_id}/jobs` (youtube_prep, audio_extract)
Creates a job in `uploading` state and its upload resource. Each video tool owns its creation route
(004 research V1; it replaced the platform-wide `POST /api/jobs`), implemented over
`JobAccess.create` by the shared helper `videokit.core.uploads.create_upload_job`.
Request: `{"size": 1234567890, "file_name": "trip.mp4"}`
(YouTube Prep also accepts optional `language` and `fallback_language`; see
`specs/006-youtube-prep-language/contracts/http-api.md`.)
- `file_name` is optional (string, at most 1024 characters; longer → `422 invalid_request`). Core keeps
  only its last path component (split on `/` and `\`), removes control characters, trims
  surrounding whitespace and cuts it to 255 characters; empty → none. It is stored for the tool's
  use, never logged and not echoed back by `GET /api/jobs/{job_id}`.
- `201` → `{"job_id": "<id>", "upload_url": "/api/uploads/<id>"}`
- `413 file_too_large` (size > max, FR-004), `422 invalid_request` (`size` missing, not an integer or
  not positive)

### `GET /api/jobs/{job_id}`
```json
{
  "job_id": "<id>",
  "tool_id": "youtube_prep",
  "state": "uploading|queued|extracting|transcribing|generating|done|failed|cancelled",
  "upload": {"offset": 0, "length": 1234567890},
  "queue_position": 2,
  "failure_reason": null,
  "failure_code": null,
  "expires_at": "2026-10-12T10:00:00Z",
  "duration_s": 1234.5,
  "language": "en",
  "providers": {"transcription": "local", "llm": "anthropic:claude-opus-5-5"},
  "usage": {"input_tokens": 51234, "output_tokens": 5120, "cache_read_tokens": 0,
            "cache_write_tokens": 48000, "audio_minutes": 0, "cost_usd": 0.31},
  "result": null
}
```
`result` is the tool's result object (for YouTube Prep: `YouTubePrepResult` from data-model.md)
when `state == "done"`. `404 job_not_found` for unknown or purged ids (UI shows "expired").
`failure_reason` (short, user-safe English detail) and `failure_code` are set iff
`state == "failed"`; the UI renders the failure from its translation catalog by `failure_code`
(FR-036) and shows `failure_reason` only as detail. Codes: `not_a_video`, `server_restarted`,
`no_audio` (Audio Extract: the video has no audio track), `extraction_failed`,
`transcription_failed`, `generation_failed` (all providers of the stage failed),
`generation_invalid` (output still broke the rules after repair), `internal_error`.
Clients fall back to a generic message for an unknown or `null` code (jobs failed before the code
was added keep `null`).
Polled every 2 s by the UI while not terminal.

### `POST /api/jobs/{job_id}/cancel`
- `202` → job state after the request (`cancelled`, or the running state with cancellation
  pending until the stage boundary, FR-037).
- `409 not_cancellable` when `done|failed|cancelled`.

## Uploads (tus 1.0.0 core)

All responses include `Tus-Resumable: 1.0.0`. Requests must send `Tus-Resumable: 1.0.0`
(`412` otherwise).

| Method | Path | Behavior |
|---|---|---|
| `OPTIONS` | `/api/uploads/{job_id}` | `204`, `Tus-Version: 1.0.0`, `Tus-Max-Size` |
| `HEAD` | `/api/uploads/{job_id}` | `200`, `Upload-Offset`, `Upload-Length`, `Cache-Control: no-store`; `404` if job unknown or not `uploading` |
| `PATCH` | `/api/uploads/{job_id}` | `Content-Type: application/offset+octet-stream`, `Upload-Offset` must equal stored offset (`409` otherwise); body ≤ 50 MB (`413`); appends; `204` with new `Upload-Offset`. When offset reaches length → ffprobe validation → `queued` or `failed` (`not_a_video`) |

## YouTube Prep tool (`/api/tools/youtube_prep`)

### `POST /api/tools/youtube_prep/jobs/{job_id}/regenerate`
Request: `{"section": "cover_titles|title|description|tags|hashtags|chapters", "hint": "optional, ≤ 500 chars"}`
- `200` → full updated `YouTubePrepResult` (only `section` changed) and updated `usage`
  (as in `GET /api/jobs/{id}`: `{"result": {...}, "usage": {...}}`).
- `409 job_not_done`, `409 regeneration_in_progress` (per-job lock), `404 job_not_found`
  (incl. expired, US2-AS4), `422 invalid_section`, `502 generation_failed` (all providers failed or
  output still invalid after repair; previous section kept).
