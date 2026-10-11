# syntax=docker/dockerfile:1

# Stage 1: build the SPA.
FROM node:24-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: runtime. One image for CPU and GPU hosts: cuBLAS comes from pip (nvidia-cublas-cu12) and
# is registered with ldconfig; the host driver is injected by the NVIDIA container toolkit when a
# GPU is passed through. Without a GPU, faster-whisper simply runs on the CPU.
FROM python:3.14-slim

LABEL org.opencontainers.image.title="VideoKit" \
      org.opencontainers.image.description="Self-hosted video tools in one container, starting with YouTube Prep." \
      org.opencontainers.image.authors="Anton Krivenko" \
      org.opencontainers.image.licenses="MIT"

ENV DEBIAN_FRONTEND=noninteractive \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH=/app/.venv/bin:$PATH \
    DATA_DIR=/data \
    PORT=8000 \
    NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility

# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# uv is bind-mounted on the sync steps only, so it is not shipped in the image. The engine never
# loads nvrtc (research R2), so it is left out.
# Dependencies first so this layer is cached until the lockfile changes.
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=from=ghcr.io/astral-sh/uv:0.13,source=/uv,target=/usr/local/bin/uv \
    uv sync --locked --no-dev --no-install-package nvidia-cuda-nvrtc-cu12 --no-install-project

# Make the pip-installed CUDA libraries (cuBLAS) visible to the dynamic loader.
RUN find /app/.venv -path "*/nvidia/*/lib" -type d > /etc/ld.so.conf.d/nvidia.conf && ldconfig

COPY backend/src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=from=ghcr.io/astral-sh/uv:0.13,source=/uv,target=/usr/local/bin/uv \
    uv sync --locked --no-dev --no-install-package nvidia-cuda-nvrtc-cu12

COPY --from=frontend /build/dist ./src/videokit/static

# Fixed non-root user; /data is created owned by it so a fresh named volume is writable.
RUN useradd --uid 10001 --user-group --no-create-home --home-dir /app videokit \
    && mkdir -p /data \
    && chown videokit:videokit /data \
    && chmod -R a+rX /app

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
