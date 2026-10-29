# HTTP API Contract: Video Generation (delta)

Base contract: `specs/001-videokit-youtube-prep/contracts/http-api.md` plus the 003 delta. Errors
use the `{"error": {"code", "message"}}` envelope.

## Removed: `POST /api/jobs`

Replaced by each tool's own creation route (research V1). The frontend and backend ship in one
image, so nothing else calls it; the release notes mark it as a breaking API change.

## New: `POST /api/tools/youtube_prep/jobs` and `POST /api/tools/audio_extract/jobs`

Same request and responses as the removed `POST /api/jobs`, minus `tool_id` (the path names the
tool):

- Request: `{"size": 1234567890, "file_name": "trip.mp4"}` (`file_name` optional, same sanitizing).
- `201` → `{"job_id": "<id>", "upload_url": "/api/uploads/<id>"}`
- `413 file_too_large`, `422 invalid_request`.

The tus endpoints (`/api/uploads/{id}`) are unchanged; on the last byte core hands the job to its
tool's `accept` (the playable-video check for both tools), exactly as before.

## New: Video Generation routes (`/api/tools/video_generation`)

Mounted only when at least one video provider is configured (`VIDEO_PROVIDERS` non-empty); otherwise
every path below answers `404` and `GET /api/tools` does not list the tool (clarification
2026-10-29).

### `GET /api/tools/video_generation/options`

What the form offers and what the estimate needs (from the first provider's capabilities and the
price table):

```json
{
  "durations_s": [5, 6, 7, 8, 9, 10],
  "default_duration_s": 8,
  "aspect_ratios": ["9:16", "16:9"],
  "resolutions": ["1080p", "720p"],
  "max_pictures": 7,
  "max_picture_bytes": 10000000,
  "picture_types": ["image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"],
  "max_prompt_chars": 2000,
  "price_per_second_usd": {"720p": 0.101, "1080p": 0.152},
  "price_per_picture_usd": 0.00168
}
```

`price_per_second_usd` / `price_per_picture_usd` are `null` when the model has no known price; the
form then says the cost is unknown (FR-007). Values are derived from the price table and the
provider's tokens-per-second (research V8); `durations_s` narrows if the provider rejects a value
(V9).

### `POST /api/tools/video_generation/jobs`

`multipart/form-data`, at most 7 × 10 MB of pictures plus small fields (< 75 MB, below 100 MB proxy
limits):

| Field | Type | Rule |
|-------|------|------|
| `prompt` | text | required, 1–2,000 characters after trimming |
| `duration_s` | text (int) | one of `options.durations_s` |
| `aspect_ratio` | text | `9:16` \| `16:9` |
| `resolution` | text | `1080p` \| `720p` |
| `pictures` | file, repeated | 0–7, in order (position = order in the form); JPEG/PNG/WebP/HEIC by content, ≤ 10 MB each |

- `201` → `{"job_id": "<id>"}`; the job is already `queued` (no upload stage).
- `422 invalid_request` (missing/invalid field), `422 invalid_picture` (type, decode failure),
  `413 picture_too_large`, `422 too_many_pictures`.
- The request body is streamed with a hard cap; over the cap → `413` before the rest is read.

### `GET /api/jobs/{job_id}` for `tool_id == "video_generation"`

- `state`: `queued → generating → done | failed | cancelled`.
- `providers`: `{"video": "omni:gemini-omni-1.1-flash"}` once a provider accepted the request.
- `usage.video_output_tokens` added (all tools; 0 elsewhere).
- `result` when `done`:

```json
{
  "file": "clip.mp4",
  "thumbnail": "thumbnail.jpg",
  "download_name": "slow zoom into picture 1.mp4",
  "duration_s": 8.0,
  "width": 1080,
  "height": 1920,
  "aspect_ratio": "9:16",
  "resolution": "1080p",
  "size_bytes": 7340032
}
```

- New failure codes: `provider_out_of_credit`, `content_blocked`, `provider_timeout`,
  `invalid_picture`, `provider_unavailable`, `resume_impossible`, `video_failed` (provider returned
  no usable clip). Each maps to its own catalog message (FR-011, FR-026).

### `GET|HEAD /api/tools/video_generation/jobs/{job_id}/clip`

The MP4 as an attachment named `download_name` (RFC 5987 `filename*` for non-ASCII), byte ranges
supported (preview seeking). `404 job_not_found` (unknown, expired, other tool), `409 job_not_done`.

### `GET /api/tools/video_generation/jobs/{job_id}/thumbnail`

`image/jpeg`, `404` / `409` as above.

### `GET /api/tools/video_generation/jobs/{job_id}/inputs`

For Try again and the Recent clips prompt; any state, while not expired:

```json
{
  "prompt": "slow zoom into picture 1, then picture 3 fades in over it",
  "duration_s": 8,
  "aspect_ratio": "9:16",
  "resolution": "1080p",
  "pictures": ["/api/tools/video_generation/jobs/<id>/pictures/1",
               "/api/tools/video_generation/jobs/<id>/pictures/2"]
}
```

### `GET /api/tools/video_generation/jobs/{job_id}/pictures/{n}`

The normalized picture `n` (1-based) as `image/jpeg`. `404` when out of range or expired.

## Changed: `GET /api/tools`

Lists `video_generation` only when the tool is enabled:

```json
{"id": "video_generation", "name": "Video Generation", "description": "Generate a short silent clip from a prompt and pictures."}
```

## Changed: `GET /api/health`

`providers` gains `"video": [{"name": "omni", "ready": true, "detail": "model=gemini-omni-1.1-flash"}]` (empty
list when none configured). `check()` makes no paid call (FR-021).
