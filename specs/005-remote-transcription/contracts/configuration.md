# Configuration Contract: Remote Transcription Server (delta)

Base contract: `specs/001-videokit-youtube-prep/contracts/configuration.md`. Environment variables
only; documented in `.env.example` (placeholders) and the README.

## Added

| Variable | Default | Meaning |
|----------|---------|---------|
| `OPENAI_TRANSCRIBE_URL` | *(empty)* | Base URL of an OpenAI-compatible transcription server, including the API prefix, e.g. `http://whisper.example.com:8000/v1`. Empty → OpenAI's API. Must start with `http://` or `https://`. An address whose host is `api.openai.com` counts as OpenAI's API. |
| `OPENAI_TRANSCRIBE_API_KEY` | — | Optional transcription key. OpenAI's API: used instead of `OPENAI_API_KEY` when set. Any other address: the only key sent (none when unset). Secret; never logged or returned. |

## Changed

| Variable | Before | After |
|----------|--------|-------|
| `TRANSCRIBE_PROVIDERS` | default `local`; allowed `local`, `openai` | no default; allowed `openai` |
| `OPENAI_TRANSCRIBE_MODEL` | `whisper-1`, sent to OpenAI | `whisper-1` by default; sent to whichever address is configured (e.g. `Systran/faster-whisper-large-v3` for speaches) |

## Removed

| Variable | Note |
|----------|------|
| `WHISPER_MODEL` | No longer read; a leftover value is ignored. |
| `WHISPER_DEVICE` | No longer read; a leftover value is ignored. |

The image no longer sets `HF_HOME`, `NVIDIA_VISIBLE_DEVICES` or `NVIDIA_DRIVER_CAPABILITIES`.

## Startup validation

One `Configuration error:` line each; values are never printed:

- `TRANSCRIBE_PROVIDERS must list at least one provider` (unchanged text; now also the fresh-install case)
- `TRANSCRIBE_PROVIDERS lists 'local', which was removed in 0.4.0 (use openai, with OPENAI_TRANSCRIBE_URL for a self-hosted server)`
- `TRANSCRIBE_PROVIDERS contains an unknown provider (allowed: openai)`
- `OPENAI_TRANSCRIBE_URL must start with http:// or https://`
- `OPENAI_API_KEY or OPENAI_TRANSCRIBE_API_KEY is required when TRANSCRIBE_PROVIDERS lists 'openai' without a self-hosted OPENAI_TRANSCRIBE_URL`
- `OPENAI_API_KEY is required when 'openai' is listed in LLM_PROVIDERS` (the LLM rule, now separate from transcription)

## Health

`GET /api/health` → `providers.transcription` entry:
`{"name": "openai", "ready": true, "detail": "model=<model>"}` for OpenAI's API, or
`"detail": "model=<model>, self-hosted"` for any other address. No network call is made; the address
and keys are never included.

## Cost

OpenAI's API: per audio minute by model, as before (`whisper-1` $0.006). Any other address: 0,
whatever the model is called.

## Deployment (Compose / docker run)

- No GPU reservation, `--gpus` flag or NVIDIA Container Toolkit needed.
- `/data` keeps jobs and the SQLite database only. A leftover model cache from earlier releases
  (`<data>/models`, `<data>/huggingface`) is not used and may be deleted by the operator; VideoKit
  does not delete it.
- A server on the Docker host is reached from the container via
  `--add-host host.docker.internal:host-gateway` and `OPENAI_TRANSCRIBE_URL=http://host.docker.internal:<port>/v1`.

## Migration from 0.3.x (release notes)

1. Set `TRANSCRIBE_PROVIDERS=openai`.
2. For a self-hosted server, set `OPENAI_TRANSCRIBE_URL` and `OPENAI_TRANSCRIBE_MODEL` (and
   `OPENAI_TRANSCRIBE_API_KEY` if the server needs one). For OpenAI's API, set `OPENAI_API_KEY`.
3. Remove `WHISPER_MODEL`, `WHISPER_DEVICE`, and the GPU reservation / `--gpus` flag.
4. Optionally delete `<data>/models` and `<data>/huggingface`.
