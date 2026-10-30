# Data Model: YouTube Prep Output Language

There is no database or core schema change. Everything lives in YouTube Prep's job folder and
`result.json`.

## Language setting: `DATA_DIR/jobs/<id>/language.json`

Written once, right after the job is created (before the upload starts), and never changed.

| Field | Type | Rule |
|-------|------|------|
| `choice` | string | `"auto"` or a supported ISO 639-1 code (research L2) |
| `fallback` | string | a supported code; `"en"` when the client sent none or an unsupported one |

A missing file (a job from an older release) reads as `{"choice": "auto", "fallback": "en"}`. The
file is purged with the job folder.

## Output language (derived, research L4)

| `choice` | speech detected | output language |
|----------|-----------------|-----------------|
| a code | either | `choice` |
| `auto` | yes | detected language (`media.language`, else `en`), as today |
| `auto` | no | `fallback` |

## `YouTubePrepResult.language` (`result.json`, existing field)

Now the **output** language (it was the detected language, or `en` without speech). Regenerate
reads it as the job's language. The `no_speech` entry in `notices` is unchanged; the UI names
`language` in it.

## Unchanged

- `jobs.language` and `jobs.no_speech` still record what transcription detected (the job API's
  `language` keeps that meaning).
- `media.json` is unchanged, so the cached prefix is too (SC-004).
