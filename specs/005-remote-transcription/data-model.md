# Data Model: Remote Transcription Server

No database or job-schema change. Jobs record the transcription provider (`openai`) and model as
before.

## Transcription settings (environment)

| Field | Type | Rule |
|-------|------|------|
| `transcribe_providers` | ordered list of `openai` | at least one; `local` rejected with its own message |
| `openai_transcribe_url` | URL string, optional | `http://` or `https://`; empty or host `api.openai.com` = OpenAI's API |
| `openai_transcribe_model` | string | default `whisper-1` |
| `openai_transcribe_api_key` | secret, optional | OpenAI's API: preferred over `openai_api_key`; other address: the only key sent |
| `openai_api_key` | secret, optional | required for OpenAI's API transcription unless `openai_transcribe_api_key` is set; never sent to another address |

## Transcript

Unchanged fields (`language`, `language_probability`, `segments[start, end, text]`, `audio_minutes`,
`no_speech`, `provider`, `model`) plus `self_hosted: bool` (default `False`) — `True` when produced by
an address other than OpenAI's API; its cost is 0.

## Job directory

`DATA_DIR/jobs/<id>/audio.wav` is no longer written; `audio.mp3` is the only extracted audio.
