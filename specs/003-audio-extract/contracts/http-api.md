# HTTP API Contract: Audio Extract (delta)

Base contract: `specs/001-videokit-youtube-prep/contracts/http-api.md`. Errors use the
`{"error": {"code", "message"}}` envelope.

## Changed: `POST /api/jobs`

Request: `{"tool_id": "audio_extract", "size": 1234567, "file_name": "trip.mp4"}`

- `file_name` is **optional** (string, at most 1024 characters; longer → `422 invalid_request`).
  It is stored for the tool's use (research A5) and not echoed back. Clients send the picked
  file's name for every tool.
- Responses unchanged.

## Changed: `GET /api/jobs/{job_id}`

- `queue_position` counts only queued jobs of the same tool (research A1).
- For `tool_id == "audio_extract"`, `state` moves `queued → extracting → done`, and `result` is
  `AudioExtractResult` when `done`:

```json
{
  "file": "audio.mp3",
  "download_name": "trip.mp3",
  "duration_s": 754.2,
  "size_bytes": 18101234,
  "bitrate_kbps": 192
}
```

- New failure code `no_audio`. `usage` stays zero; `providers` stays `{}`; `language` stays `null`.

## Changed: `GET /api/tools`

Lists both tools:

```json
[
  {"id": "audio_extract", "name": "Audio Extract", "description": "Get a video's audio as an MP3."},
  {"id": "youtube_prep", "name": "YouTube Prep", "description": "Title, description, tags and chapters for a video."}
]
```

Order follows tool discovery (package name order).

## New: `GET /api/tools/audio_extract/jobs/{job_id}/audio` (also `HEAD`)

- `200` → the MP3 body. Headers: `Content-Type: audio/mpeg`,
  `Content-Disposition: attachment; filename="<ASCII fallback>"; filename*=UTF-8''<percent-encoded download_name>`
  (plain `filename="…"` only when the name is ASCII and has no `%`), `Content-Length`,
  `Accept-Ranges: bytes`. The name is `result.download_name` from the job's `result.json`, so it
  matches what Share uses. Byte-range requests return `206`.
- `404 job_not_found` — unknown id, expired job, or a job of another tool.
- `409 job_not_done` — the job is not `done` (including `failed`/`cancelled`).
- `404 job_not_found` as well when `audio.mp3` is missing (purged between the checks).
