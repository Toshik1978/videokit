#!/usr/bin/env bash
# Container smoke test: start the image with placeholder keys (startup checks key presence only,
# no network), wait for GET /api/health to return 200, then fetch / (the SPA).
# Before that, as the image's user and without any keys, check that the caches the local
# transcription stack writes to (Hugging Face hub/xet, XDG) resolve to a writable place under /data;
# a cache under the read-only home directory makes every local transcription job fail.
# Usage: scripts/docker_smoke.sh [image]   (default: videokit:ci)
set -euo pipefail

image="${1:-videokit:ci}"
name="videokit-smoke-$$"
timeout_s="${SMOKE_TIMEOUT:-120}"

docker run --rm --entrypoint python "$image" -c '
import os, pathlib
from huggingface_hub import constants

for path in (constants.HF_HOME, constants.HF_HUB_CACHE, os.environ.get("XDG_CACHE_HOME", "")):
    assert path.startswith("/data/"), f"cache dir {path!r} is outside /data"
    pathlib.Path(path).mkdir(parents=True, exist_ok=True)
    (pathlib.Path(path) / ".write-test").write_text("ok")
print("cache dirs writable")
' || { echo "model cache directories are not writable for the container user" >&2; exit 1; }

cleanup() {
  status=$?
  if [ "$status" -ne 0 ]; then
    docker logs "$name" >&2 || true
  fi
  docker rm -f "$name" >/dev/null 2>&1 || true
  exit "$status"
}
trap cleanup EXIT

docker run -d --name "$name" -p 127.0.0.1::8000 \
  -e TRANSCRIBE_PROVIDERS="${SMOKE_TRANSCRIBE_PROVIDERS:-openai}" \
  -e LLM_PROVIDERS=anthropic \
  -e OPENAI_API_KEY=placeholder-openai-key \
  -e ANTHROPIC_API_KEY=placeholder-anthropic-key \
  "$image" >/dev/null

port="$(docker port "$name" 8000/tcp | head -n1 | sed 's/.*://')"
base="http://127.0.0.1:${port}"

healthy=false
for ((i = 0; i < timeout_s; i++)); do
  if [ "$(curl -s -o /dev/null -w '%{http_code}' "$base/api/health" || true)" = "200" ]; then
    healthy=true
    break
  fi
  if [ "$(docker inspect -f '{{.State.Running}}' "$name")" != "true" ]; then
    echo "container exited before becoming healthy" >&2
    exit 1
  fi
  sleep 1
done
"$healthy" || { echo "timed out waiting for /api/health" >&2; exit 1; }

curl -fsS "$base/" | grep -qi '<div id="app"' || { echo "GET / did not return the SPA" >&2; exit 1; }
echo "smoke test passed"
