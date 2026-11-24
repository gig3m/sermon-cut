# Sermon-Cut Autopilot — Design

**Status:** approved design, pre-implementation
**Date:** 2026-07-30
**Branch:** `feat/autopilot` (git worktree at `~/projects/sermon-cut-autopilot`; `main` stays usable)

## Goal

Turn the interactive, single-laptop CLI pipeline into an **always-on autonomous
service on the homelab** that:

1. is **not tied to one laptop** — runs on an owned always-on Linux box,
2. is **hands-off** — wakes weekly, does discovery + judgment itself,
3. is **resilient** — long jobs survive restarts, retry, and report; no terminal
   babysitting,
4. is **drivable from anywhere** — a small private web dashboard to monitor,
   approve, and trigger.

**Hard constraint (unchanged):** no recurring cost beyond a Claude Max
subscription. The homelab is owned hardware; Claude runs under Max via a
long-lived token; Resend + Google APIs stay within free tiers.

## Non-goals

- Re-rendering already-uploaded videos to fix baked-in branding (thumbnails only,
  per prior decision).
- Public multi-user access, accounts, or a hosted SaaS.
- Replacing the existing `sermon_cut` CLI/skills — they are **reused**, not
  forked. `main` keeps working by hand throughout.

## Preserved invariants

- Privacy is always `unlisted`; `upload` is the only outbound action.
- Discovery is audio-only; full video download happens only in `clip`.
- Heavy deps stay lazily imported; tests run offline under pytest with injected
  runners/clients.

## Architecture

All processes run on the homelab. The heavy artifacts stay in `runs/<id>/`
exactly as today; a small SQLite DB indexes status.

```
            weekly cron ─┐        ┌─ "Run now" / Approve / Retry
                         ▼        ▼
                    ┌──────────────────┐        reads/writes
                    │   Dashboard app  │◄──────────────► auto.db (SQLite)
                    │  (FastAPI+HTML)  │                 (status index)
                    └────────┬─────────┘
                             │ enqueues job
                             ▼
                    ┌──────────────────┐   executes    ┌──────────────────┐
                    │  Orchestrator    │──────────────►│  Claude Code -p  │
                    │  (job runner)    │  headless      │  /sermon-auto    │
                    └────────┬─────────┘                └────────┬─────────┘
                             │                                   │ judgment
                             │ keys exec RESEND_API_KEY          ▼
                             ▼                          sermon_cut CLI stages
                    ┌──────────────────┐                (fetch/transcribe/
                    │   notify.py      │                 clip/intro/assemble/
                    │  (Resend email)  │                 upload/shorts/thumbs)
                    └──────────────────┘
```

### Components (each a small, independently testable unit)

- **`sermon_cut/auto/status.py`** — the `auto.db` SQLite store. One row per weekly
  run: `video_id, status, stage, plan_path, confidence_note, sermon_url,
  shorts_urls (json), error, created_at, updated_at`. Pure functions for
  transitions; unit-tested with a temp DB. Status enum:
  `discovering → needs_approval → rendering → ready_for_review → done`, plus
  `error` and `skipped`.
- **`sermon_cut/auto/confidence.py`** — the confidence gate. Given a drafted plan
  + validate-plan result + Claude's self-assessment, returns
  `(confident: bool, note: str)`. Confident requires: length IN_BAND **and**
  Claude's own boundary check passes (in-point on the preacher's greeting not a
  Scripture reading; out-point on the closing prayer/"Amen"; no reading bleed).
  Injectable so it is unit-tested offline.
- **`sermon_cut/auto/notify.py`** — `send_email(subject, html)` via Resend HTTP
  API. Reads `RESEND_API_KEY` from the environment; the process is launched under
  `keys exec RESEND_API_KEY -- …` so the secret never touches disk or logs.
  Swappable channel behind one function.
- **`sermon_cut/auto/orchestrator.py`** — the job runner invoked by cron and by
  the dashboard. Resolves the source, invokes headless Claude for the judgment
  steps, drives the CLI stages, writes status, sends notifications. Two entry
  jobs: `discover_decide(video_url)` and `render_publish(video_id)`.
- **`.claude/skills/sermon-auto/`** — a new **unattended** skill that Claude Code
  runs headless (`claude -p`). It is the discovery + judgment brain: read
  transcript → draft `plan.json` → self-assess → emit a machine-readable verdict
  (`confident` + note) the orchestrator consumes. It reuses the analysis logic of
  `/sermon-cut` but never prompts.
- **`dashboard/`** — a small FastAPI app + server-rendered HTML (no SPA). Private
  (behind existing homelab access — Tailscale / reverse proxy; not public). Reads
  `auto.db` + `runs/`; POST endpoints enqueue orchestrator jobs.

## The run flow (two Claude invocations — resilient, no long-lived pause)

**Run 1 — Discover & Decide** (auto, weekly, or "Run now"):
`fetch-audio → transcribe → Claude drafts plan.json → validate-plan → confidence
gate`.
- **Confident** → chains straight into Run 2.
- **Unsure** → status `needs_approval`, plan saved, **email fired** (Resend),
  dashboard shows it. Process exits (nothing paused).

**Run 2 — Render & Publish** (auto after a confident Run 1, or after dashboard
approval):
`clip (full-download+trim fallback) → intro → outro → assemble → upload Unlisted
→ chain /sermon-shorts + /livestream-thumbs` → status `ready_for_review`, **email**
with the Unlisted links.

**Approval loop:** `needs_approval` → user opens the dashboard from the email →
**Approve** (enqueue Run 2), **Edit** (adjust start/end/title/chapters inline then
approve), or **Skip** (e.g. 5th-Sunday singing service). Approval is a fresh
orchestrator job, not a resumed process.

**Failure handling:** every CLI stage is idempotent-by-artifact, so a
crashed/restarted job resumes at the first missing artifact. The status DB records
the last good stage + any error; the dashboard shows it with **Retry**; an
**error email** guarantees a stuck week is never silent.

## Dashboard

Private web app. Views:
- **Week list** — each service + status badge + timestamps.
- **Needs-approval** — `plan.json` rendered readably (start/end as `HH:MM:SS`,
  length, title, speaker, chapters), source-video link, Claude's confidence note.
  Controls: Approve / Edit / Skip.
- **Ready-for-review** — Unlisted sermon + Shorts YouTube links (review/publish
  there).
- **Actions** — "Run now" (URL or `latest`); per-run **Retry** on error.

## Notifications (Resend email)

- **Needs approval** — subject e.g. *"Sermon needs your OK — Aug 2"*, body with
  drafted title/boundaries/why-held + a deep link to the dashboard item.
- **Ready for review** — Unlisted links (sermon + Shorts).
- **Error / stuck** — so a silent failure never strands a week.
- Key via `keys exec RESEND_API_KEY -- …`; Resend free tier covers the volume.

## OAuth persistence (Phase 1 — DONE by owner 2026-07-30)

The 7-day refresh-token expiry was a consequence of **Testing** publishing status.
The OAuth consent screen has been **published to "In production"** (unverified is
fine for single-user; the "unverified app" warning is clicked through once). A
one-time re-auth on the homelab (delete `token.json`, run the flow) then yields a
**non-expiring** refresh token. Service accounts are not viable for personal
YouTube uploads; "test user" does not fix expiry. This retires the CLAUDE.md OAuth
caveat.

## Build strategy

- Isolated branch `feat/autopilot` in a git worktree at
  `~/projects/sermon-cut-autopilot`; `main` at `~/projects/sermon-cut` stays
  runnable by hand throughout. Additive code only — new `sermon_cut/auto/` package
  + `dashboard/` app; existing stages/skills/`assets/` reused. Merge to `main`
  when proven.
- Claude Code headless auth on the homelab under Max via a long-lived token
  (`claude setup-token`) — the mechanism that keeps this at $0 rather than
  API-metered. Validated in Phase 2 before building on it.

## Phasing (each phase independently useful)

1. **OAuth publish** — DONE; re-auth on homelab yields non-expiring `token.json`.
2. **Headless Claude on homelab** — `claude -p` under Max runs a trivial skill
   end-to-end. Proves the $0 driver.
3. **`/sermon-auto` skill + confidence gate + `status.py`** — unattended
   discover→decide→render flow, runnable by hand first, writing to `auto.db`.
4. **`notify.py`** — Resend via `keys exec`, wired to needs-approval / ready /
   error events.
5. **Dashboard** — read-only status view first, then Approve / Edit / Run-now /
   Retry actions.
6. **Scheduler** — weekly cron + "Run now"; full dry-run on a past service
   (`i0N9440hBOI`) before the first live Sunday.

## Testing

- Offline unit tests (pytest, injected fakes) for the confidence gate and every
  `status.py` transition.
- Dashboard actions tested against a seeded `auto.db`.
- End-to-end dry run on an already-processed week before the first live Sunday;
  privacy assertions guard that no path uploads non-`unlisted`.

## Open questions (resolve during writing-plans)

- Exact weekly cron time (Sunday evening, after the stream is posted).
- Dashboard framework confirm (FastAPI + Jinja assumed) and how it is fronted
  (Tailscale hostname vs reverse proxy).
- Whether Shorts run in the same Run 2 or as a separately-queued follow-on job.
