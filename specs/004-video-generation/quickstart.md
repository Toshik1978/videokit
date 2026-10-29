# Quickstart: Video Generation

Validation guide for this feature. Contracts: [http-api.md](contracts/http-api.md),
[internal-interfaces.md](contracts/internal-interfaces.md),
[configuration.md](contracts/configuration.md); data: [data-model.md](data-model.md).

## 1. Default suites (no keys, no network)

```bash
task lint && task test && task e2e
```

Expected, with the fake video provider (`backend/tests/fakes_videogen.py`) and ffmpeg-made fixtures:

- **Refactored creation (V1)**: YouTube Prep and Audio Extract create jobs via
  `POST /api/tools/<id>/jobs`; upload, resume, size limit, `not_a_video` and the restart message
  behave as before. `POST /api/jobs` answers `404`/`405`.
- **Video Generation**: a multipart create with 2 pictures returns `201` and a `queued` job; the
  job goes `generating → done`; `clip.mp4` has no audio stream, the requested orientation, and a
  thumbnail; `GET …/clip` downloads with the prompt-based name; `GET …/inputs` returns prompt,
  options and picture URLs in order.
- **Validation**: 8 pictures → `422 too_many_pictures`; an 11 MB picture → `413
  picture_too_large`; a text file → `422 invalid_picture`; empty prompt → `422 invalid_request`.
- **Concurrency**: four jobs → three `generating`, one `queued` with position 1.
- **Restart**: a job with `operation.json` resumes and the fake records exactly one `submit`; a
  job with only `submit.json` fails `resume_impossible`; YouTube Prep jobs still fail
  `server_restarted`.
- **Failures**: fake errors map to `provider_out_of_credit`, `content_blocked`,
  `provider_timeout`, `provider_unavailable`, `video_failed`, each with its own UI message.
- **Hidden tool**: with `VIDEO_PROVIDERS` empty, startup succeeds, `/api/tools` omits the tool,
  `/api/tools/video_generation/options` is `404`, and the frontend route shows not-found.
- **Log hygiene**: no prompt text, picture bytes or provider messages appear in captured logs.
- **e2e (4 projects)**: pick pictures, reorder with the buttons, remove one, generate, see the
  clip in Recent clips after a reload, Try again prefilled; no horizontal scroll at 360 px; targets
  ≥ 44×44 px.

## 2. Image

```bash
docker build -t videokit:ci . && scripts/docker_smoke.sh videokit:ci
task docker:size
```

Expected: the smoke test also decodes a HEIC fixture and a tiled (grid) HEIC fixture through `normalize_picture` (research V7) and
remuxes a generated H.264 + AAC clip to a silent MP4; the compressed image stays under 1.25 GB.

## 3. Live (opt-in, real key, costs ≈ $0.60)

```bash
cd backend && GEMINI_API_KEY=<your-api-key> uv run pytest -m live -k video
```

One 5 s 720p clip from a prompt and one PNG. Confirms the **live-verify** items of research.md:
the `duration` format (V9), usage reported for video (V8), how a safety refusal surfaces (V10,
using a deliberately refused prompt that costs nothing if blocked), and that the interaction can be
deleted afterwards (V6).

## 4. Manual, on a phone

```bash
mkdir -p .data && VIDEO_PROVIDERS=omni GEMINI_API_KEY=<your-api-key> task docker:run
```

Open the app on the phone, generate a vertical 1080p clip from two gallery illustrations, switch
apps for a few minutes, come back: the clip is in Recent clips; Share it to a video editor; Try
again opens the same pictures and prompt.
