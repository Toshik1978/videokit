# Internal Interfaces: YouTube Prep Output Language (delta)

All changes are inside `videokit/tools/youtube_prep/` and the frontend. Core is unchanged (Principle
III).

## `tools/youtube_prep/languages.py` (new)

```python
LANGUAGES: dict[str, str]  # ISO 639-1 code -> English name, the 61 entries of research L2
AUTO = "auto"
LANGUAGE_JSON = "language.json"

@dataclass(frozen=True)
class LanguageSetting:
    choice: str = AUTO      # AUTO or a key of LANGUAGES
    fallback: str = "en"    # a key of LANGUAGES

def normalize_fallback(value: str | None) -> str: ...   # base subtag, lower-cased; unsupported/None -> "en"
def write_setting(job_dir: Path, setting: LanguageSetting) -> None: ...  # atomic write
def read_setting(job_dir: Path) -> LanguageSetting: ...  # missing file -> LanguageSetting()
def instruction(code: str) -> str: ...
#   "Write all output in <English name> (language code xx)."; a detected code outside LANGUAGES uses the code as name
```

## `tools/youtube_prep/routes.py`

- `CreateJobRequest(uploads.CreateUploadRequest)` adds `language: str = "auto"` and
  `fallback_language: str | None = Field(None, max_length=35)`.
- `create_job` validates `language` before creating anything (`AppError("invalid_language", …,
  422)`), calls `uploads.create_upload_job`, then `write_setting` in a worker thread. If the write
  fails it calls `JobAccess.discard` and re-raises.
- `GET /options` returns `{"languages": sorted(LANGUAGES)}`.

## `tools/youtube_prep/pipeline.py`

- `output_language(setting, media) -> str` (data-model.md table) replaces `output_language(media)`.
  `DEFAULT_LANGUAGE` and `ENGLISH_INSTRUCTION` are removed.
- `turn_notes(media, language)` builds the duration note plus `languages.instruction(language)`. It
  is used for generate, repair and regenerate turns only, never the prefix.
- `run` reads the setting once, resolves the language and passes it to `_generate` and
  `build_result`. `build_result(normalized, media, language)` sets `result.language = language`.
- `regenerate` uses `current.language` as the job's language.
- `_transcribe` keeps recording the detected language (`en` without speech) in `media.json` and on
  the job row, as before.

## Frontend

- `components/UploadPanel.vue`: an optional prop `createFields?: Record<string, unknown>`, merged
  into the create-job body.
- `tools/youtube-prep/language.ts` (new):
  - `endonym(code)` uses `Intl.DisplayNames` and falls back to the code.
  - `loadChoice(supported)` and `saveChoice(value)` use `localStorage`, wrapped in try/catch.
  - `browserLanguage()` returns the lower-cased base subtag of `navigator.language` (`pt-BR` -> `pt`), or `undefined` when the browser reports none.
- `tools/youtube-prep/YouTubePrepPage.vue`: fetches `/options`, shows a labelled `<select>`
  ("Same as the speech" first, then endonyms sorted), saves on change, and passes
  `{language, fallback_language}` as `createFields`.
- `tools/youtube-prep/JobPage.vue`: shows `Output language: {name}`; the `no_speech` notice gets
  `{language}`.
- `i18n/en.json`: new keys for the picker label, the "Same as the speech" option, the output-language
  line and `errors.invalid_language`. The `no_speech` text takes `{language}`.
