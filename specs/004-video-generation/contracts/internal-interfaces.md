# Internal Interface Contract: Video Generation (delta)

Base contract: `specs/001-videokit-youtube-prep/contracts/internal-interfaces.md` plus the 003
delta. Core still never imports tools; tools still never spawn ffmpeg or import vendor SDKs.

## Tool — changed (every tool implements all members)

```python
@runtime_checkable
class Tool(Protocol):
    id: str
    name: str
    description: str
    router: APIRouter                 # now also holds the tool's job-creation route
    concurrency: int                  # jobs of this tool that may run at once (YouTube Prep 1, Audio Extract 1, Video Generation 3)
    resume_after_restart: bool        # False: waiting/running jobs fail with server_restarted (today); True: they continue

    def enabled(self, settings: Settings) -> bool:
        """Whether the tool is mounted, listed and given a lane (Video Generation: VIDEO_PROVIDERS non-empty)."""

    def accept(self, job: JobHandle, ctx: CoreContext) -> None:
        """Blocking. Called once when the job's inputs are complete (last tus byte, or `JobAccess.complete`).
        Return to queue the job; raise AppError(code, message) to fail it (inputs are then deleted).
        YouTube Prep / Audio Extract: `ctx.media.probe_source(job.dir)` and record `duration_s` (today's check).
        Video Generation: check `inputs.json` is present and complete."""

    async def run(self, job: JobHandle, ctx: CoreContext) -> None: ...
```

Discovery validates the new members (`concurrency ≥ 1`, booleans, callables) and rejects a tool
missing any of them (`RegistryError`).

## JobAccess — added

```python
def create(self, tool_id: str, *, upload_length: int = 0, source_name: str | None = None) -> Job:
    """A new job in `uploading`. With `upload_length > 0` its tus resource is `/api/uploads/<id>`
    (size limits enforced: AppError 413 file_too_large / 422 invalid_request). With 0 the tool writes its
    inputs into `job_dir(id)` itself and then calls `complete`."""

def job_dir(self, job_id: str) -> Path: ...

async def complete(self, job_id: str) -> None:
    """Inputs are complete: run the tool's `accept` and queue the job (or fail it). Idempotent; the same
    path the tus upload takes on its last byte."""

async def discard(self, job_id: str) -> None:
    """Delete a job whose inputs could not be written (validation failed mid-request)."""
```

Each tool's creation route is a few lines over these (shared helper for video uploads:
`videokit.core.uploads.create_upload_job(request, tool_id, body)` with `body: uploads.CreateUploadRequest`
(`size`, optional `file_name`), returning the 201 payload). `discard` leaves a job that is no longer
`uploading`, or an unknown one, alone.

## CoreContext — added

```python
video: VideoChain | None   # None when VIDEO_PROVIDERS is empty
```

## MediaService — added

```python
def probe_source(self, job_dir: Path) -> ProbeResult:
    """Today's upload check, moved behind the media service: probe source.bin; AppError not_a_video."""

def normalize_picture(self, data_path: Path, out_path: Path) -> None:
    """Decode JPEG/PNG/WebP/HEIC (first image of a HEIF sequence), write a JPEG with no metadata,
    long side ≤ 2048 px. AppError invalid_picture when it does not decode."""

@dataclass(frozen=True)
class Clip:
    file: str; thumbnail: str; duration_s: float; width: int; height: int; size_bytes: int

def finalize_clip(self, raw: Path, job_dir: Path) -> Clip:
    """raw provider video → clip.mp4 (H.264 copied when possible else re-encoded; audio removed;
    metadata stripped; +faststart) and thumbnail.jpg. Deletes raw. AppError video_failed."""
```

## Video generation seam (`videokit.core.videogen`)

```python
@dataclass(frozen=True)
class Picture:
    position: int          # 1-based
    jpeg: bytes

@dataclass(frozen=True)
class VideoRequest:
    prompt: str
    pictures: list[Picture]
    duration_s: int
    aspect_ratio: Literal["9:16", "16:9"]
    resolution: Literal["720p", "1080p"]
    # always silent: providers request no audio where the API allows it

@dataclass(frozen=True)
class Capabilities:
    durations_s: list[int]
    max_pictures: int
    video_tokens_per_second: dict[str, int]   # {"720p": 5792, "1080p": 8688} — fallback usage and the estimate
    tokens_per_image: int                     # 1120

@dataclass(frozen=True)
class Accepted:
    provider: str          # "<provider>:<model>"
    ref: str               # provider reference (Omni: stored interaction id), persisted in operation.json

@dataclass(frozen=True)
class PollResult:
    done: bool
    video: Path | None     # downloaded raw video when done
    usage: Usage           # input/output/video_output tokens as reported, else derived (research V8)
    error: VideoError | None

class VideoError(Exception):
    code: Literal["provider_out_of_credit", "content_blocked", "invalid_picture",
                  "provider_unavailable", "video_failed"]

@runtime_checkable
class VideoGenerator(Protocol):
    name: str
    model: str
    def check(self) -> ProviderStatus: ...           # no paid calls
    def capabilities(self) -> Capabilities: ...
    def submit(self, req: VideoRequest) -> str: ...  # operation ref; raises VideoError
    def poll(self, ref: str, out_dir: Path) -> PollResult: ...
    def cancel(self, ref: str) -> None: ...          # best-effort
    def forget(self, ref: str) -> None: ...          # best-effort delete of the stored request (privacy, V6)

class VideoChain:
    providers: list[VideoGenerator]
    def submit(self, req: VideoRequest) -> Accepted:
        """Try providers in order; move on only when a provider raised before accepting. Raises the last
        provider's VideoError when none accepted."""
    def poll(self, accepted: Accepted, out_dir: Path) -> PollResult:
        """Route to the provider that accepted; never to another one."""
```

## Worker — changed behaviour

- Lanes: one lane per **enabled** tool with `tool.concurrency` slots; a slot claims the oldest queued
  job of its tool not already claimed (under the worker lock, `_current`), so N slots never start the
  same job.
- Startup: `JobStore.recover_after_restart(resumable=…)` fails waiting/running jobs only of tools with
  `resume_after_restart = False`. Running jobs of resumable tools are re-run first by their lane
  (`run` reads `operation.json` and resumes).
- Inputs-complete is `Worker.complete_inputs(job_id)`: calls `tool.accept` in a thread, then queues;
  used by both tus completion (and the startup re-fire) and `JobAccess.complete`.
- `TRANSITIONS[QUEUED]` gains `GENERATING`.

## Usage / pricing — added

`Usage.video_output_tokens: int = 0` (summed by `__add__`, stored in the job `usage` JSON);
price-table field `video_output` ($ per 1M tokens) used by `pricing.estimate`;
`DEFAULT_PRICES["gemini-omni-1.1-flash"] = {"input": 1.50, "output": 9.00, "video_output": 17.50}`.

## Shared download helper

`content_disposition(name)` moves from `tools/audio_extract/routes.py` to
`videokit.core.downloads` so both file-producing tools use one RFC 5987 implementation.
