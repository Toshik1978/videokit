# Feature Specification: Audio Extract Tool

**Feature Branch**: `003-audio-extract`

**Created**: 2026-10-14

**Status**: Draft

**Input**: User description: "New tool 'Audio Extract' — the second VideoKit tool alongside
YouTube Prep. The user uploads one video and downloads its audio track as an MP3, to send it to
Telegram and reuse it in other video creation; used often. MP3 at good quality; one video at a
time; the download keeps the video's name; same resumable upload as YouTube Prep; no AI and no
cost; files expire on the same schedule as YouTube Prep results; a video with no audio fails with
a clear message; a video with several audio tracks yields the main one."

## Clarifications

### Session 2026-10-14

- Q: Should the done page have a Share button as well as Download? → A: Yes. Download plus Share,
  with Share shown only where the browser supports sharing files.
- Q: Should an Audio Extract job wait in the same one-at-a-time line as YouTube Prep jobs, or run
  while a YouTube Prep job is processing? → A: Separate line. Audio Extract jobs run one at a time
  in their own line, alongside at most one running YouTube Prep job.
- Q: Should VideoKit be able to start with no AI keys at all for Audio Extract's sake? → A: No.
  Startup validation stays as it is; Audio Extract simply never uses an AI provider and has no
  cost. A no-AI startup mode is out of scope.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Get a video's audio as an MP3 (Priority: P1)

A video creator opens VideoKit, chooses Audio Extract and uploads a video (for example,
`trip.mp4`, filmed on a phone). While it uploads and processes they see progress and the current
stage. When processing finishes they press Download and get `trip.mp3`, which they send to a
messenger or drop into another video project. They do this often, so the flow is short: pick a
file, wait, download.

**Why this priority**: This is the whole tool; without it nothing else in this feature has value.

**Independent Test**: Upload a short sample video with an audio track; verify the job reaches
done and the downloaded file is a playable MP3 whose duration matches the video's and whose name
is the video's name with the `.mp3` extension.

**Acceptance Scenarios**:

1. **Given** the Audio Extract page, **When** the user uploads a valid video with audio, **Then**
   the page shows upload progress, then the stages queued → extracting, and finally a Download
   button (plus a Share button where the browser can share files).
2. **Given** a completed job for `trip.mp4`, **When** the user presses Download, **Then** the
   browser saves a file named `trip.mp3`.
3. **Given** a completed job, **When** the downloaded file is played, **Then** it contains the
   video's audio from start to end (duration within 1 second of the video's) at the agreed quality.
4. **Given** a completed job, **When** the user reopens the job link later within the retention
   period, **Then** the Download button is still there and downloads the same file again.
5. **Given** a running VideoKit whose AI providers are unavailable or failing, **When** a user runs
   Audio Extract, **Then** it works exactly the same; no cost is shown for the job.
6. **Given** a completed job on a phone whose browser can share files, **When** the user presses
   Share and picks a messenger, **Then** the messenger receives `trip.mp3`.
7. **Given** a job that is uploading, queued or extracting, **When** the user cancels it, **Then**
   the job shows as cancelled and all of its data is deleted.

---

### User Story 2 - Clear outcome for videos without usable audio (Priority: P2)

A user uploads a screen recording that has no sound, or a file that is not a video at all. Instead
of an empty or broken MP3 they are told plainly what is wrong.

**Why this priority**: Without it the tool silently produces useless files or cryptic errors, but
the main flow (Story 1) already delivers value on its own.

**Independent Test**: Upload a video with no audio track and a non-video file; verify each job
ends failed with its own readable message and no file is offered for download.

**Acceptance Scenarios**:

1. **Given** a video with no audio track, **When** processing runs, **Then** the job ends failed
   with the message that the video has no audio, and no Download button is shown.
2. **Given** a file that is not a playable video, **When** the upload completes, **Then** the job
   ends failed with the same "not a playable video" message YouTube Prep uses.
3. **Given** a video with several audio tracks, **When** processing runs, **Then** the MP3
   contains only the video's main (default) audio track.

---

### User Story 3 - Audio Extract on the home page (Priority: P3)

A user opening VideoKit sees Audio Extract as a second tool card next to YouTube Prep, with a name
and a one-line description, and opens it with one tap.

**Why this priority**: Users can reach the tool by its direct link without it, but discovery from
the home page is how the toolbox is meant to be used.

**Independent Test**: Open the home page; verify both tool cards are shown and the Audio Extract
card opens the Audio Extract page.

**Acceptance Scenarios**:

1. **Given** the home page, **When** it loads, **Then** it shows the Audio Extract card alongside
   YouTube Prep.
2. **Given** the home page, **When** the user selects the Audio Extract card, **Then** the Audio
   Extract page opens.

---

### Edge Cases

- Video file name with non-Latin characters, spaces or several dots (`Поездка в горы.final.mov`):
  the download name keeps it intact and only the last extension becomes `.mp3`
  (`Поездка в горы.final.mp3`).
- Video file name without an extension (`clip`): the download is named `clip.mp3`.
- File name that is empty or made only of characters unsafe in file names: the download falls back
  to `audio.mp3`.
- Very short video (a few seconds): still produces an MP3; there is no minimum length.
- Long video (90 minutes): produces an MP3 of the full length.
- Video whose audio track is present but silent: produces a silent MP3 (silence is valid audio).
- Job expired: opening the job link or the download says the job has expired and the user must
  upload again.
- User closes the browser during processing: processing continues; reopening the job link shows
  the current status or the Download button until the job expires.
- Server restarts while the job is queued or extracting: the job is marked failed with the same
  "server restarted, please upload again" message YouTube Prep uses.
- YouTube Prep and Audio Extract jobs submitted at the same time: each waits only in its own
  tool's line, so an Audio Extract job starts without waiting for a running YouTube Prep job; the
  queue position shown counts only jobs of the same tool.

## Requirements *(mandatory)*

### Functional Requirements

**Audio Extract tool**

- **FR-001**: The system MUST provide an Audio Extract tool that takes one uploaded video and
  produces one MP3 file containing the video's audio track.
- **FR-002**: The MP3 MUST be encoded at a constant 192 kbps, keeping the source's channel layout
  (mono stays mono, stereo stays stereo; more than two channels are mixed down to stereo).
- **FR-003**: When a video has several audio tracks, the system MUST use the track the video marks
  as default, or the first audio track when none is marked.
- **FR-004**: When a video has no audio track, the job MUST end failed with a readable message
  saying the video has no audio, and no file MUST be offered.
- **FR-005**: The downloaded file MUST be named after the uploaded video's original name with its
  last extension replaced by `.mp3` (or `.mp3` appended when there is none), preserving non-Latin
  characters; characters unsafe in file names MUST be replaced, and an empty result MUST fall back
  to `audio.mp3`.
- **FR-006**: The user MUST be able to download the MP3 any number of times while the job is
  retained; after it expires the download MUST say the job has expired.
- **FR-007**: The tool MUST NOT use any AI provider, its jobs MUST succeed regardless of the AI
  providers' readiness, and its jobs carry no cost. VideoKit's existing startup validation of
  configured providers is unchanged.
- **FR-008**: The completed job page MUST show a Download button and the audio's duration and file
  size.
- **FR-009**: The job MUST show its current stage: uploading (with percentage), queued (with
  position), extracting, done, cancelled, or failed (with a readable reason).

**Platform (reused, extended where needed)**

- **FR-010**: Audio Extract MUST use the same resumable, chunked upload, size limit, playable-video
  check, cancellation, shareable job link and restart handling as YouTube Prep.
- **FR-018**: Audio Extract jobs MUST run one at a time in their own first-in-first-out line,
  independent of YouTube Prep's line: at most one Audio Extract job and one YouTube Prep job run at
  the same time, and a job's queue position counts only jobs waiting in the same line. YouTube
  Prep's own behaviour (one job at a time) MUST NOT change.
- **FR-011**: The platform MUST let a tool offer a file produced by a job for download, without
  the platform depending on any specific tool; adding Audio Extract MUST NOT require changes to
  YouTube Prep.
- **FR-012**: The uploaded source video MUST be deleted as soon as the MP3 is produced, or as soon
  as the job fails or is cancelled.
- **FR-013**: The MP3 MUST be deleted together with the rest of the job's data at the end of the
  same configurable retention period that applies to YouTube Prep jobs.
- **FR-014**: Logs and user-facing messages MUST NOT contain the file's content, its original name,
  credentials or internal error traces.
- **FR-015**: Every user-facing string of the tool MUST come from the interface translation
  catalog.
- **FR-016**: The Audio Extract page MUST be fully usable on screens from 360 px wide in current
  iOS Safari and Android Chrome: no horizontal scrolling, touch targets at least 44×44 px, file
  selection from the device gallery/files, and the downloaded file saved by the browser's normal
  download handling.
- **FR-017**: The completed job page MUST offer a Share button next to Download wherever the
  browser supports sharing files, handing the MP3 (with its download name) to the device's share
  sheet so it can be sent straight to apps such as Telegram; where sharing files is unsupported
  (typically desktop browsers) only Download is shown. Dismissing the share sheet is not an error.

### Key Entities

- **Audio Extract job**: a job of the shared job system for this tool; holds the stage, the
  original video name (used only to name the download), and on success the produced audio's
  duration and size.
- **Audio file**: the MP3 produced by a job; stored with the job's data, downloadable while the
  job is retained, deleted when the job expires or is cancelled.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: From opening the home page, a user can get the MP3 of a short phone video downloaded
  in 4 taps or fewer, excluding the device's own file picker.
- **SC-002**: For a 30-minute video, the time from upload complete to Download available is under
  1 minute on a typical CPU-only host, even while a YouTube Prep job is running.
- **SC-003**: The produced MP3 plays in common phone players and messengers, and its duration is
  within 1 second of the video's.
- **SC-004**: 100 % of videos without an audio track end with the "no audio" message; none produce
  a downloadable file.
- **SC-005**: No job data, including the MP3, remains more than 1 hour after the retention period
  ends.
- **SC-006**: The complete flow can be done on a 360 px-wide phone in both iOS Safari and Android
  Chrome without horizontal scrolling or zooming.

## Assumptions

- Users are the same individual creators who use YouTube Prep; one video at a time is enough and
  batch processing is out of scope.
- Quality: MP3 at 192 kbps is good enough both for messengers and for reuse in video edits; other
  formats, bitrates and lossless copies of the original track are out of scope.
- Trimming, volume normalisation, noise removal and other audio editing are out of scope.
- No in-page audio player is required; the user listens after downloading.
- Running one Audio Extract job next to one YouTube Prep job is within what a typical self-hosted
  host can handle; audio extraction is light compared with transcription.
- Retention, maximum upload size and accepted video formats are the existing platform settings;
  this tool adds no settings of its own.
- No job history list is provided; the job link is the only way back to a job.
- Starting VideoKit without any AI provider credentials (a no-AI mode) is out of scope.
