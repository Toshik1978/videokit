#!/usr/bin/env bash
# Container smoke test: start the image with placeholder keys (startup checks key presence only,
# no network), wait for GET /api/health to return 200, then fetch / (the SPA).
# Before that, as the image's user and without any keys, check that the caches the local
# transcription stack writes to (Hugging Face hub/xet, XDG) resolve to a writable place under /data;
# a cache under the read-only home directory makes every local transcription job fail.
# Also generate tiny clips in the codecs users upload (H.264, HEVC, VP9, AV1, MP3 audio) with the
# image's own ffmpeg and run each through the app's media code (probe, audio and frame extraction).
# Also encode a generated tone to MP3 (libmp3lame, 192k) and probe it, as Audio Extract does.
# Then repeat the health/SPA checks with the container running as the invoking uid:gid and a
# bind-mounted temp directory as /data (how Compose and `task docker:run` run it), and verify the
# SQLite DB and caches were written there, owned by that uid.
# Also (Video Generation) decode a HEIC sample generated during the run through MediaService.normalize_picture
# (ffmpeg has no HEIF muxer, so backend/tests/heic_sample.py assembles the sample, mounted read-only) and remux a
# generated H.264 + AAC clip to a silent MP4 with MediaService.finalize_clip (research V6, V7). The HEIC samples
# are a single image and a 2x2 tile grid (how phones store photos): the grid must come out as the whole picture.
# Usage: scripts/docker_smoke.sh [image]   (default: videokit:ci)
set -euo pipefail

image="${1:-videokit:ci}"
tests_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../backend/tests" && pwd)"
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

docker run --rm --entrypoint python "$image" -c '
import shutil, subprocess, sys, tempfile
from pathlib import Path

from videokit.core.config import Settings
from videokit.core.media import SOURCE_NAME, MediaService

encoders = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
av1 = next((e for e in ("libsvtav1", "libaom-av1") if f" {e} " in encoders), "libsvtav1")
clips = {
    "h264-aac.mp4": ["-c:v", "libx264", "-c:a", "aac"],
    "hevc-aac.mp4": ["-c:v", "libx265", "-c:a", "aac"],
    "vp9-opus.webm": ["-c:v", "libvpx-vp9", "-c:a", "libopus"],
    "av1-opus.webm": ["-c:v", av1, "-c:a", "libopus"],
    "h264-mp3.mkv": ["-c:v", "libx264", "-c:a", "libmp3lame"],
}
source = ["-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=15",
          "-f", "lavfi", "-i", "sine=frequency=440:duration=2"]
settings = Settings(_env_file=None)
with tempfile.TemporaryDirectory() as tmp:
    for name, codecs in clips.items():
        job_dir = Path(tmp) / name
        job_dir.mkdir()
        try:
            subprocess.run(["ffmpeg", "-v", "error", "-y", *source, *codecs, "-shortest", str(job_dir / name)],
                           check=True, capture_output=True)
            shutil.move(job_dir / name, job_dir / SOURCE_NAME)
            media = MediaService(settings).extract(job_dir)
            assert media.has_audio and media.frames
            assert (job_dir / "audio.wav").stat().st_size > 0 and (job_dir / "audio.mp3").stat().st_size > 0
        except Exception as exc:
            # The exception type only: its message may carry ffmpeg output or paths.
            print(f"codec check failed: {name} ({type(exc).__name__})", file=sys.stderr)
            sys.exit(1)
print("codecs ok")
' || { echo "the image cannot decode every common video codec" >&2; exit 1; }

# Video Generation: normalize a HEIC picture to JPEG and finalize an H.264 + AAC clip to a silent MP4.
docker run --rm -v "$tests_dir:/tests:ro" --entrypoint python "$image" -c '
import subprocess, sys, tempfile
from pathlib import Path

sys.path.insert(0, "/tests")
from fakes_videogen import write_clip
from heic_sample import write_heic

from videokit.core.config import Settings
from videokit.core.media import MediaService

media = MediaService(Settings(_env_file=None))


def streams(path, entries="codec_type,codec_name"):
    cmd = ["ffprobe", "-v", "error", "-show_entries", f"stream={entries}", "-of", "csv=p=0", str(path)]
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout.split()


with tempfile.TemporaryDirectory() as tmp:
    job_dir = Path(tmp)
    for name, size, grid in (("heic", (64, 48), (1, 1)), ("heic-grid", (128, 96), (2, 2))):
        try:
            write_heic(job_dir / f"{name}.heic", *size, grid=grid)
            media.normalize_picture(job_dir / f"{name}.heic", job_dir / f"{name}.jpg")
            assert streams(job_dir / f"{name}.jpg") == ["mjpeg,video"]
            assert streams(job_dir / f"{name}.jpg", "width,height") == [f"{size[0]},{size[1]}"]
        except Exception as exc:
            # The exception type only: its message may carry ffmpeg output or paths.
            print(f"picture check failed: {name} ({type(exc).__name__})", file=sys.stderr)
            sys.exit(1)
    try:
        write_clip(job_dir / "raw.mp4")
        clip = media.finalize_clip(job_dir / "raw.mp4", job_dir)
        assert streams(job_dir / clip.file) == ["h264,video"]
        assert clip.thumbnail and (job_dir / clip.thumbnail).stat().st_size > 0 and not (job_dir / "raw.mp4").exists()
    except Exception as exc:
        print(f"clip check failed: h264-aac ({type(exc).__name__})", file=sys.stderr)
        sys.exit(1)
print("pictures and clips ok")
' || { echo "the image cannot normalize a HEIC picture or finalize a clip" >&2; exit 1; }

# Audio Extract encodes MP3 with libmp3lame at 192k: encode a generated tone and probe the result.
docker run --rm --entrypoint sh "$image" -c '
set -e
dir="$(mktemp -d)"
ffmpeg -v error -y -f lavfi -i sine=frequency=440:duration=2 -c:a libmp3lame -b:a 192k "$dir/tone.mp3"
codec="$(ffprobe -v error -select_streams a:0 -show_entries stream=codec_name -of csv=p=0 "$dir/tone.mp3")"
[ "$codec" = "mp3" ]
' || { echo "the image cannot encode MP3 with libmp3lame at 192k" >&2; exit 1; }

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
