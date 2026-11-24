# Dynamic Title Card — Design

**Date:** 2026-07-07
**Status:** Approved, ready for implementation
**Supersedes:** the deferred "per-week dynamic Canva intro text" seam noted in CLAUDE.md.

## Problem

The intro stage currently loops a single static branded PNG
(`assets/intro_card.png`) into a 5-second bumper. Every sermon gets the same
"welcome" card. We want a per-sermon **title card** — the church branding plus
this week's sermon **title / speaker / date** — generated automatically from the
data the pipeline already has.

## Approach

Keep the design work in Claude Design (as the current card was made) and keep the
per-week text in code:

- **Claude Design** produces a branded *background* once → `assets/intro_bg.png`
  (1920×1080): gold logo + "Southside Church" in the upper third, a clear zone
  below for text.
- **Pillow** composites the sermon title/speaker/date into that clear zone on
  every run, reading from the existing `plan.json`.

Pillow was chosen over ffmpeg `drawtext` (fiddly wrapping/escaping) and Canva
autofill (network/interactive, not offline-testable) because it gives real
typographic control — measure, wrap, auto-fit — while staying local,
deterministic, and offline-testable, consistent with the project's invariants.

## Components

### New: `sermon_cut/title_card.py`

Three units, split so the layout math is testable without fonts:

- `format_date(iso: str) -> str` — pure. `"2026-07-05" → "July 5, 2026"`.
  Raises `ValueError` on malformed input.
- `fit_and_wrap(text, max_width, max_height, max_font, min_font, measure) -> (lines, font_size)`
  — pure. Wraps `text` to fit `max_width` and shrinks the font from `max_font`
  toward `min_font` until the wrapped block fits `max_width × max_height`.
  `measure(text, font_size) -> (w, h)` is injected, so tests use a synthetic
  measurer (no font files, deterministic).
- `build_title_card(plan, *, background_path, font_dir, workdir) -> Path`
  — loads `intro_bg.png`, draws the stack (title → speaker → gold divider →
  date) into the clear zone using the vendored fonts, writes
  `workdir/title_card.png`, returns it. Pillow imported **lazily** inside the
  function (invariant). Idempotent: returns early if `title_card.png` exists.

### Modified: `sermon_cut/cli.py`

`intro` subcommand gains `--plan`, `--background`, `--font-dir` (all optional):

- **`--plan` given** → `build_title_card` from plan data, then `make_intro` loops
  the result.
- **`--plan` omitted** → current behavior (`make_intro` on `--image`). Backward
  compatible; existing tests and the static-card fallback are untouched.

### Modified: `sermon_cut/upload.py`

The uploaded video **title** becomes `f"{plan.title} — {format_date(plan.date)}"`.
`plan.title` stays clean (no embedded date); the date is composed in at upload
time and on the card. Single source of truth: title/date/chapters all derive from
one `plan.json`, so a confirm-gate edit propagates everywhere.

### Assets & dependency

- `pyproject.toml` — add `pillow>=10`.
- `assets/fonts/` — vendored **Lora** (serif; title/speaker) and **Nunito**
  (sans; date label), both OFL/Google Fonts (redistributable).
- `assets/intro_bg.png` — branded background, produced once via Claude Design.

## Layout & typography

Brand vocabulary from the existing card: white serif headline, gold (~`#C7A66B`)
accents, letter-spaced caps sans labels, on the green backdrop.

Clear zone: lower ~55% of the frame, ~12.5% side margins (~1440px usable width).
Stack, vertically centered in the zone:

1. **Title** — Lora, white, hero size, auto-fit (wrap ≤3 lines, shrink
   ~110px→~60px until it fits width+height).
2. **Speaker** — Lora, gold, medium.
3. **Gold divider** — thin rule.
4. **Date** — Nunito, letter-spaced caps, muted; `format_date(plan.date)`.

## Error handling

- Missing `intro_bg.png` or a vendored font → raise with the missing path named.
- **Title too long** → the title is sized as large as possible within
  `_TITLE_MAX_LINES` lines and the vertical space above the frame edge, down to a
  `_TITLE_MIN` floor. If it still won't fit, `build_title_card` raises
  `TitleTooLongError` so the pipeline stops for a shorter title, rather than
  shrinking to an unreadable size.
- The static `--image` path (no `--plan`) remains the documented fallback.

## Testing

- `format_date` — pure unit tests incl. malformed input.
- `fit_and_wrap` — pure tests with a synthetic measurer: short text stays large;
  long text wraps and/or shrinks; never exceeds bounds.
- `build_title_card` — integration test: draw onto a small synthetic background
  with a bundled font, assert output PNG exists, is 1920×1080, and is non-blank;
  assert idempotent skip.
- `upload` — assert the composed title equals `"{title} — {formatted date}"`.

All offline and deterministic. Pillow is import-light at module top level (lazy
import), so `pytest` collection stays fast.

## Out of scope (YAGNI)

- Config-driven styling (colors/sizes/margins) — hardcode brand constants now.
- Multiple intro slides — one card.
- Runtime Canva generation — background is a one-time Claude Design export.
