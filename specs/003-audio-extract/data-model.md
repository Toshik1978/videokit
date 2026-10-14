# Data Model: Audio Extract Tool

Deltas against `specs/001-videokit-youtube-prep/data-model.md`. Decisions: [research.md](research.md).

## Job (core, `jobs` table)

| Field | Change | Notes |
|-------|--------|-------|
| `source_name` | **new**, `TEXT NULL` | Original upload file name: last path component, control characters removed, ≤ 255 characters (A5). Set at creation from `POST /api/jobs` `file_name`; `NULL` when not sent. Never logged, not returned by `GET /api/jobs/{id}`. Deleted with the row. Added in place by `core/db.py`'s `_ADDED_COLUMNS`. |

`Job` (the read-only snapshot) gains `source_name: str | None`.

### State machine

One new edge: `extracting → done` (A2). Full table:

| From | To |
|------|----|
| uploading | queued (only when offset == length), cancelled, failed |
| queued | extracting, cancelled, failed |
| extracting | transcribing, **done**, cancelled, failed |
| transcribing | generating, cancelled, failed |
| generating | done, cancelled, failed |
| done / failed / cancelled | — |

Audio Extract path: `uploading → queued → extracting → done`.

### Queue lanes (A1)

- A lane is the set of `queued` jobs with the same `tool_id`, ordered by `queued_at, rowid`.
- `next_queued(tool_id)` returns the oldest job of that lane; `queue_position(job_id)` = 1 + queued
  jobs of the **same tool** ahead of it; `queue_length()` stays the total over all lanes.
- At most one job per lane is in a running state at a time.

## AudioTrack (core, `MediaService.extract_mp3` return value)

| Field | Type | Notes |
|-------|------|-------|
| `file` | `str` | `"audio.mp3"`, relative to the job directory |
| `duration_s` | `float` | probed from the produced MP3 |
| `size_bytes` | `int` | file size |

Raises `AppError` with `not_a_video` (unplayable source), `no_audio` (no audio stream) or
`extraction_failed` (ffmpeg error). Deletes `source.bin` on success. On failure the worker
deletes it (existing `_fail`).

## AudioExtractResult (tool, `result.json`)

| Field | Type | Notes |
|-------|------|-------|
| `file` | `str` | `"audio.mp3"` |
| `download_name` | `str` | from `naming.download_name(source_name)`, always ends with `.mp3` (A6) |
| `duration_s` | `float` | ≥ 0 |
| `size_bytes` | `int` | > 0 |
| `bitrate_kbps` | `int` | `192` |

## Failure codes

New: `no_audio` ("The video has no audio track."). Reused: `not_a_video`, `extraction_failed`,
`server_restarted`, `internal_error`. Each has a `job.failures.<code>` catalog entry.

## Files in `DATA_DIR/jobs/<id>/` for an Audio Extract job

| File | Lifetime |
|------|----------|
| `source.bin` | upload → deleted right after `audio.mp3` is written, or on failure/cancel/restart |
| `audio.mp3` | until the job expires (purge) or is cancelled |
| `result.json` | same as `audio.mp3` |
