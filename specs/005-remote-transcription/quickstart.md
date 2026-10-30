# Quickstart: Remote Transcription Server

Validation guide for `spec.md`. Configuration details: `contracts/configuration.md`.

## 1. Default suites (no network, no GPU)

```bash
task lint && task test
```

Expected: green; coverage ≥ 80 % per stack. Covers configuration validation (`local`, unknown, empty,
bad address scheme, OpenAI's API without a key, self-hosted address without a key is fine), the
`openai` transcriber against a stub SDK client (base URL, timeout and the right key per address;
`OPENAI_API_KEY` never sent to a self-hosted address; segments, language name and code, chunk
offsets), cost 0 for self-hosted and per-minute for OpenAI, health detail without address or key, and
log hygiene with keys set.

## 2. Image

```bash
docker build -t videokit:ci . && scripts/docker_smoke.sh videokit:ci && task docker:size
docker run --rm --entrypoint python videokit:ci -c "import importlib.util as u; assert not any(u.find_spec(m) for m in ('faster_whisper', 'ctranslate2', 'av'))"
```

Expected: smoke passes without `--gpus`; size guard passes at the lowered limit; the compressed size
is at least 40 % below v0.3.0's (SC-003); no Whisper/GPU modules in the image.

## 3. Against a real OpenAI-compatible server (opt-in)

Start any speaches-compatible server, then:

```bash
cd backend && OPENAI_TRANSCRIBE_URL=http://localhost:8000/v1 \
  OPENAI_TRANSCRIBE_MODEL=Systran/faster-whisper-large-v3 uv run pytest -m live --no-cov -k self_hosted
```

Expected: a short spoken fixture returns segments with ascending times, the right language code, a
positive duration and `self_hosted=True`.

## 4. End to end

Run the image with `TRANSCRIBE_PROVIDERS=openai`, `OPENAI_TRANSCRIBE_URL`, `OPENAI_TRANSCRIBE_MODEL`
and an LLM key; upload a video with speech in YouTube Prep.

Expected: chapters and text in the spoken language; transcription cost 0; `GET /api/health` shows
`model=…, self-hosted` only; VideoKit's process holds no GPU memory (SC-001). Stop the server's model
(idle unload) and run another job: it completes (SC-004).

## 5. Upgrade path

Start the new image with the old `.env` (`TRANSCRIBE_PROVIDERS=local`, `WHISPER_MODEL`, `WHISPER_DEVICE`).

Expected: startup fails with the `'local' … removed in 0.4.0` line only; after following the migration
steps it starts.
