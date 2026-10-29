# Internal Interface Contracts (backend)

These are the seams between `core` and tools (Constitution III) and between `core` and vendors
(Constitution IV). Signatures are indicative Python; exact types are settled in implementation.

## Tool

```python
class Tool(Protocol):
    id: str                      # "youtube_prep"
    name: str
    description: str
    router: APIRouter            # mounted at /api/tools/{id}
    async def run(self, job: JobHandle, ctx: CoreContext) -> None: ...
```

- `JobHandle.source_name` (read-only property, `str | None`) is the original upload file name as
  sanitized by core, or `None` when the client sent none. `Job.source_name` carries it in the
  `JobAccess.get` snapshot.
- Tools live in `videokit/tools/<id>/` and expose a module-level `TOOL`; the registry imports every
  subpackage of `videokit.tools` at startup.
- `run` is called by the worker after upload completes. It drives stages via `job.set_state(...)`
  and must call `job.check_cancelled()` at each stage boundary (raises `JobCancelled`).

## CoreContext (what tools may use)

```python
class CoreContext(Protocol):
    media: MediaService          # extract(job) -> MediaArtifacts (audio, frames, deletes source)
    transcriber: Transcriber     # the fallback chain, not a single provider
    llm: LLMClient               # the fallback chain
    settings: Settings
```

## JobAccess (what a tool's HTTP routes may use)

```python
class JobAccess(Protocol):
    ctx: CoreContext                             # same context the worker hands to `run`
    def get(self, job_id: str) -> Job | None: ...      # read-only snapshot: state, tool_id, providers, usage
    def handle(self, job_id: str) -> JobHandle: ...    # .dir, .record(...), .add_usage(...) on an existing job
    def create(self, tool_id: str, *, upload_length: int = 0, source_name: str | None = None) -> Job: ...
    def job_dir(self, job_id: str) -> Path: ...
    async def complete(self, job_id: str) -> None: ...  # inputs complete: tool.accept, then queue (idempotent)
    async def discard(self, job_id: str) -> None: ...   # drop a job still `uploading` (row and directory)

def job_access(request: Request) -> JobAccess: ...     # from `videokit.core.registry`
```

- For tool routes that act on an existing job outside the worker (e.g. YouTube Prep regenerate): look
  up the job (404 if unknown or of another tool, state checks), read and write files in its directory,
  record providers and add usage so the job's accumulated `usage` stays complete.
- Job creation belongs to the tools: a video tool's `POST /api/tools/<id>/jobs` calls
  `videokit.core.uploads.create_upload_job(request, tool_id, body)`, which creates the job through
  `JobAccess.create` (size limits: `413 file_too_large`, `422 invalid_request`) and returns the tus
  upload URL. The last tus byte reaches `JobAccess.complete`'s path (`Worker.complete_inputs`).
- Routes never touch the job store directly. `JobHandle` methods raise `JobNotFound` (re-exported by
  `videokit.core.registry`) if the job was purged meanwhile.

## Transcriber

```python
class Transcriber(Protocol):
    name: str
    def check(self) -> ProviderStatus: ...               # startup validation, no network calls that cost money
    def transcribe(self, audio: AudioFiles) -> Transcript: ...
    # Transcript = language, language_probability, segments[{start, end, text}], audio_minutes
```

Implementations: `LocalWhisperTranscriber`, `OpenAITranscriber`, `FakeTranscriber` (tests).
Chain: tries providers in configured order; raises `AllProvidersFailed(stage="transcription")`.

## LLMProvider

```python
class LLMProvider(Protocol):
    name: str                    # "anthropic" | "openai"
    model: str
    def check(self) -> ProviderStatus: ...
    def generate(self, req: GenerationRequest) -> GenerationResponse: ...

@dataclass
class GenerationRequest:
    system: str
    context_text: str                     # timestamped transcript lines "[m:ss] text"
    frames: list[LabeledFrame]            # (t_seconds, jpeg_bytes), labeled "[frame m:ss]"
    schema: dict                          # JSON schema of expected output
    turns: list[Turn]                     # follow-ups: instruction, prior output, repair feedback

@dataclass
class GenerationResponse:
    data: dict                            # parsed, schema-valid JSON
    usage: Usage                          # tokens incl. cache read/write
    provider: str                         # "anthropic:claude-opus-5-5"
```

- The stable prefix (system + context + frames) comes first so providers can cache it; `turns`
  vary per call (generate / repair / regenerate).
- Implementations: `AnthropicProvider`, `OpenAIProvider`, `FakeLLM` (tests; scripted responses).
- Chain: tries providers in order; SDK retries handle 429/5xx; on exhaustion or non-retryable
  error the next provider is tried; raises `AllProvidersFailed(stage="generation")`.
