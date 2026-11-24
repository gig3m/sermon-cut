# Sermon-Cut Autopilot — Web-UI / Homelab Control Surface — Design

**Status:** approved design, pre-implementation
**Date:** 2026-08-25
**Branch:** `feat/autopilot` (git worktree at `~/Projects/sermon-cut-autopilot`)
**Parent spec:** `2026-07-30-autopilot-design.md` (this supersedes its deferred
Phases 5–6 and reshapes the deployment).

## Goal

Turn the weekly sermon pipeline into a **remote-control, homelab-run** service:
a small web UI on the Docker host (behind nginx-proxy-manager) where
Kyle, from his phone, sees newly-detected Sunday services, sets speaker + options,
and hits **Process** — and the box runs the whole pipeline (livestream thumbnail
+ sermon cut + Shorts) **straight through to Unlisted**, at the same quality as
the interactive weekly runs, showing progress and the review links in the UI.

**Not** blind cron autonomy: a cron only *populates* the queue; a human triggers
each run and front-loads the judgment (speaker, optional title). Claude stays in
the loop at the genuine judgment points, running headless on the box.

**Hard constraint (unchanged):** $0 beyond a Claude Max subscription. Owned
hardware; headless `claude -p` under Max; no paid/recurring dependency.

## Non-goals

- Blind weekly cron that processes with no human trigger.
- An approval gate before rendering (straight-to-Unlisted; the Unlisted upload is
  the only review point).
- Email notifications (the web UI replaces the Resend/notify path entirely).
- Public/multi-user access or app-level accounts (private behind NPM/Tailscale).
- Re-rendering already-uploaded videos.

## Preserved invariants

- Privacy is always `unlisted`; `upload` is the only outbound action.
- Discovery is audio-only; full-video download happens only in the render step.
- Heavy deps (faster_whisper, googleapiclient, PIL, cv2, numpy) and binaries
  (yt-dlp, ffmpeg) are never imported at module top-level; tests run fully
  offline under pytest with injected runners/clients.
- New code is additive under `sermon_cut/auto/` + a new `dashboard/` app; existing
  stages/skills/`assets/` are reused, not forked.

## Architecture / deployment

One Docker compose stack at `~/docker/sermon-cut/` on the Docker host, fronted by the
existing **nginx-proxy-manager** (proxy host → container port, TLS + hostname).
A single long-lived container runs, in one process group:

- the **FastAPI dashboard** (web UI),
- the **discovery-watcher** (scheduled channel scan that populates the queue),
- the **job runner** (executes processing jobs).

```
        nginx-proxy-manager (TLS, hostname)
                    │
                    ▼
        ┌───────────────────────────────┐
        │  container: sermon-cut          │
        │                                 │
        │  FastAPI dashboard ─┐           │
        │  discovery-watcher ─┼─► auto.db │◄── status/queue
        │  job runner ────────┘   (vol)   │
        │        │                        │
        │        ├─ sermon_cut CLI stages │
        │        └─ claude -p (judgment)  │
        └───────────────────────────────┘
          mounts: ~/.claude (ro), token.json, client_secrets.json,
                  config.toml, assets/; named vols: runs/, whisper-cache, auto.db
```

**Image** carries: Python 3.12 + the `sermon_cut` package and deps, `ffmpeg`,
**yt-dlp nightly** (self-updates on container start — it rots against YouTube),
and the **Claude Code CLI**.

**Volumes (persist across redeploys):**
- Mounted gitignored runtime files: `token.json`, `client_secrets.json`,
  `config.toml`, `assets/` (branded PNGs + vendored fonts).
- `~/.claude` mounted **read-only** so headless `claude -p` runs under Max ($0).
- Named volumes: `runs/` (artifacts), faster-whisper **model cache** (else it
  re-downloads each run), and `auto.db`.

**Primary risk (validated first, Phase 0):** headless `claude -p` under Max
*inside a container* with mounted `~/.claude`. Proven natively on the Docker host;
unproven in-container. Phase 0 is a throwaway spike — build the image, mount the
creds, confirm `claude -p "say ok"` returns without prompting. If it can't be
made to work, the model needs rethinking, so it's the day-one go/no-go.

## Processing flow (what "Process" runs)

Triggered with `(video_id, speaker, title_override|null, options)`; the job runner
executes on the box, writing `stage`/`status` to `auto.db` after each step for
live UI progress. Order:

1. **Livestream thumbnail** — render the dated ink card, force-set it (bypasses
   the generic-card detector blindspot). Skippable via option.
2. **Discovery** — `fetch-audio` (audio-only) → `transcribe`.
3. **Find the sermon** — *Claude judgment (`claude -p`, reuses `/sermon-auto`)*:
   reads the transcript, returns rough `start`/`end`, the exact **in/out phrases**
   (preacher's greeting after the pre-sermon reading; closing-prayer "Amen"),
   chapters, and a self-assessment note.
4. **Drift-pin** — *deterministic Python*: probe short windows around the rough
   boundaries, `transcribe_words`, locate Claude's named greeting/"Amen" phrases,
   set frame-accurate `start`/`end`. Removes non-uniform Whisper drift (observed
   14–31s misses) with no human. Sets `plan.start`/`plan.end`.
5. **Title** — the form field if provided; else *Claude judgment (`claude -p`
   vision)*: extract a frame at the sermon start, read the church's on-screen
   slide title (e.g. "Remain in Me").
6. **Render** — *deterministic, robust*: yt-dlp nightly **full-download → mux with
   audio → frame-accurate `-ss/-t` cut at 30fps** (never the hang-prone windowed
   `clip`); re-transcribe the cut to **re-pin chapters** drift-free; build intro +
   outro; assemble with dissolves/fades.
7. **Upload sermon** — Unlisted, dated title, title-card thumbnail; write
   `upload.json` receipt.
8. **Shorts** — *Claude judgment (pick ≤3 standalone moments) → deterministic
   render*: `shorts-clip` / `shorts-render` / `shorts-upload`, each Unlisted with
   the full-sermon link. Skippable via option.

**Straight to Unlisted — no approval gate.** Every stage is idempotent-by-
artifact; on failure the runner sets `status=error` + message, and the UI's
**Retry** resumes at the first missing artifact. The step-3 self-assessment is
**logged as a note** on the row (a shaky boundary is visible for YouTube review)
but never blocks.

**Division of labor:** Python owns the mechanics (fetch/transcribe/drift-pin/
render/upload); Claude is called only at three judgment points — find sermon,
read title, pick Shorts.

## Web UI & discovery watcher

**Discovery watcher** — scheduled inside the container (e.g. Sunday midday plus a
couple re-checks): scans the channel `/streams` tab (reusing the existing
stream-scan + `is_sunday_service`), inserts Sunday services not yet in `auto.db`
as `discovered`. Populates only; never processes. Idempotent.

**Web UI** — FastAPI + server-rendered HTML (no SPA), private behind NPM. Views:
- **Queue** — services newest-first, each a status badge
  (`discovered → processing (stage) → done | error`); columns date, title (once
  known), status, links when done; a **Rescan now** button.
- **Process form** (from a `discovered` row) — **Speaker** (defaults to config
  `default_speaker`, editable for guests), **Title** (optional; blank ⇒
  read-slide-via-vision), **options** checkboxes: *skip thumbnail*, *skip Shorts*,
  *force re-run* (ignore existing artifacts). **Process** button.
- **Detail** — live progress (current stage), the confidence note, the finished
  **Unlisted links** (sermon + each Short), and on failure the error + **Retry**.

**Actions:** Process, Retry, Rescan — the whole surface, deliberately small.

**Auth:** none in-app; private behind NPM/Tailscale (trust the network). A
shared-password gate is a possible later add, intentionally omitted now.

**Trigger mechanics:** Process/Retry enqueue a job row in `auto.db`; the runner
picks it up and executes the processing flow. The web layer never blocks on work —
it reads status and refreshes (short poll or manual). Web writes intent, runner
does work, both communicate only through the DB.

## Core reuse & gaps closed

**Reused from the built `sermon_cut/auto/`:**
- `status.py` (`auto.db`) — kept; schema extended with `speaker`,
  `title_override`, `options`, and the `discovered` queue state.
- `orchestrator.py` — kept as the spine; `render_publish` gets the robust render
  port, `make_shorts` gets a real selection step, plus a livestream-thumb step
  and speaker/title/options threading; the `needs_approval` branch is removed.
- `confidence.py` — demoted to advisory (logged note, not a gate).
- `/sermon-auto` skill — reused for find-sermon; two small sibling judgment calls
  added: read-title-via-vision, pick-Shorts.
- `notify.py` — dropped from the flow (no email; UI is the surface).

**Deferred gaps now closed:** render-robustness (full-download-trim + drift-pin as
tested code), `shorts-select` (the selection judgment call feeding the existing
shorts render stages), error-status + Retry (replacing the failure email), and the
notify-config gap (moot).

## Testing

All offline (pytest, injected fakes — the established pattern):
- Drift-pin helper: synthetic word-transcripts → correct frame-accurate
  `start`/`end`.
- Discovery watcher: injected stream-enumerate → correct `discovered` rows;
  idempotent re-scan.
- Orchestrator flow: injected runner/judge → correct stage/status transitions,
  straight-to-Unlisted, error+retry.
- Web UI: routes against a seeded `auto.db`.
- Then a full **dry-run on a past service** in the container before first live use.

## Phasing (each independently useful; Phase 0 is go/no-go)

0. **Spike:** headless `claude -p` under Max inside the container with mounted
   `~/.claude`. Go/no-go before building on it.
1. **Render-robustness + `shorts-select`** — port the hand-run download/drift/
   chapter/render steps into tested code, and the Shorts selection call. Also
   hardens the interactive weekly runs — payoff before the UI exists.
2. **Orchestrator wiring** — speaker/title/options, title-via-vision, livestream-
   thumb step, straight-to-Unlisted, error status; the discovery watcher.
3. **Web UI** — queue → form → detail; Process / Retry / Rescan.
4. **Docker packaging + NPM** — compose stack, volumes, in-container yt-dlp
   self-update, proxy host; the end-to-end dry run.

## Open questions (resolve during writing-plans)

- Exact discovery-watcher schedule (Sunday midday + N re-checks) and hostname for
  the NPM proxy host.
- Web framework confirm (FastAPI + Jinja assumed) and poll interval.
- Whether the job runner is a thread in the FastAPI process or a separate
  in-container worker loop (leaning: a background worker loop polling `auto.db`,
  so a long render never ties up a web worker).
