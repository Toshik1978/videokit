# Quickstart: validating Audio Extract

Prerequisites: `mise install && task setup`; ffmpeg on `PATH` (the dev shell and image have it).
Contracts: [contracts/http-api.md](contracts/http-api.md). Data: [data-model.md](data-model.md).

## 1. Automated suites (no keys, no network, no GPU)

```bash
task lint && task test     # backend + frontend, 80 % coverage gate per stack
task e2e                   # Playwright: desktop-chrome, iphone, pixel, mobile-360
```

Expected: green. The new tests cover naming rules (A6), track choice/channels/sample rate/metadata
(A3–A4, ffmpeg-generated fixtures), per-tool lanes (A1), `extracting → done` (A2), `source_name`
capture (A5), the download route (A7) and the Share/Download UI (A8–A9).

## 2. Manual run

```bash
task docker:build && task docker:run      # needs a valid .env, as for YouTube Prep
```

Make a sample video:

```bash
ffmpeg -f lavfi -i testsrc=duration=20:size=640x360:rate=25 \
       -f lavfi -i sine=frequency=440:duration=20 -shortest -c:v libx264 -c:a aac "Sample trip.mp4"
```

1. Open `http://localhost:8000/` → two cards: Audio Extract and YouTube Prep.
2. Audio Extract → pick `Sample trip.mp4` → progress → `Extracting` → Download.
3. Download saves `Sample trip.mp3`; `ffprobe` shows `mp3`, ~192 kb/s, duration ≈ 20 s.
4. Reload the job link → Download still works and gives the same file.
5. Start a long YouTube Prep job, then an Audio Extract job: the Audio Extract job is not shown
   as waiting behind it and finishes first (FR-018).
6. Upload a video made with `-an` (no audio) → failed with the "no audio" message, no Download.
7. On a phone (iOS Safari / Android Chrome, HTTPS origin): Share opens the system share sheet with
   `Sample trip.mp3`; choosing a messenger sends the audio file. On desktop Chrome/Firefox only
   Download is shown.

## 3. API spot checks

```bash
curl -sI http://localhost:8000/api/tools/audio_extract/jobs/<job-id>/audio   # 200, audio/mpeg, attachment
curl -s  http://localhost:8000/api/tools/audio_extract/jobs/nope/audio         # 404 job_not_found
```
