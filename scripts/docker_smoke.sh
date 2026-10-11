#!/usr/bin/env bash
# Container smoke test: start the image with placeholder keys (startup checks key presence only,
# no network), wait for GET /api/health to return 200, then fetch / (the SPA).
# Before that, as the image's user and without any keys, check that the caches the local
# transcription stack writes to (Hugging Face hub/xet, XDG) resolve to a writable place under /data;
# a cache under the read-only home directory makes every local transcription job fail.
# Then repeat the health/SPA checks with the container running as the invoking uid:gid and a
# bind-mounted temp directory as /data (how Compose and `task docker:run` run it), and verify the
# SQLite DB and caches were written there, owned by that uid.
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

name_bind="${name}-bind"
bind_dir="$(mktemp -d)"

cleanup() {
  status=$?
  if [ "$status" -ne 0 ]; then
    docker logs "$name" >&2 || true
    docker logs "$name_bind" >&2 || true
  fi
  docker rm -f "$name" "$name_bind" >/dev/null 2>&1 || true
  rm -rf "$bind_dir"
  exit "$status"
}
trap cleanup EXIT

# wait_and_check <container-name>: wait for /api/health 200, then check that / serves the SPA.
wait_and_check() {
  local cname="$1" port base healthy=false i
  port="$(docker port "$cname" 8000/tcp | head -n1 | sed 's/.*://')"
  base="http://127.0.0.1:${port}"
  for ((i = 0; i < timeout_s; i++)); do
    if [ "$(curl -s -o /dev/null -w '%{http_code}' "$base/api/health" || true)" = "200" ]; then
      healthy=true
      break
    fi
    if [ "$(docker inspect -f '{{.State.Running}}' "$cname")" != "true" ]; then
      echo "container $cname exited before becoming healthy" >&2
      exit 1
    fi
    sleep 1
  done
  "$healthy" || { echo "timed out waiting for /api/health ($cname)" >&2; exit 1; }
  curl -fsS "$base/" | grep -qi '<div id="app"' || { echo "GET / did not return the SPA ($cname)" >&2; exit 1; }
}

env_args=(
  -e "TRANSCRIBE_PROVIDERS=${SMOKE_TRANSCRIBE_PROVIDERS:-openai}"
  -e LLM_PROVIDERS=anthropic
  -e OPENAI_API_KEY=placeholder-openai-key
  -e ANTHROPIC_API_KEY=placeholder-anthropic-key
)

# Run 1: the image's default user and its own /data.
docker run -d --name "$name" -p 127.0.0.1::8000 "${env_args[@]}" "$image" >/dev/null
wait_and_check "$name"

# Run 2: arbitrary (host) uid:gid with a bind-mounted /data, like docker compose / task docker:run.
uid="$(id -u)"
gid="$(id -g)"
docker run -d --name "$name_bind" --user "$uid:$gid" -v "$bind_dir:/data" -p 127.0.0.1::8000 \
  "${env_args[@]}" "$image" >/dev/null
wait_and_check "$name_bind"

# Health already passed, so the app has opened its SQLite DB in /data; caches are written on demand.
docker exec "$name_bind" python -c '
import os, pathlib
for path in (os.environ["HF_HOME"], os.environ["XDG_CACHE_HOME"]):
    pathlib.Path(path).mkdir(parents=True, exist_ok=True)
    (pathlib.Path(path) / ".write-test").write_text("ok")
' || { echo "caches not writable under the bind-mounted /data" >&2; exit 1; }

db_file="$(find "$bind_dir" -maxdepth 2 -name '*.db' -o -maxdepth 2 -name '*.sqlite*' | head -n1)"
[ -n "$db_file" ] || { echo "no SQLite database found in the bind-mounted /data" >&2; exit 1; }
[ -d "$bind_dir/huggingface" ] && [ -d "$bind_dir/cache" ] \
  || { echo "cache directories missing in the bind-mounted /data" >&2; exit 1; }
wrong_owner="$(find "$bind_dir" ! -user "$uid" | head -n1)"
[ -z "$wrong_owner" ] || { echo "files in the bind-mounted /data are not owned by uid $uid" >&2; exit 1; }

echo "smoke test passed"
