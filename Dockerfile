# syntax=docker/dockerfile:1

# Stage 1: build the SPA.
FROM node:24-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: runtime. The CUDA/cuDNN runtime lets faster-whisper use a GPU when one is passed
# through; the same image runs on CPU-only hosts.
FROM nvidia/cuda:12.9.1-cudnn-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive \
    UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH=/app/.venv/bin:$PATH \
    DATA_DIR=/data \
    PORT=8000

# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv

WORKDIR /app

# Dependencies first so this layer is cached until the lockfile changes.
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv python install 3.13 \
    && uv sync --locked --no-dev --no-install-project

COPY backend/src ./src
RUN uv sync --locked --no-dev

COPY --from=frontend /build/dist ./src/videokit/static

# Fixed non-root user; /data is created owned by it so a fresh named volume is writable.
RUN useradd --uid 10001 --user-group --no-create-home --home-dir /app videokit \
    && mkdir -p /data \
    && chown videokit:videokit /data \
    && chmod -R a+rX /app /opt/python

# The user's home (/app) is read-only, so runtime caches (Hugging Face hub/xet, XDG) live in the /data
# volume. Set only here, after the build steps, so build-time tools (uv) do not write into /data.
ENV HF_HOME=/data/huggingface \
    XDG_CACHE_HOME=/data/cache

USER 10001:10001
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/api/health', timeout=4)"]

CMD ["sh", "-c", "exec uvicorn --factory videokit.main:app_from_env --host 0.0.0.0 --port \"${PORT}\""]
