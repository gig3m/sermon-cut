# `/livestream-thumbs` skill — design

**Date:** 2026-07-09
**Status:** Approved for implementation
**Supersedes:** the one-off `experiments/livestream-thumbs/` job (2026-07-09
livestream-thumbnails spec/plan). This productionizes that work into the tested
package + a skill.

## Goal

A repeatable skill, `/livestream-thumbs`, that scans the church channel for
**Sunday-service livestreams that have no branded thumbnail**, renders the ink
`"<date> Livestream"` title card for each, and sets it — fully unattended. Run it
weekly (catches the newest stream) or after a gap (catches several). The
back-catalog gets done on the first run; steady state is "the one new Sunday."

## Decisions (from brainstorming)

- **Detection = image heuristic.** YouTube exposes no custom-vs-auto flag, but the
  branded card and an auto video-frame separate cleanly on the *primary*
  thumbnail: card ≈ brightness 35 / saturation 0.8; auto frame ≈ 120 / 51. A
  stream "has no thumbnail" when its primary thumbnail is NOT a dark, near-gray
  card. Stateless, needs no auth, and can never overwrite an existing card.
- **Scope = Sunday services only.** Titles matching the Sunday worship service
  ("Sunday" + "Worship"); Wednesday/Friday/marriage classes are excluded. Any
  year.
- **Unattended, auto-set all.** Even the first ~100+ back-catalog pass sets
  without a gate; the reversible set + the channel is the review point. Quota is
  ample (~50 units/set, 10k/day).
- **Productionized into `sermon_cut/`** with unit tests around the pure decision
  logic, matching `/sermon-cut` and `/sermon-shorts` (skill drives a CLI; heavy
  deps injected or lazy-imported).

## Invariants preserved

- **No re-auth, no new scope** — existing `token.json` (`youtube.upload`) covers
  `thumbnails.set`. Discovery (yt-dlp) and thumbnail fetch (HTTP GET of a public
  image) need no auth.
- **No full-video download** — enumeration is `yt-dlp --flat-playlist`; detection
  fetches only the small thumbnail JPEG.
- **No new dependencies** — Pillow (already used by `title_card`) computes the
  image stats; `yt-dlp`/`ffmpeg` unchanged; no image-hash lib.
- **Heavy deps never top-level** — PIL imported inside functions; the YouTube
  client and yt-dlp/HTTP calls injected as callables so `pytest` runs offline.
- **Privacy untouched** — setting a thumbnail does not change visibility; only
  videos currently showing an auto frame are touched; every set is reversible.

## Components

### `sermon_cut/livestream_thumbs.py` (new)

Pure, unit-tested logic + thin orchestration.

- `ordinal(n:int)->str` — `1`→`1st` … `21`→`21st`.
- `parse_service_date(title:str)->tuple[int,int,int]|None` — `(y,m,d)` or None;
  tolerant of abbreviations, extra spaces, missing space after the dash, a
  trailing ordinal, and the `Match`→March typo. Rejects impossible days.
- `livestream_label(title:str)->str|None` — `"<Month> <D><ord>, <YYYY> Livestream"`.
- `is_sunday_service(title:str)->bool` — True when the title denotes the Sunday
  worship service (case-insensitive contains "sunday" and "worship"). Excludes
  Wednesday/Friday/marriage/Bible-class-only titles.
- `has_branded_card(brightness:float, saturation:float)->bool` — the pure
  threshold: `brightness < 70 and saturation < 12`.
- `thumbnail_stats(image_bytes:bytes)->tuple[float,float]` — `(brightness,
  saturation)` of a downscaled RGB copy (PIL, lazy import). brightness =
  mean((r+g+b)/3); saturation = mean(max−min) over pixels.
- `find_candidates(*, enumerate_streams, fetch_thumbnail)->list[Candidate]` —
  orchestration: `enumerate_streams()` yields `(video_id, title)`; keep Sunday
  services with a parseable date; for each, `fetch_thumbnail(video_id)->bytes`,
  compute stats, and keep only those WITHOUT a branded card. Returns
  `Candidate(video_id, title, label)`. Both dependencies are injected.
- `run(*, config, enumerate_streams, fetch_thumbnail, render, set_thumbnail,
  out_dir, dry_run=False, ids=None, limit=None)->list[Result]` — find
  candidates, (optionally restrict by `ids`/`limit`), render each via
  `render(label, out_path)`, and unless `dry_run` call `set_thumbnail(video_id,
  png)`. Idempotent: reuse an existing `out_dir/<id>.png`. Returns per-video
  `Result(video_id, label, detected, rendered, set_ok)`. All I/O is injected;
  this function is fully testable with fakes.

`Candidate`/`Result` are small dataclasses (or NamedTuples).

### `sermon_cut/title_card.py` (modify)

Add `build_livestream_card(label:str, *, background_path, font_dir, out_path)->
Path`: one left-aligned Lora-Bold line at the sermon-title baseline
(`_BLOCK_TOP + eyebrow_h + _META_GAP`, `eyebrow_h=int(_EYEBROW_SIZE*1.4)`), gold
accent rule, no eyebrow — reusing `_measurer`/`fit_and_wrap`/`_load_font` and the
existing constants. Raises `TitleTooLongError` if it can't fit. PIL lazy-imported
as in `build_title_card`. (This is the experiment's `card.py`, folded into the
module it belongs to.)

### `sermon_cut/cli.py` (modify)

Add subcommand `livestream-thumbs`:
- Flags: `--dry-run` (scan + render, no set), `--ids A,B` (restrict), `--limit N`.
- Wires the real dependencies: `enumerate_streams` = `yt-dlp --flat-playlist
  --dump-json <channel>/streams` (subprocess, parse JSON lines);
  `fetch_thumbnail` = HTTP GET `https://i.ytimg.com/vi/<id>/maxresdefault.jpg`
  with `hqdefault.jpg` fallback (stdlib `urllib`); `render` =
  `build_livestream_card` onto `assets/intro_bg_ink.png`; `set_thumbnail` = the
  authenticated client's `thumbnails().set()`; `out_dir = runs/livestream-thumbs/`.
- Reads `youtube_channel_id`, background, and font dir from config/assets.
- Prints the result table and a final `set N/M` summary.

### `.claude/skills/livestream-thumbs/SKILL.md` (new)

Front-matter: name `livestream-thumbs`, a description that triggers on "set/find
thumbnails for the livestreams / the streams tab / the Sunday services." Body:
1. Confirm prerequisites (`assets/intro_bg_ink.png`, `token.json`).
2. Run `python -m sermon_cut livestream-thumbs --dry-run` first, show the scan
   table (what it detected as missing), and **sanity-check the count** — normal
   weekly run is 1; a first run may be ~100+ (expected, back-catalog).
3. Run `python -m sermon_cut livestream-thumbs` to set them.
4. Report the table; note YouTube thumbnail-propagation lag; point out any
   skipped/unparsed titles.

## Config

Reuses `youtube_channel_id`. Background `assets/intro_bg_ink.png`, fonts
`assets/fonts/`. Output `runs/livestream-thumbs/<id>.png` (gitignored). Detection
thresholds are module constants (`_CARD_MAX_BRIGHTNESS=70`,
`_CARD_MAX_SATURATION=12`).

## Testing (offline)

- `ordinal`, `parse_service_date`, `livestream_label` — ported cases + edge cases.
- `is_sunday_service` — accepts Sunday-worship titles, rejects Wednesday/Friday/
  class-only titles.
- `has_branded_card` — true at (35, 0.8) and (34.6, 0.8); false at (120, 51) and
  boundary checks around the thresholds.
- `find_candidates` — with a fake `enumerate_streams` (mix of Sunday/Wednesday,
  parseable/unparseable) and a fake `fetch_thumbnail` returning stub bytes mapped
  to known stats: asserts only Sunday-with-auto-frame become candidates, and an
  already-carded Sunday is excluded.
- `run` — with fakes for all I/O: asserts render called per candidate, set called
  per candidate unless `dry_run`, idempotent skip when the PNG exists, and the
  `Result` rows are correct. `thumbnail_stats`/`build_livestream_card` get a light
  PIL-backed test (produce stats for a synthesized dark vs. bright image;
  render a card and assert the file exists / TitleTooLong raises).

## Rollout / verification

- `--dry-run` on the real channel first: confirm the candidate list is the
  expected Sunday streams still on auto frames (the 22 2026 ones already carded
  should NOT appear).
- Then the live run; spot-check a couple of newly-set thumbnails on the channel.
- `pytest -q` stays green (package suite + new tests).

## Risks / notes

- **Dark auto-frame false-skip:** a Sunday video whose auto frame is unusually
  dark AND desaturated could be misread as already-carded and skipped. The
  margin is large (sat 0.8 vs 51) so this is unlikely; the dry-run table surfaces
  it, and re-running after a manual nudge is trivial. Acceptable.
- **Thumbnail propagation lag** on YouTube's CDN — not an error.
- **`drip` TTL / bg** — `assets/intro_bg_ink.png` is already committed to disk;
  the skill depends on the file, not the drip URL.
