# Internal Interface Contract: Audio Extract (delta)

Base contract: `specs/001-videokit-youtube-prep/contracts/internal-interfaces.md`. Core still never
imports tools; tools still never spawn ffmpeg or import vendor SDKs.

## JobHandle — added

```python
@property
def source_name(self) -> str | None:
    """The original upload file name (sanitized by core), or None when the client sent none."""
```

## Job snapshot — added

`Job.source_name: str | None` (read through `JobAccess.get`).

## MediaService — added

```python
@dataclass(frozen=True)
class AudioTrack:
    file: str          # "audio.mp3", relative to the job dir
    duration_s: float
    size_bytes: int

def extract_mp3(self, job_dir: Path) -> AudioTrack:
    """Encode the source's main audio track (default-flagged, else first) to a 192 kbps CBR MP3:
    mono stays mono, stereo stays stereo, more channels → stereo, > 48 kHz → 48 kHz,
    < 32 kHz → 44.1 kHz (keeps 192 kbps), no metadata.
    Deletes source.bin on success. Raises AppError: not_a_video | no_audio | extraction_failed."""
```

## Worker / JobStore — changed behaviour

- `JobStore.next_queued(tool_id: str) -> str | None` — oldest queued job of that tool.
- `JobStore.queue_position(job_id)` — counts only the same tool's queued jobs.
- `JobStore.create(tool_id, upload_length, source_name: str | None = None)`.
- `Worker` runs one loop per registered tool; each runs its tool's jobs one at a time.
- `TRANSITIONS[EXTRACTING]` gains `DONE`.

## Audio Extract tool

```python
class AudioExtractTool:
    id = "audio_extract"
    name = "Audio Extract"
    description = "Get a video's audio as an MP3."
    router = routes.router          # GET/HEAD /jobs/{job_id}/audio
    async def run(self, job: JobHandle, ctx: CoreContext) -> None:
        # set_state("extracting") → ctx.media.extract_mp3(job.dir) in a thread
        # → write result.json {file, download_name, duration_s, size_bytes, bitrate_kbps}
```

Frontend tool module `frontend/src/tools/audio-extract/index.ts`: `id = 'audio_extract'`, routes
`/tools/audio-extract` and `/tools/audio-extract/jobs/:id`, labels
`tools.audio_extract.name` / `tools.audio_extract.description`.
