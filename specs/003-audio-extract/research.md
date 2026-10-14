# Research: Audio Extract Tool

Decisions for [plan.md](plan.md). Numbering is local to this feature (A1–A12); `R*` refers to
`specs/001-videokit-youtube-prep/research.md`.

## A1. Separate queue lanes (FR-018)

- **Decision**: One FIFO lane per tool id. The worker runs one loop task per registered tool; each
  loop takes the oldest `queued` job *of its tool* (`JobStore.next_queued(tool_id)`), so at most
  one job per tool runs at a time. `queue_position` counts only queued jobs of the same tool.
  `/api/health`'s `queue_length` stays the total across lanes. The worker tracks the set of jobs it
  is starting (`_current: set[str]`) so cancellation keeps its race protection per job.
- **Rationale**: Exactly what the clarification asks (one Audio Extract job next to at most one
  YouTube Prep job), with no new configuration and no tool-specific code in core. YouTube Prep's
  behaviour is unchanged: its lane still runs one job at a time.
- **Alternatives considered**: a `Tool.lane` attribute letting tools share a lane (no current need;
  revisit if a third heavy tool competes with transcription for the GPU); a general concurrency
  limit N (does not stop two transcriptions running together).

## A2. Single-stage tools in the state machine

- **Decision**: Allow `extracting → done`. Audio Extract goes `uploading → queued → extracting →
  done`; the worker's existing `_finish` moves the job to `done` after `run` returns.
- **Rationale**: "Extracting" is already the user-facing stage name and describes the work. One
  added edge keeps the enforced state machine honest.
- **Alternatives considered**: a new `converting` state (a new UI label and catalog string for no
  user benefit); skipping through `transcribing`/`generating` (lies about the work).

## A3. MP3 encoding (FR-002)

- **Decision**: `MediaService.extract_mp3(job_dir)` in core runs
  `ffmpeg -v error -y -i source.bin -map 0:<idx> -vn -sn -dn -map_metadata -1 -map_chapters -1
  -c:a libmp3lame -b:a 192k [-ac 2] [-ar 48000 | -ar 44100] audio.mp3`.
  `-ac 2` only when the chosen track has more than two channels (mono stays mono, stereo stays
  stereo). `-ar 48000` only when the source rate is above 48 kHz (MP3 does not support higher
  rates; libmp3lame rejects them). `-ar 44100` only when the source rate is below 32 kHz: MPEG-2
  and 2.5 layer III top out at 160 kbps, so libmp3lame would silently drop the bitrate, and
  44.1 kHz keeps the promised 192 kbps. Metadata and chapters are dropped so phone tags such as
  location or device never reach the shared file.
- **Rationale**: Constitution assigns ffmpeg to core (`core` owns media processing); tools must not
  spawn ffmpeg. The Docker image's ffmpeg already encodes MP3 (YouTube Prep makes a 32 kbps MP3 for
  cloud transcription, R6).
- **Alternatives considered**: VBR `-q:a 2` (similar size and quality, but the user agreed on
  192 kbps and CBR keeps the size predictable); stream copy of the original track (rejected by the
  user in favour of MP3).

## A4. Choosing the audio track (FR-003)

- **Decision**: From `ffprobe` streams, the first audio stream with `disposition.default == 1`;
  else the first audio stream. Map it explicitly by stream index. Attached-picture and data streams
  are ignored. No audio stream → `AppError("no_audio", "The video has no audio track.", 422)`.
- **Rationale**: Matches the spec wording and how players pick a track. ffmpeg's implicit choice
  picks the track with the most channels, which is not the "main" track.
- **Alternatives considered**: ffmpeg default selection (see above); letting the user pick a track
  (out of scope).

## A5. Remembering the original file name (FR-005)

- **Decision**: `POST /api/jobs` accepts an optional `file_name` (string, ≤ 1024 characters). Core
  keeps only its last path component, removes control characters, trims it to 255 characters and
  stores it in a new nullable `jobs.source_name` column (added by the existing in-place migration
  list in `core/db.py`). Tools read it through a new read-only `JobHandle.source_name`; it is not
  returned by `GET /api/jobs/{id}` and is never logged. It is deleted with the job row by the purge.
  `UploadPanel` sends `file.name` for every tool.
- **Rationale**: The name must survive reopening the job link on another device (US1-AS4), so it
  has to live on the server. Sending it from the job-creation call works for every tool without
  tus metadata parsing.
- **Alternatives considered**: tus `Upload-Metadata` (base64 pairs parsed in the upload code for
  one field); keeping it only in the browser (lost on another device).

## A6. Download name rules (FR-005)

- **Decision**: A pure function in the tool, `naming.download_name(source_name: str | None) -> str`:
  drop the last extension when the text after the last dot is 1–8 letters or digits and the stem
  before it is not empty; replace `/ \ : * ? " < > |` and control characters with `_`; strip
  leading/trailing spaces and dots; cut the stem to 150 characters; an empty stem becomes `audio`;
  append `.mp3`. Non-Latin characters are kept unchanged.
  Examples: `trip.mp4` → `trip.mp3`; `Поездка в горы.final.mov` → `Поездка в горы.final.mp3`;
  `clip` → `clip.mp3`; `v1.2 final` → `v1.2 final.mp3`; `???.mp4` → `___.mp3`; `None`/`""`/`.mp4` →
  `audio.mp3`.
- **Rationale**: Deterministic rules live in tool code (Constitution III) and are table-testable.

## A7. Serving the download (FR-006)

- **Decision**: Tool route `GET /api/tools/audio_extract/jobs/{job_id}/audio` returns a Starlette
  `FileResponse` (`audio/mpeg`; HEAD and byte-range requests are supported by Starlette 1.x). The
  route builds `Content-Disposition` itself: `attachment` with a quoted ASCII `filename`, plus
  `filename*=UTF-8''…` (the whole name percent-encoded) when the name is non-ASCII or contains `%`
  (some browsers decode `%XX` in a plain `filename`; all prefer `filename*`). For a non-ASCII name
  the ASCII `filename` is a fallback: accents are transliterated (NFKD), every character outside a
  conservative allowlist becomes `_` (NFKD maps fullwidth quotes, slashes and backslashes back to
  ASCII, so they are replaced too), and it is `audio.mp3` when no letter or digit is left. The name
  is the `download_name` stored in the job's `result.json` (derived again from the uploaded name
  only when it is absent or unreadable), so Download and Share always agree. It uses the existing
  `JobAccess.get` (404 `job_not_found` for unknown, expired or other-tool jobs) and
  `JobAccess.handle(id).dir`; `409 job_not_done` before `done`. No core change is needed for
  serving.
- **Rationale**: Keeps file knowledge in the tool; core already exposes everything required.
- **Alternatives considered**: a generic core `GET /api/jobs/{id}/files/{name}` (speculative; one
  tool needs it today).

## A8. Download button and expiry in the UI

- **Decision**: Download is an `<a href download>` link (keyboard/long-press friendly). Its click
  handler prevents the default, re-checks `GET /api/jobs/{id}`; on 404 the page switches to the
  catalog's "expired" message, otherwise it navigates to the download URL
  (`window.location.assign`), which the browser saves as an attachment.
- **Rationale**: Large files stream straight to disk (no in-memory blob on phones), and an expired
  job shows catalog text instead of the server's JSON error (FR-036 of 001).

## A9. Share to apps (FR-017)

- **Decision**: Web Share API with files. The Share button is shown only when
  `navigator.canShare?.({ files: [new File([], 'a.mp3', { type: 'audio/mpeg' })] })` is true. On
  tap: fetch the MP3 as a blob (button shows "Preparing…"), build
  `new File([blob], result.download_name, { type: 'audio/mpeg' })`, call `navigator.share({ files })`.
  If the browser rejects with `NotAllowedError` because the tap's user activation expired during a
  long fetch, keep the file and show "Ready — tap Share again"; the next tap shares at once.
  `AbortError` (sheet dismissed) is silent. Other errors show the generic catalog message. The
  fetched file is cached for the page's lifetime only.
- **Rationale**: Safari requires `share()` inside a user gesture; fetching first then sharing works
  for typical files, and the second-tap fallback covers long fetches without prefetching up to
  ~130 MB on every page view.
- **Alternatives considered**: prefetch on page load (wastes data on desktop and when the user only
  downloads); share a URL instead of the file (Telegram would get a link that expires, not audio).

## A10. Result shape

- **Decision**: The tool writes `result.json`:
  `{"file": "audio.mp3", "download_name": "trip.mp3", "duration_s": 754.2, "size_bytes": 18101234,
  "bitrate_kbps": 192}`. Duration comes from probing the produced MP3; size from the file. Core's
  existing `_read_result` returns it in `GET /api/jobs/{id}`. Usage stays at zero and the page shows
  no cost footer (FR-007).

## A11. Cancellation

- **Decision**: Unchanged semantics: a running job stops at the next stage boundary. Audio Extract
  has one stage, so a cancel during `extracting` takes effect when ffmpeg finishes (under a minute
  for a 30-minute video, SC-002); the job then becomes `cancelled` and its directory is deleted.
- **Alternatives considered**: killing ffmpeg mid-run (needs a cancellable subprocess API in core;
  not needed at this duration).

## A12. Test fixtures

- **Decision**: Extend the `make_video` fixture (ffmpeg `lavfi`) to build: no-audio video; video with
  two audio tracks where the second is default and differs in channel count (mono first, stereo
  default) so the chosen track is observable from the output's channels; 5.1 track; 96 kHz track;
  a file with a `location` metadata tag. Assertions probe the MP3 (codec `mp3`, bitrate ≈ 192 kbps,
  channels, sample rate, duration within 1 s, no tags). The web-share flow is covered by Vitest with
  a stubbed `navigator`; Playwright covers upload → done → download on the existing projects.
