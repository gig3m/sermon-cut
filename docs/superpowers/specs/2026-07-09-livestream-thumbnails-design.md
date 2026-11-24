# Livestream thumbnails (one-off) — design

**Date:** 2026-07-09
**Status:** Approved for implementation
**Type:** One-off / throwaway batch job (NOT a maintained pipeline feature)

## Goal

Give the church channel's back-catalog of full Sunday livestreams a branded
title-card thumbnail instead of YouTube's auto-picked frame. Reuse the sermon
title-slide format, but on the new **ink** (charcoal) background instead of the
green one, and with a single line of text — the video's own date + "Livestream"
— in place of the sermon card's speaker / date / title block.

Example card text: `July 5th, 2026 Livestream`.

## Scope

- **Target videos:** all **2026** streams from the channel's `/streams` tab —
  Jan 4, 2026 through the latest (~22 videos). Every 2026 stream happens to be a
  "Sunday Morning Class & Worship" service, so no type filter is needed beyond
  "year == 2026". Older years (2023–2025) and non-Sunday classes are out of
  scope for this pass.
- **Not in scope:** the extracted sermon videos (already handled by
  `sermon-cut`), Shorts, and any maintained/weekly automation. This is a manual
  catch-up job run once.

## Non-goals / invariants preserved

- No new code in the tested `sermon_cut/` package; no new CLI stage. The job is
  a self-contained script under `experiments/livestream-thumbs/` (untracked,
  like the existing `experiments/shorts/` spike).
- Reuses the sermon card's drawing code by **import**, so "same format" is true
  by construction rather than by copy.
- No re-auth and no new scope: the existing `token.json` already carries
  `youtube.upload`, which authorizes `thumbnails.set`. The full 2hr videos are
  never downloaded — enumeration is metadata-only via yt-dlp.
- Privacy of the target videos is untouched; setting a thumbnail does not change
  visibility. The action is reversible (re-set or clear the thumbnail).

## The card

Background: the ink card pulled via `drip` (`intro_bg_ink.png`), saved to
`assets/intro_bg_ink.png` (gitignored, same as `assets/intro_bg.png`). It is the
green background's twin — 1920×1080, gold logo + "SOUTHSIDE CHURCH" wordmark in
the upper third, an empty clear zone below.

Rendering reuses `sermon_cut.title_card` helpers (`fit_and_wrap`, `_measurer`,
`_load_font`, `_draw_tracked`, and the layout constants `_SIDE_MARGIN`,
`_BLOCK_TOP`, `_TITLE_MAX/_MIN`, `_TITLE_LEADING`, `_GOLD`, `_WHITE`,
`_RULE_OFFSET`, `_RULE_W`, `_EYEBROW_*`, `_META_GAP`, `_BOTTOM_MARGIN`):

- **One text line only** — no eyebrow. The line sits where the sermon *title*'s
  first line sits, so the two card types line up: vertical anchor
  `_BLOCK_TOP + eyebrow_h + _META_GAP` (the same y the sermon title starts at),
  where `eyebrow_h` is computed exactly as in `build_title_card` so a
  livestream card and a sermon card share the same title baseline.
- Lora **Bold**, white, sized by the existing `fit_and_wrap` sweep
  (`_TITLE_MAX`→`_TITLE_MIN`) within the usable width; wraps to a second line
  only if it ever overflows (the expected strings are short and stay one line).
- The gold vertical accent rule to the left of the block, spanning the rendered
  line(s), same as the sermon card.
- Output: 1920×1080 PNG, well under YouTube's 2 MB thumbnail limit (the base ink
  PNG is ~0.6 MB).

## Date text

Parsed from each video's **title**, not its upload date — the title date is the
service date we want on the card.

- Regex extracts `Month Day, Year` tolerant of: abbreviated months
  (`Jan`/`Feb`/…), an ordinal already in the title (`February 14th`), extra
  spaces (`April 4,  2026`), and a missing space after the dash (`-April 20`).
  `Match` is normalized to `March` defensively (a real typo in the catalog),
  though 2026 titles are clean.
- Reformats to `"<Month> <D><ordinal>, <YYYY> Livestream"` with an English
  ordinal suffix (`1st`, `2nd`, `3rd`, `4th`, … `21st`, …).
- Any title that does not parse to a valid 2026 date is **skipped and printed in
  a report line** — never guessed, never rendered with a wrong date.

## Pipeline

1. **Enumerate** — `yt-dlp --flat-playlist --dump-json <channel>/streams`,
   keep entries whose parsed title year is 2026. Produce `(video_id, title,
   date_text)` rows.
2. **Render** — one PNG per row → `experiments/livestream-thumbs/out/<id>.png`.
   Idempotent: skip if the PNG already exists.
3. **Set thumbnail** — `youtube.thumbnails().set(videoId, media_body=PNG)` using
   `get_authenticated_service(client_secrets.json, token.json)`. Cost ≈ 50 units
   × 22 ≈ 1,100 of the 10,000/day quota. Records `{video_id, date_text, ok}` per
   video.

The script exposes flags so rollout is staged:
`--render-only` (steps 1–2, no upload), `--ids <a,b>` (restrict to specific
videos), `--limit N`. Default run renders + sets all matched 2026 videos.

## Rollout (safety)

This mutates **public** videos, so it ships in two stages:

1. **Pilot** — render + set the 2 most recent (July 5 and June 28, 2026).
   Kyle eyeballs the live thumbnails on YouTube.
2. **Batch** — on approval, set the remaining ~20.

Every PNG is kept on disk; re-running `set` is idempotent and any thumbnail can
be re-set or cleared, so the operation is fully reversible.

## Verification

- Before upload: open a rendered pilot PNG and confirm it opens on the ink
  card with the correct ordinal date, gold rule, and title-aligned baseline.
- After upload: confirm YouTube shows the new custom thumbnail on the pilot
  videos (thumbnail propagation can lag a minute or two).
- Report a final table: video id, date text, render OK, set OK; and an explicit
  list of any skipped/unparsed titles.

## Risks / open notes

- **Thumbnail propagation lag** on YouTube's CDN — the pilot check may need a
  minute. Not an error.
- **Phone-verified channel required** for custom thumbnails; the channel already
  uses custom thumbnails on sermon uploads (`upload` sets `title_card.png`), so
  this is already satisfied.
- **`drip` TTL** — the ink file's drip URL expires ~24h after upload; it's copied
  into `assets/` immediately so the job never depends on the URL staying live.
