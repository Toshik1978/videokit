# Contract: Container Image and Compose File

## Image `ghcr.io/<owner>/<repo>` (local builds: `videokit:local`, CI: `videokit:ci`)

Unchanged from feature 001 (see `specs/001-videokit-youtube-prep/contracts/configuration.md`):
environment variables, port 8000, `/data` volume, default user 10001, health check, read-only
`/app`, caches under `/data`.

Changed or added:

| Item | Value |
| --- | --- |
| base | `python:3.14-slim` (Debian 13); no `nvidia/cuda` base |
| platforms | `linux/amd64` |
| GPU | `NVIDIA_VISIBLE_DEVICES=all`, `NVIDIA_DRIVER_CAPABILITIES=compute,utility`; GPU libraries from the pinned `nvidia-cublas-cu12` package, registered with `ldconfig` |
| ffmpeg | Debian package |
| size | compressed ≤ 1.25 GB |
| labels | `org.opencontainers.image.title=VideoKit`, `.description`, `.authors=Anton Krivenko`, `.licenses=MIT`; `.source`, `.version`, `.revision`, `.created` added by the release |
| tags | `X.Y.Z` and `latest` per release; no other tags |
| visibility | public (pullable without login) |

## `compose.yaml`

| Key | Value |
| --- | --- |
| `image` | `ghcr.io/<owner>/<repo>:latest` (the real owner and repository name) |
| `build` | removed (local builds use `task docker:build` / `task docker:run`) |
| `user` | `${PUID:-1000}:${PGID:-1000}` (unchanged) |
| `volumes` | `./.data:/data` (unchanged) |
| `deploy.resources.reservations.devices` | enabled: `driver: nvidia`, `count: all`, `capabilities: [gpu]`, with a comment to delete the block on hosts without the NVIDIA container toolkit |
