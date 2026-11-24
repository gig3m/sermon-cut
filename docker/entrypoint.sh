#!/usr/bin/env bash
set -euo pipefail

# yt-dlp rots against YouTube — freshen to the latest nightly on every start.
# This installs into the appuser-owned venv that is first on PATH, so the
# pipeline's PATH `yt-dlp` calls pick it up. Best-effort: a network hiccup here
# must never stop the dashboard from serving.
pip install --no-cache-dir -U --pre "yt-dlp[default]" 2>&1 | tail -1 \
    || echo "yt-dlp self-update skipped (offline?)"

exec python -m dashboard.server
