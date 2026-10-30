# Data Model: Video Generation Tool

Base model: `specs/001-videokit-youtube-prep/data-model.md` and the 003 delta. Decisions: [research.md](research.md).

## jobs table (SQLite) — changed

No new columns. Changed rules:

| Field | Change |
|-------|--------|
| `upload_length` | `0` allowed: a job whose tool takes no resumable upload (Video Generation). `0 < length ≤ max` still holds for jobs created with an upload. |
| `state` | Jobs of every tool start in `uploading` ("inputs not complete yet"). A job with `upload_length = 0` completes its inputs as soon as its tool's creation route has written them (V1). |
| `providers` | Video Generation records `{"video": "<provider>:<model>"}`. |
| `usage` | Gains `video_output_tokens` (int, default 0) next to the token and audio fields (V8). Rows created before this feature read it as 0. |

## State machine — changed

```text
uploading ──inputs complete + tool accepts──▶ queued ──▶ <tool stages> ──▶ done
    │                                           │             │
    └── tool rejects (failed) / cancel ─────────┴── cancel ───┴── failed / cancelled
```

- New edge: `QUEUED → GENERATING` (Video Generation has a single stage, `generating`; V5). Every
  existing edge stays.
- Video Generation path: `uploading → queued → generating → done | failed | cancelled`.
- Restart (V3): jobs of tools with `resume_after_restart = True` keep their state; a `queued` job
  waits as before, a `generating` job is re-run by its lane before any queued job. Jobs of the
  other tools are failed with `server_restarted`, unchanged.

## Job directory `DATA_DIR/jobs/<id>/` — Video Generation

| File | Written | Content |
|------|---------|---------|
| `inputs.json` | creation route, atomically, before inputs complete | `{prompt, duration_s, aspect_ratio: "9:16"\|"16:9", resolution: "1080p"\|"720p", pictures: ["pictures/1.jpg", …]}` (order = position) |
| `pictures/<n>.jpg` | creation route | normalized picture `n` (1-based): JPEG, metadata stripped, long side ≤ 2048 px (V7) |
| `submit.json` | `run`, atomically, **before** calling the provider | `{started_at}` — marks "a request may be in flight" |
| `operation.json` | `run`, atomically, right after the provider accepted | `{provider, ref, submitted_at}` — the provider's reference (Omni: the stored interaction id, V2/V3) |
| `raw.mp4` | the provider adapter, as `raw.mp4.part` then renamed | the clip as delivered: inline bytes (720p) or the download of the delivery URI (1080p) (V2) |
| `charged.json` | `run`, atomically, right after the clip's usage was added | `{charged_at}`: the usage is never added twice (V3) |
| `clip.mp4` | `run` | final clip: H.264 MP4, no audio track, `+faststart` (V6) |
| `thumbnail.jpg` | `run` | first-second frame of the clip, ≤ 480 px long side |
| `result.json` | `run`, atomically, last | `{file, thumbnail, download_name, duration_s, width, height, aspect_ratio, resolution, size_bytes}` |

All files are deleted with the job (purge at TTL, cancel). Pictures are kept until then for Try
again (clarification 2026-10-29).

Validation rules (enforced in the creation route; the form mirrors them):

- `prompt`: required, trimmed, 1–2,000 characters.
- `duration_s`: a whole number of seconds from 5 to 10 that the provider supports (research V9).
- `aspect_ratio`: `9:16` (default) or `16:9`; `resolution`: `1080p` (default) or `720p`.
- `pictures`: 0–7 files, each ≤ 10 MB (decimal), detected by content as JPEG, PNG, WebP or
  HEIC/HEIF; anything else → `422 invalid_picture`.

## Resume rules (V3)

| On `run` the job dir has … | Action |
|----------------------------|--------|
| `result.json` naming a clip on disk | finish the job: delete the stored operation (best-effort), never poll or charge again |
| neither `submit.json` nor `operation.json` | write `submit.json`, submit, write `operation.json`, poll |
| `operation.json` | poll that operation (resume); never submit again |
| `submit.json` only | fail `resume_impossible` — a request may have been accepted, so resubmitting could charge twice |

A collected clip adds its usage only while `charged.json` is absent, so a resume that collects it
again does not charge twice. A job still `uploading` with `upload_length = 0` at startup is deleted
with its directory (V1).

## Recent clip entry (device only, `localStorage`)

Key `videokit.video_generation.recent` → JSON array, newest first, ≤ 20 entries:
`{job_id: string, created_at: ISO-8601, prompt: string (first 80 characters)}`.

Pruning on page load: drop entries older than `job_ttl_hours` (from `/api/config`), then drop
each entry whose `GET /api/jobs/{id}` answers `404 job_not_found`. Every read and write is wrapped
so unavailable storage yields an empty list.
