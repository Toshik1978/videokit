# HTTP API: YouTube Prep Output Language (delta)

Base contracts: `specs/001-videokit-youtube-prep/contracts/http-api.md` and
`specs/004-video-generation/contracts/http-api.md`. Only YouTube Prep routes change; Audio Extract
and core routes do not.

## New: `GET /api/tools/youtube_prep/options`

`200` → `{"languages": ["af", "ar", …, "zh"]}`

- The supported ISO 639-1 codes (research L2), sorted by code. The client names them itself
  (research L3).
- Makes no provider call.

## Changed: `POST /api/tools/youtube_prep/jobs`

Request:

```json
{"size": 1234567890, "file_name": "trip.mp4", "language": "auto", "fallback_language": "pt-BR"}
```

| Field | Required | Rule |
|-------|----------|------|
| `size`, `file_name` | as before | unchanged |
| `language` | no (default `"auto"`) | `"auto"` or a code from `options.languages`; anything else → `422 invalid_language`, and no job is created |
| `fallback_language` | no | any string up to 35 characters (longer → `422 invalid_request`); normalized to its base subtag, lower-cased; an unsupported value becomes `en`. Never refuses otherwise |

- `201` → `{"job_id": "<id>", "upload_url": "/api/uploads/<id>"}`, as before; `language.json` is
  already written.
- Errors: as before (`413 file_too_large`, `422 invalid_request`), plus `422 invalid_language`
  (message: "This language is not supported.").
- An older client that sends neither field gets `auto` with an `en` fallback, which is today's
  behaviour.

## Changed: `YouTubePrepResult.language` (in `GET /api/jobs/{id}` and the regenerate response)

It is the language the output is written in (data-model.md), not necessarily the detected one.

Field note: the job's own top-level `language` in `GET /api/jobs/{id}` stays the language detected by
transcription, so with a picked language it can differ from `result.language` (the output). Clients
should read `result.language` for the output language.

## Unchanged

`POST /api/tools/youtube_prep/jobs/{id}/regenerate` has the same request and responses; it writes
in `result.language`.
