# Configuration Contract: Video Generation (delta)

Base contract: `specs/001-videokit-youtube-prep/contracts/configuration.md`. Environment variables
only; documented in `.env.example` (placeholders) and the README.

| Variable | Default | Meaning |
|----------|---------|---------|
| `VIDEO_PROVIDERS` | *(empty)* | Ordered, comma-separated video generation providers. Allowed: `omni`. Empty → the Video Generation tool is hidden (not mounted, not listed, no lane). |
| `GEMINI_API_KEY` | — | Google AI Studio API key (paid tier). Required when `omni` is listed. |
| `OMNI_MODEL` | `gemini-omni-1.1-flash` | Omni model id sent to the Gemini API. |

Startup validation (one `Configuration error:` line each, values never printed):

- `VIDEO_PROVIDERS contains an unknown provider (allowed: omni)`
- `GEMINI_API_KEY is required when 'omni' is listed in VIDEO_PROVIDERS`

`PRICING_OVERRIDES` accepts the new `video_output` field, e.g.
`{"gemini-omni-1.1-flash": {"video_output": 17.5}}`.

Operator notes (README): the key must belong to a project with billing enabled (prepaid credit);
Google keeps stored requests for the AI Studio retention setting (55 days by default on the paid
tier) — VideoKit deletes each one after downloading its clip; Tier 1 projects have a rolling $10
per 10 minutes spend cap.
