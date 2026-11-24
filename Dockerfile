FROM python:3.12-slim-bookworm

# System deps: ffmpeg (render), node (the Claude CLI), tzdata (church-local
# watcher schedule), ca-certs + curl + gnupg (nodesource install).
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg tzdata ca-certificates curl gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get update \
    && apt-get install -y nodejs \
    && node --version && npm --version \
    && rm -rf /var/lib/apt/lists/*

# Claude Code CLI — the judgment steps run headless under Max ($0).
# PINNED, and bump it deliberately: the host's ~/.claude/settings.json is
# mounted in, so its `model` must be one THIS CLI version accepts. An unpinned
# `npm i -g` caches in the layer and a rebuild silently keeps the old CLI —
# 2026-09-06 the container ran 2.1.247 against a model needing >= 2.1.251 and
# every `claude -p` exited 1 ("does not support this model"). Check drift with
# `docker exec sermon-cut claude --version` vs `claude --version` on the host.
ARG CLAUDE_CODE_VERSION=2.1.263
RUN npm i -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}

# Non-root user matching the host user (UID 1000) so files written into the
# mounts (runs/, auto.db, ~/.claude) stay owned by you, not root.
RUN useradd -m -u 1000 -s /bin/bash appuser
ENV HOME=/home/appuser

# An appuser-owned venv, first on PATH: the pipeline shells to PATH `yt-dlp`, so
# the freshened yt-dlp must be the one it finds — and the entrypoint's self-update
# must be writable without root.
RUN python -m venv /opt/venv && chown -R appuser:appuser /opt/venv
ENV PATH=/opt/venv/bin:${PATH}

WORKDIR /app
COPY . /app
RUN chown -R appuser:appuser /app

USER appuser
RUN pip install --no-cache-dir -U pip \
    && pip install --no-cache-dir -e ".[dashboard]" \
    && pip install --no-cache-dir -U --pre "yt-dlp[default]" \
    && pip install --no-cache-dir nvidia-cublas-cu12 nvidia-cudnn-cu12

# CUDA runtime libs for faster-whisper's CTranslate2 backend. CTranslate2 4.8
# needs cuDNN 9 and the host ships cuDNN 8, so these come from pip wheels INSIDE
# the image — never from the host. ctranslate2 dlopen()s them at load time, so
# their lib dirs must be on LD_LIBRARY_PATH or CUDA silently stays unavailable.
ENV NVIDIA_DRIVER_CAPABILITIES=compute,utility
ENV LD_LIBRARY_PATH=/opt/venv/lib/python3.12/site-packages/nvidia/cublas/lib:/opt/venv/lib/python3.12/site-packages/nvidia/cudnn/lib

EXPOSE 8890
ENTRYPOINT ["/app/docker/entrypoint.sh"]
