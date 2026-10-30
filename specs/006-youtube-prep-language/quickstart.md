# Quickstart: YouTube Prep Output Language

How to check `spec.md` end to end. The interfaces are in `contracts/`.

## 1. Default suites (no network)

```bash
task lint && task test && task e2e
```

Expected result: every suite is green, with coverage of at least 80 % per stack. The suites cover:

- **Job creation:** a valid or missing `language` creates the job and writes `language.json`. A
  bad `language` gets `422 invalid_language` and creates no job. `fallback_language` is normalized.
- **Output language:** the resolution table for every case (picked, auto with speech, auto
  without speech, missing file).
- **Prompt caching:** generate, repair and regenerate send a byte-identical prefix.
- **Regenerate:** keeps `result.language`.
- **Options:** the endpoint lists the codes.
- **Picker:** the choice is remembered and restored, and falls back when storage throws or a code
  is unknown.
- **Job page:** shows the output language and names it in the no-speech notice.
- **Mobile:** the 360 px and touch-target checks pass with the picker on the page.

## 2. Manual check in the running app

Run `task docker:build && task docker:run` with a working transcription and LLM setup.

1. Open YouTube Prep and pick a language other than English. Upload a short silent clip. Every
   section should be in that language, and the result page should show the language's own name,
   in the summary line and in the no-speech notice.
2. Reload the page. The same language should still be selected.
3. Regenerate the title. It should come back in the same language.
4. Switch to "Same as the speech" and upload a clip with speech. The output should follow the
   speech, as in the previous release.
5. On "Same as the speech", with the browser's language set to a supported non-English language,
   upload a silent clip. The output should be in the browser's language.
