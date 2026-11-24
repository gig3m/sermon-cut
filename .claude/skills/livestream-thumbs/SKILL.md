---
name: livestream-thumbs
description: Use when the user wants branded thumbnails on the church channel's full Sunday livestreams (the /streams tab), or wants to find/backfill livestreams not yet wearing the dated card (auto-picked frame OR the generic stream-time placeholder). Scans the channel, renders an ink "<date> Livestream" title card for each Sunday service lacking one, and sets it Unlisted-safe — runs unattended.
---

# Livestream Thumbnails

Put a branded **ink title card** (`"<date> Livestream"`) on the church channel's
full **Sunday-service livestreams** that still show YouTube's auto-picked frame.
Runs from the repo root. This is the whole 2hr livestreams on the
`/streams` tab — NOT the extracted sermons (those are `/sermon-cut`).

Runs **unattended**: the CLI finds the Sunday streams lacking a card, renders the
dated card, and sets it. Setting a thumbnail never changes a video's visibility
and is fully reversible; only videos currently on an auto frame are touched (a
video already carrying a card is detected and skipped, never overwritten).

## How it decides "no thumbnail"

There is no API flag for custom-vs-auto thumbnails. Instead it renders the card
it WOULD set for the stream and compares the live public thumbnail against it
by image distance (64×36 grayscale, mean abs diff; threshold 1.0). Measured:
the same card after YouTube's re-encode 0.08, a card with a different date 2.8,
the generic dark "Livestream" placeholder the church sets at stream time 8.3, an
auto video frame 74.7. 🚨 Do not go back to a "dark + grayscale == branded"
brightness heuristic: the stream-time placeholder is also dark and grayscale,
and that rule skipped a real stream on 2026-09-06. A candidate means "not wearing
OUR dated card", not "no custom thumbnail".

## Setup check (first run only)

Confirm these exist in the repo root:
- `.venv`, `config.toml` (supplies `youtube_channel_id`)
- `assets/intro_bg_ink.png` (the ink background) and `assets/fonts/`
- `client_secrets.json` + `token.json` (the existing `youtube.upload` token
  already authorizes `thumbnails.set` — no re-auth)

Always invoke the CLI as `.venv/bin/python -m sermon_cut ...`.

## Steps

1. **Scan first (dry run).** Run:
   `.venv/bin/python -m sermon_cut livestream-thumbs --dry-run`
   It prints each Sunday stream it detected as still on an auto frame, and
   renders its card into `runs/livestream-thumbs/<id>.png` without setting
   anything.

2. **Sanity-check the count.** A normal weekly run finds **1** (the newest
   Sunday). A first run over the back-catalog may find **~100+** — that's
   expected; do not treat it as an error. If the count looks wrong (e.g. it lists
   a stream you know already has a card, or a Wednesday class slipped in), stop
   and report before setting. Optionally open a rendered PNG to eyeball the card.

3. **Set them.** Run (no flags = find → make → set, unattended):
   `.venv/bin/python -m sermon_cut livestream-thumbs`
   Each row prints `render=ok set=ok`. Re-runs are idempotent (cached PNGs;
   already-carded videos are skipped).

4. **Report.** Give the final `N/M set` line and the per-video table. Note that
   YouTube's public watch page can lag a minute or two on thumbnail propagation
   (Studio → Content reflects it first). Each `set=X` row prints its reason.

## Rate limit on big backfills (expected)

YouTube caps `thumbnails.set` at roughly **~100 per day** per channel, returning
HTTP 429 ("uploaded too many thumbnails recently"). This is a **daily** limit,
not a short rolling window — once hit, it stays blocked for ~24h (observed:
retrying every 25 min for 4 hours set 0 more). On a first back-catalog run (~100+
Sundays), expect it to set ~100, then stop and report `NOTE: hit YouTube's
thumbnail rate limit — N still need a card`. This is normal, not a bug: the run
short-circuits instead of firing dozens of doomed calls. **To finish: re-run
`livestream-thumbs` the NEXT DAY** (not minutes later — the limit is daily).
Re-runs are idempotent — already-carded videos are detected and skipped, so each
day's run drains up to ~100 more. Weekly steady-state (1 new Sunday) never hits
the limit.

## Flags

- `--dry-run` — scan + render, never set.
- `--ids A,B` — restrict to specific video ids.
- `--limit N` — cap how many to set (e.g. `--limit 1` for just the newest).

## Scope

Sunday worship services only (title contains "Sunday" + "Worship"), any year.
Wednesday/Friday/marriage classes are excluded by design. The card text is the
service date parsed from the title, formatted `"<Month> <D><ordinal>, <YYYY>
Livestream"` (e.g. `July 5th, 2026 Livestream`); a title whose date can't be
parsed is skipped, not guessed.
