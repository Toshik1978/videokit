# VideoKit

[![Backend tests](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/toshik1978/3efce57fa029fdddf6aa3c3faefa8a70/raw/backend-tests.json)](.github/workflows/ci.yml)
[![Backend coverage](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/toshik1978/3efce57fa029fdddf6aa3c3faefa8a70/raw/backend-coverage.json)](.github/workflows/ci.yml)
[![Frontend tests](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/toshik1978/3efce57fa029fdddf6aa3c3faefa8a70/raw/frontend-tests.json)](.github/workflows/ci.yml)
[![Frontend coverage](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/toshik1978/3efce57fa029fdddf6aa3c3faefa8a70/raw/frontend-coverage.json)](.github/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

VideoKit is an open-source, self-hosted web app that hosts small video tools. You run it as a
single Docker container on your own machine. It has three tools:
**YouTube Prep**, **Audio Extract** and **Video Generation** (the last one only when you configure a
video provider).

## About this project

VideoKit is a large experiment in a fully AI-built application, taken from an idea to running
software by combining [Spec Kit](https://github.com/github/spec-kit) and
[Superpowers](https://github.com/obra/superpowers). The code, the tests and this documentation were
all written by AI agents working under that combined workflow. The result is shared as is.

No issues, no pull requests and no support: the repository is provided as is (see
[`CONTRIBUTING.md`](CONTRIBUTING.md)).

## YouTube Prep

Upload a finished video and get ready-to-paste YouTube metadata, written in the language spoken in
the video:

- **Cover titles**: 3 options, at most 5 words each.
- **Title**: at most 100 characters.
- **Description**: 2 to 4 short paragraphs, at most 5000 characters, without chapters embedded.
- **Tags**: one comma-separated list, at most 500 characters in total.
- **Hashtags**: 3 to 5, each starting with `#`.
- **Chapters**: one per line as `<time> "<title>"`, starting at `0:00`, with at least 3 chapters
  at least 10 seconds apart, for example:

  ```text
  0:00 "Arrival and lobby"
  ```

Other features:

- Resumable, chunked uploads (50 MB per request), so large videos survive flaky connections and
  request-size limits in front of the app.
- Every section is editable in the browser and has a Copy button. Edits are not saved on the
  server.
- Per-section Regenerate with an optional hint. It reuses the already extracted transcript and
  frames, so it is quick and cheap.
- Progress per stage (uploading, queued, extracting, transcribing, generating), a queue (one job at
  a time per tool), cancel, and a shareable job link that works while the job is retained.
- Pluggable transcription and text-generation providers, tried in the order you configure, with
  automatic fallback. The result footer shows the providers used, token usage and estimated cost.

## Audio Extract

Upload a video and get its audio as an MP3, with no AI and no cost:

- **Format**: MP3 at 192 kbps constant bitrate, from the video's main audio track (the one flagged
  as default, else the first). Mono stays mono, more than two channels become stereo, sample rates
  above 48 kHz become 48 kHz and rates below 32 kHz become 44.1 kHz (so the file keeps 192 kbps).
  Metadata is stripped.
- **Download**: the file is named after the video (`trip.mp4` becomes `trip.mp3`). The job page
  shows the duration and size.
- **Share**: on phones and other browsers that can share files, a Share button hands the MP3 to
  other apps.
- A video without an audio track fails with a clear message.
- Same resumable upload, retention (`JOB_TTL_HOURS`), cancel and job link as YouTube Prep. Each
  tool has its own queue, so an extraction never waits behind a YouTube Prep job.

## Video Generation

Generate one short, silent clip from a prompt and up to seven pictures, to cut into your own edit
(for example a Short). The tool only appears when you configure a video provider (see
[Configuration](#configuration)); with `VIDEO_PROVIDERS` empty there is no card, and the tool's
link and endpoints answer "not found".

- **Inputs**: a prompt of up to 2,000 characters in any language, and 0 to 7 pictures (JPEG, PNG,
  WebP or HEIC, at most 10 MB each; phone HEIC photos work). Pictures keep the order you set, and
  the prompt can refer to them as "picture 1", "picture 2" and so on.
- **Options**: duration of 5 to 10 seconds (8 by default), vertical 9:16 (default) or horizontal
  16:9, and 1080p (default) or 720p. The form shows the estimated cost before you submit.
- **Result**: an MP4 without an audio track (any audio the provider returns is removed), with a
  preview, its actual duration, size, orientation and cost, and Download (named after the start of
  the prompt) and Share buttons.
- **Try again** opens the form with the same pictures, prompt and options; each attempt is a new,
  separately charged job.
- **Recent clips** lists the clips started on this device (up to 20, newest first, kept only in the
  browser), so you can come back to a clip after leaving the page. There is no server-side job
  list.
- **Cancel**: a generating job can be cancelled, but the provider may still charge for that
  attempt.
- **Queue**: up to 3 clips generate at once; the rest wait in the tool's own line, so other tools
  are never blocked.
- **Restarts**: queued and generating jobs survive a server restart. A running job continues to
  wait for the provider's result and is never sent twice, so a restart does not cost a second
  charge. If the server stops in the instant between sending the request and recording the
  provider's reference, the job fails with a clear message instead of being resubmitted.
- Failures have their own messages: provider account out of credit, content refused by the
  provider's safety filter, timeout (after 20 minutes of waiting), invalid pictures and provider
  unavailable.

**Breaking API change:** `POST /api/jobs` no longer exists. Every upload-based tool now creates its
jobs through its own route, `POST /api/tools/<id>/jobs` (for example
`POST /api/tools/youtube_prep/jobs`), with the same request and response minus `tool_id`; the
resumable upload endpoints are unchanged. The bundled web app already uses the new routes, so only
scripts that call the API directly need updating.

## No authentication: protect it yourself

**VideoKit has no login, no users and no access control.** Anyone who can reach the port can upload
videos, spend your provider credits and open any job link they know. Securing access is your
responsibility as the operator. This matters most for Video Generation, which spends money on every
clip and has no spending limit of its own: protecting the endpoint (for example with a reverse
proxy that authenticates users) is the operator's job.

- Do not expose the port directly to the internet. Bind it to a trusted network, or put it behind
  something that authenticates users.
- A reverse proxy, a tunnel service or an authentication gateway (an identity-aware proxy, basic
  auth, SSO in front of the app, a VPN) all work. Use whichever you already run.
- Serve it over HTTPS if it is reachable beyond localhost. Browsers only allow clipboard access and
  home-screen install from a secure origin. On plain HTTP, Copy falls back to selecting the text.
- Uploads are sent in chunks of at most 50 MB, so a request-body limit of 64 to 100 MB or more on
  your proxy is enough. For example, nginx needs `client_max_body_size 100m;`. Make sure proxy
  timeouts are long enough for one chunk on a slow connection.
- Job ids are long random tokens, so links cannot be guessed, but anyone who has a link can see
  that job. Treat links as private.

## Quickstart

You need Docker with the Compose plugin.

```bash
cp .env.example .env     # then edit .env and fill in your API key placeholders
mkdir -p .data           # create it yourself, or Docker creates it owned by root
docker compose up -d    # pulls ghcr.io/toshik1978/videokit:latest and starts it
```

Then open <http://localhost:8000>. The service listens on port 8000 and keeps its data (jobs,
SQLite index) in the gitignored `./.data` directory next to `compose.yaml`, bind
mounted as `/data`. The container runs as your user (`PUID`/`PGID` in `.env`, default 1000;
set them to the output of `id -u` and `id -g` if yours differ) so it can write there. `./.data`
must exist before the first start, hence the `mkdir`. To back up or wipe the
data, copy or delete `./.data`.

To build the image yourself instead of pulling it, see [Development](#development).

Transcription has no default provider: `.env.example` sets `TRANSCRIBE_PROVIDERS=openai`, which needs
either `OPENAI_API_KEY` or a self-hosted server (see [Transcription](#transcription)). Text
generation defaults to Anthropic, so also set `ANTHROPIC_API_KEY` in `.env`. The app refuses to start, with a message naming the problem, if a
listed provider lacks its key or model id. `GET /api/health` reports which configured providers
are ready.

## Configuration

Everything is configured with environment variables. `.env.example` has placeholder values; the full
reference is in
[`specs/001-videokit-youtube-prep/contracts/configuration.md`](specs/001-videokit-youtube-prep/contracts/configuration.md)
and, for the video variables,
[`specs/004-video-generation/contracts/configuration.md`](specs/004-video-generation/contracts/configuration.md).
The most important variables:

| Variable | Default | Meaning |
| --- | --- | --- |
| `TRANSCRIBE_PROVIDERS` | none | Required. Ordered, comma-separated; the only provider is `openai` (OpenAI's API or a self-hosted OpenAI-compatible server). |
| `OPENAI_TRANSCRIBE_URL` | empty | Base URL of a self-hosted transcription server, with its API prefix (for example `http://host.docker.internal:8000/v1`). Empty means OpenAI's API. Must start with `http://` or `https://`. |
| `OPENAI_TRANSCRIBE_MODEL` | `whisper-1` | Model id sent to whichever address is configured. |
| `OPENAI_TRANSCRIBE_API_KEY` | none | Optional transcription key. For OpenAI's API it is used instead of `OPENAI_API_KEY`; for a self-hosted server it is the only key sent (none when unset). |
| `LLM_PROVIDERS` | `anthropic` | Ordered, comma-separated: `anthropic`, `openai`. The next one is tried if one fails. |
| `ANTHROPIC_API_KEY` | none | Required if `anthropic` is listed. |
| `OPENAI_API_KEY` | none | Required if `openai` is in `LLM_PROVIDERS`, or for OpenAI transcription when `OPENAI_TRANSCRIBE_API_KEY` is unset. Not needed for a self-hosted transcription server. |
| `OPENAI_MODEL` | none | Required if `openai` is in `LLM_PROVIDERS`. It has no default so a stale model id is never pinned. |
| `VIDEO_PROVIDERS` | empty | Ordered, comma-separated: `omni`. Empty hides the Video Generation tool. |
| `GEMINI_API_KEY` | none | Required if `omni` is listed. A Google AI Studio key from a project with billing enabled. |
| `OMNI_MODEL` | `gemini-omni-1.1-flash` | Model id sent to the Gemini API. |
| `MAX_UPLOAD_GB` | `10` | Largest accepted upload. Larger files are rejected before transfer. |
| `JOB_TTL_HOURS` | `24` | How long job data is kept. |

Example of OpenAI transcription with an OpenAI fallback for text generation:

```bash
TRANSCRIBE_PROVIDERS=openai
LLM_PROVIDERS=anthropic,openai
ANTHROPIC_API_KEY=<your-anthropic-api-key>
OPENAI_API_KEY=<your-openai-api-key>
OPENAI_MODEL=<your-openai-model-id>
```

Example of enabling Video Generation:

```bash
VIDEO_PROVIDERS=omni
GEMINI_API_KEY=<your-api-key>
```

Video Generation uses Google's Gemini Omni Flash, which is available on the paid tier only:

- The key must belong to a Google AI Studio project with billing enabled. Billing is prepaid: you
  top up credit, and a clip fails with the "provider account needs topping up" message when it runs
  out.
- Tier 1 projects have a rolling spend cap of US$10 per 10 minutes, so a burst of high-resolution
  clips can be rejected for a while. Check your project's tier and limits in AI Studio.
- VideoKit itself enforces no spending limit. Control spending through the provider account.

## Transcription

VideoKit does not transcribe inside the container, so the image stays small and needs no GPU. Audio
goes to an OpenAI-compatible transcription API, which is either OpenAI's or a server you run yourself.

- **OpenAI:** set `TRANSCRIBE_PROVIDERS=openai` and `OPENAI_API_KEY` (or `OPENAI_TRANSCRIBE_API_KEY`
  to use a different key for transcription only).
- **Self-hosted:** also set `OPENAI_TRANSCRIBE_URL` to your server's base URL including the API
  prefix, and `OPENAI_TRANSCRIBE_MODEL` to a model the server serves. Any OpenAI-compatible server
  works, for example [speaches](https://github.com/speaches-ai/speaches); the server may use a GPU,
  VideoKit does not need one. An `https://` address whose host is `api.openai.com` counts as
  OpenAI's API; `http://api.openai.com` is refused at startup, because the OpenAI key is never sent
  over plain HTTP. Without a server key no key is sent.
- Each transcription request may take up to 30 minutes. That is enough for a long video on a
  reasonably fast server (for example one with a GPU); on a CPU-only server, long videos may time out.

Example for a server running on the Docker host, listening on port 8000:

```bash
TRANSCRIBE_PROVIDERS=openai
OPENAI_TRANSCRIBE_URL=http://host.docker.internal:8000/v1
OPENAI_TRANSCRIBE_MODEL=Systran/faster-whisper-large-v3
```

From inside the container, the host is reachable as `host.docker.internal` once you map it. With
`docker run`, add `--add-host host.docker.internal:host-gateway`; in `compose.yaml`, add under the
service:

```yaml
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

`GET /api/health` reports the transcription provider as `model=<model>` for OpenAI's API and
`model=<model>, self-hosted` for any other address. It makes no network call and never shows the
address or keys.

## Upgrading from 0.3

Version 0.4.0 removed the in-process Whisper (`local`) transcription provider. An `.env` that still
lists it fails at startup with `TRANSCRIBE_PROVIDERS lists 'local', which was removed in 0.4.0 (use
openai, with OPENAI_TRANSCRIBE_URL for a self-hosted server)`. To upgrade:

1. Set `TRANSCRIBE_PROVIDERS=openai`.
2. For a self-hosted server, set `OPENAI_TRANSCRIBE_URL` and `OPENAI_TRANSCRIBE_MODEL` (and
   `OPENAI_TRANSCRIBE_API_KEY` if the server needs one). For OpenAI's API, set `OPENAI_API_KEY`.
   See [Transcription](#transcription) and [Configuration](#configuration).
3. Remove `WHISPER_MODEL`, `WHISPER_DEVICE` and the GPU reservation in `compose.yaml` or the
   `--gpus` flag of `docker run`; the container no longer needs a GPU.
4. Optionally delete `<data>/models` and `<data>/huggingface` (the old model cache). VideoKit never
   deletes them.

## Cost

Only the cloud providers you configure cost money. With the default configuration, text generation
is estimated to cost under US$0.50 per 30-minute video, and regenerating one section costs a small
fraction of that. Cheap regeneration relies on the provider's prompt cache, which lives about five
minutes and is refreshed by each use; a regeneration after a longer pause pays for the video's
transcript and frames again. Transcription with OpenAI's `whisper-1` adds roughly US$0.006 per audio
minute; transcription on a self-hosted server is free to VideoKit, whatever the model is called. Figures are estimates based on published per-token prices,
shown in each job's footer. If prices change, override them with `PRICING_OVERRIDES` (see the
configuration reference).

Video Generation is billed by the provider per generated second, and it is much more expensive than
the other tools: roughly US$0.10 per second at 720p and US$0.15 per second at 1080p (video output
at US$17.50 per million tokens, 5,792 tokens per second at 720p and 8,688 at 1080p), so an 8-second
1080p clip costs about US$1.20. Each picture adds a small input charge (about US$0.0017). The form
shows the estimate before you submit, and the finished job shows the actual cost. A failed or
cancelled attempt may still be charged by the provider.

## Using it from a phone

VideoKit is built for phone browsers (current iOS Safari and Android Chrome) as well as desktop.
The layout fits screens from 360 px wide, and the file picker lets you choose a video from the
gallery or files. If the screen locks during an upload, it resumes when you return, and if the page
was reloaded, selecting the same file again continues from the last confirmed point. Processing
continues on the server while you are away; reopen the job link to see the results.

To install it, choose **Add to Home Screen** in the browser's share or menu. It opens full-screen
with its own name and icon. This needs HTTPS (see the authentication section above).

## Data lifecycle and privacy

- The uploaded source video is deleted as soon as extraction finishes, or when the job fails or is
  cancelled.
- Job data (transcript, frames, results) is kept for `JOB_TTL_HOURS` (default 24) so you can
  regenerate sections, then purged automatically. Incomplete uploads are removed after the same
  period. Cancelling a job deletes its data immediately.
- If the server restarts while YouTube Prep or Audio Extract jobs are queued or running, those jobs
  are marked failed and you need to upload again. Video Generation jobs continue instead (see
  [Video Generation](#video-generation)).
- Video Generation keeps its pictures, prompt and clip with the job for the same period, so Try
  again works for that long. They are deleted with the job.
- There is no job history list on the server; the job link is the only way back to a job (Video
  Generation also remembers recent clips in your browser).
- Logs and user-facing errors never contain API keys, transcript text or stack traces.

What leaves your machine depends on the providers you choose:

| Data | Where it goes |
| --- | --- |
| Audio | Sent to the transcription server: OpenAI's API, or your own server when `OPENAI_TRANSCRIBE_URL` is set (then it goes only there). |
| Transcript text and extracted frames | Sent to the provider(s) in `LLM_PROVIDERS` (Anthropic by default, OpenAI as an option) to generate and regenerate the text. |
| Video Generation prompt and pictures | Sent to Google (Gemini API) if `omni` is in `VIDEO_PROVIDERS`. Pictures are re-encoded as JPEG first, which drops their metadata, including GPS. |

The full video file is never sent to any provider. By using cloud providers you accept their terms
and data handling.

Google stores each Video Generation request on its side for the retention period of your AI Studio
project (55 days by default on the paid tier). VideoKit deletes each stored request right after it
has downloaded the clip (a best-effort call), but a request that fails or is cancelled may stay
until that period ends.
Check your project's data settings if that matters to you.

## Development

Prerequisites: [mise](https://mise.jdx.dev/), `ffmpeg` and beads (`br`, 1.9.0 or newer; the pre-push hook needs it).

```bash
mise install    # python, uv, node, task, gitleaks, hadolint, actionlint, pre-commit, git-cliff
task setup      # backend (uv) and frontend (npm) dependencies
pre-commit install
git config merge.beads.driver "br merge-driver %O %A %B"
```

Day to day:

```bash
task lint      # ruff, ruff format, mypy, vue-tsc, ESLint, Prettier, hadolint, actionlint
task test      # backend and frontend tests with coverage (80 % line coverage floor)
task e2e       # Playwright end-to-end tests (desktop and phone emulation)
task format    # auto-format both stacks
```

- Build and run the image locally with `task docker:build` and `task docker:run`. They use the same `./.data` directory as Compose.
- Releases are tag-driven; see [`docs/RELEASING.md`](docs/RELEASING.md). Working on the code:
  [`CONTRIBUTING.md`](CONTRIBUTING.md).
- The default test suite needs no API keys, no network access to AI providers; it uses
  fakes and tiny generated video fixtures.
- Tests that call real providers are opt-in. Provide keys in your environment and run
  `cd backend && uv run pytest -m live --no-cov`.
- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/)
  (`feat:`, `fix:`, `docs:`, `chore:` and so on).
- Design documents live in [`specs/001-videokit-youtube-prep/`](specs/001-videokit-youtube-prep/)
  (the product), [`specs/002-packaging-releases/`](specs/002-packaging-releases/) (the slim image
  and the release flow), [`specs/003-audio-extract/`](specs/003-audio-extract/) (Audio
  Extract) and [`specs/004-video-generation/`](specs/004-video-generation/) (Video Generation):
  `spec.md`, `plan.md`, `research.md`, `data-model.md`, `quickstart.md` and the
  `contracts/`.
  The project principles are in
  [`.specify/memory/constitution.md`](.specify/memory/constitution.md).
- Never commit personal data, real hostnames, or secrets. Use placeholders such as `example.com` and
  `<your-api-key>`. CI scans the full history with gitleaks.

### Test and coverage badges

The badges at the top read JSON files from a GitHub gist that CI updates on every push to `main`.
In a fork, create your own gist, point the badge URLs at it (`<gist-user>/<gist-id>`), and configure
the `GIST_ID` secret (the gist id) and a `GIST_TOKEN` secret (a token with the `gist` scope) so the
workflow in [`.github/workflows/ci.yml`](.github/workflows/ci.yml) can publish them.

## License

[MIT](LICENSE), copyright Anton Krivenko.
