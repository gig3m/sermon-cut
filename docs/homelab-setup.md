# docs/homelab-setup.md

One-time setup to run autopilot unattended on the homelab.

## 1. OAuth (non-expiring token)
The OAuth consent screen is published to production (done 2026-07-30), so refresh
tokens no longer expire at 7 days. Re-auth once on the homelab:
  rm token.json
  .venv/bin/python -c "from pathlib import Path; from sermon_cut.youtube_auth import get_authenticated_service; get_authenticated_service(Path('client_secrets.json'), Path('token.json'))"
Click through the "unverified app" warning. The resulting token.json persists.

## 2. Headless Claude under Max ($0)
Install Claude Code on the homelab and authenticate to the Max subscription with a
long-lived token:
  claude setup-token
Verify headless works: `claude -p "print ok"` returns without prompting.

## 3. Secrets
No email/notification secrets are required (Phase 2 dropped the email step —
see below). Any other per-stage secrets (YouTube OAuth, etc.) are covered by
the OAuth setup above.

## 4. Run by hand (before scheduling)
  .venv/bin/python -m sermon_cut auto discover latest
`auto discover latest` and `auto discover <video-url>` both work — `latest`
resolves to the channel's newest upload and is fetched by a real watch URL
under the hood. The command runs the discovery step, records the run's status
in `auto.db`, and exits — there is no email step. Confident weeks render and
upload Unlisted directly; unsure weeks stop and wait to be re-run once
reviewed. Check status with `status.get_run` (or a future web UI, which is the
eventual trigger for these commands) and, for an unsure week, resume with:
  .venv/bin/python -m sermon_cut auto render <video_id>

**Caveat — confident-path rendering is best-effort today.** The auto-render
path calls the same `clip` stage documented in CLAUDE.md's "Post-live clip
blocker" gotcha: on a real ~2-hour livestream, `--download-sections` +
`--force-keyframes-at-cuts` can hang or throttle to ~1x realtime. The reliable
workaround (full native download + local `-c copy` trim, wide front pad,
re-transcribe-the-clip to pin the sermon start) is a manual workflow, not yet
wired into the orchestrator — that's a follow-on. So a confident, unattended
`auto discover latest` run may stall on the clip stage for a real service; be
ready to kill it and fall back to running the CLI stages by hand
(`fetch-audio` → full download + trim → `intro`/`outro`/`assemble`/`upload`)
per the CLAUDE.md gotcha if it does.

**`auto shorts <video_id>` does not work yet.** It calls a `shorts-select`
subcommand that hasn't been built — that's also a follow-on. Don't run it
expecting output; use the existing `/sermon-shorts <run-id>` skill for shorts
in the meantime.

(The weekly cron, the web dashboard, the full-download-trim clip workaround,
and `shorts-select` are the follow-on plan.)

## 5. GPU transcription (RTX 5070)
faster-whisper runs on the box's GPU. Three pieces have to line up, and each
fails silently on its own:

- `sermon_cut/transcribe.py` auto-detects CUDA (`resolve_device`) and picks
  `int8_float16` on GPU, `int8` on CPU — so the same image still runs on a
  GPU-less host. Pin it with `SERMON_CUT_WHISPER_DEVICE` /
  `SERMON_CUT_WHISPER_COMPUTE` if the detection is ever wrong.
- The image pip-installs `nvidia-cublas-cu12` + `nvidia-cudnn-cu12` and puts
  their lib dirs on `LD_LIBRARY_PATH`. **CTranslate2 4.8 needs cuDNN 9 and the
  host only has cuDNN 8** — the libs must come from the wheels in the image, not
  from the host.
- `docker-compose.yml` requests `capabilities: [gpu, compute, utility]`.
  **`compute` is required**; `utility` alone yields NVML/monitoring but cannot
  run CUDA (that is the trap the glances container sits in by design).

Verify after any change here — a clean log or an HTTP 200 proves nothing:

    docker exec sermon-cut python -c \
      "import ctranslate2; print(ctranslate2.get_cuda_device_count())"   # -> 1

and during a real transcription `nvidia-smi` must show the process holding VRAM
(large-v3 int8_float16 ≈ 2.7 GB).

Measured on `runs/tyOe_INDIIk/sp.wav` (110s), faster-whisper 1.2.1 /
ctranslate2 4.8.2:

| model | device | compute | time | xRT |
|---|---|---|---|---|
| medium | CPU | int8 | 57.8s | 1.9x |
| medium | CUDA | float16 | 3.5s | 31.0x |
| large-v3 | CUDA | float16 | 7.5s | 14.8x |
| large-v3 | CUDA | int8_float16 | — | 21.5x |

Confirmed in the deployed container on the same 110s clip: **9.9s end to end**
(11.1x realtime, model load from the cache volume included), peak 2.3 GB VRAM
sampled at 0.5s, `resolve_device()` -> `('cuda', 'int8_float16')`.

`config.toml`'s `whisper_model` is `large-v3`: ~11x faster than the old CPU path
on a materially better model, and 2.7 GB leaves headroom on the 8 GB card, which
Plex shares for nvdec/nvenc. **Drop it back to `medium` on a CPU-only host.**

### Sharing the GPU with Ollama
The card is shared with Ollama (exodon's `gemma4:12b`, ~9 GB resident). With
`SERMON_CUT_OLLAMA_URL` set in the compose environment, transcription unloads
every Ollama model and waits (up to 120s) for `/api/ps` to empty before loading
whisper on CUDA. Without it, transcribe fails with `CUDA failed with error out of
memory`. Check: `docker exec sermon-cut printenv SERMON_CUT_OLLAMA_URL`.
