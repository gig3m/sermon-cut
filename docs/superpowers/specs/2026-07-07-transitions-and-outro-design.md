# Transitions & Outro Bumper — Design

**Date:** 2026-07-07
**Status:** Approved, ready for implementation

## Problem

`assemble` currently hard-cuts intro→sermon via a `concat` filter. We want a
finished "full treatment": fade up from black, cross-dissolve the intro card into
the sermon, dissolve the sermon into a static outro card, and fade out to black.

## Approach

Stay inside `assemble`'s existing re-encoding filtergraph (transitions add
negligible cost) and replace `concat` with `xfade` (video) + `acrossfade`
(audio). Add a static outro bumper looped from `assets/outro_card.png`.

Timeline (defaults, all config-adjustable):

```
black → [fade-up 0.6s] intro (5s) → [dissolve 0.8s] → sermon → [dissolve 0.8s] → outro (5s) → [fade-out 0.6s] → black
```

`xfade` and `acrossfade` each overlap a join by the dissolve duration, so audio
and video stay in lockstep. The dissolve consumes the sermon's 8s front/back
padding (throwaway lead-in/out), so no preaching is lost. Intro/outro are silent,
so `acrossfade` becomes a natural fade-up on the sermon's first second and a
fade-down on its padded tail.

## Timing math

`assemble` probes each input's duration (ffprobe) to compute exact offsets. With
intro length `a`, sermon `b`, outro `c`, dissolve `D`, fade `F`:

- intro→sermon dissolve offset: `a - D`
- combined intro+sermon length: `a + b - D`
- sermon→outro dissolve offset: `a + b - 2D`
- total length: `a + b + c - 2D`
- fade-out start: `total - F`

Two-clip (no outro) variant: dissolve at `a - D`, total `a + b - D`, fade-out at
`total - F`.

## Components

### `sermon_cut/assemble.py`

`assemble(intro_path, sermon_path, workdir, *, outro_path=None, dissolve=0.8,
fade=0.6, width, height, fps, runner, duration_probe=_default_probe) -> Path`

- `duration_probe(path) -> float` — injectable; default runs
  `ffprobe -show_entries format=duration`. Lets tests supply fixed durations.
- Builds the `xfade`/`acrossfade` filtergraph with computed offsets, video
  `fade=in`/`fade=out`, and `loudnorm` on the combined audio.
- `outro_path=None` → two-clip weave (intro→sermon + fades). Graceful fallback
  when `assets/outro_card.png` is absent.
- Idempotent: returns early if `final.mp4` exists.

### `sermon_cut/intro.py`

Rename `make_intro` → `make_bumper(image, workdir, *, seconds=5,
out_name="intro.mp4", ...)`. Both intro and outro are still→clip bumpers; the
only difference is the output filename. Update the `intro` call site and tests.

### `sermon_cut/cli.py`

- `intro` subcommand: unchanged behavior (calls `make_bumper`, out `intro.mp4`).
- New `outro` subcommand: `outro --image assets/outro_card.png --seconds <n>` →
  loops the still into `outro.mp4`.
- `assemble` subcommand: add `--outro`, `--dissolve`, `--fade`.

### `sermon_cut/config.py` + `config.example.toml`

Add `outro_seconds=5`, `dissolve_seconds=0.8`, `fade_seconds=0.6`.

### Skill (`SKILL.md`)

- New step: build the outro clip from `assets/outro_card.png` via `outro`
  (using `outro_seconds`).
- `assemble` step passes `--outro runs/<id>/outro.mp4 --dissolve <dissolve_seconds>
  --fade <fade_seconds>`. If `assets/outro_card.png` is missing, skip `--outro`.

## Error handling / fallback

- Missing `outro_card.png` → the skill omits `--outro`; `assemble` does the
  two-clip weave. No crash.
- `duration_probe` failure → surfaces as a normal stage error (run dir retained).

## Testing (offline, injectable runner + probe)

- `assemble` (3-clip): assert filtergraph contains `xfade`/`acrossfade` with the
  correct computed offsets, `fade=t=in` at 0 and `fade=t=out` at `total-F`,
  `loudnorm`, and the right `-map`s.
- `assemble` (2-clip, no outro): single dissolve + fades, correct offsets.
- `assemble` idempotency (existing test preserved).
- `make_bumper`: existing behavior + `out_name` produces the named file.
- `outro` CLI dispatch; `assemble` CLI passes `--outro`/`--dissolve`/`--fade`.
- `config`: new fields parse with defaults.

## Out of scope (YAGNI)

- Non-fade transition styles (wipes/slides) — soft dissolve only; the filter
  string centralizes the transition name if we ever want more.
- Background music / stingers under the bumpers.
- Dynamic (per-sermon) outro text — the outro is a static PNG.
