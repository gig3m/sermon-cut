# Sermon Shorts — design

Auto-generate vertical (9:16) YouTube **Shorts** from an already-processed sermon,
under the same local / zero-recurring-cost constraints as the main pipeline. A
2026-07-09 spike (`experiments/shorts/`) proved every hard part end-to-end on real
footage ("Cities of Refuge"). This spec turns that spike into a real feature that
slots into `sermon-cut`.

> **Amendment (2026-07-09, post-ship):** The **human pre-render pick gate (Review
> gate #1) was removed.** `/sermon-shorts` now runs **unattended** — Claude
> auto-selects **up to 3** of the strongest standalone moments (quality-gated; 0
> is valid), and the `context_integrity` check became an **exclusion filter**
> (a clip that could mislead out of context is dropped, not uploaded). The
> Unlisted upload remains the review point: the user reviews the finished Shorts
> on YouTube and publishes/deletes. The sections below describing "propose a
> shortlist → a human picks" reflect the original design; the shipped behavior is
> in `.claude/skills/sermon-shorts/SKILL.md`. No CLI/module code changed — this
> was an orchestration change only.

## Goals / non-goals

- **Goal:** from a finished sermon run, propose a shortlist of standalone moments,
  and for the ones a human picks, render branded, captioned, subject-tracked 9:16
  Shorts and upload them Unlisted for review.
- **Non-goal:** full automation. A human always chooses which candidates render and
  reviews the uploaded result before publishing (context-integrity guardrail).
- **Constraint:** no paid/recurring dependency; everything local. Uploads are
  **always Unlisted** (existing invariant). No new full video download — Shorts are
  sub-clips of the already-rendered `sermon.mp4`.

## Trigger & workflow

`/sermon-shorts <run-id> [--yes]`, driven by a new skill
`.claude/skills/sermon-shorts/SKILL.md`. Requires a **completed** sermon run
(`runs/<id>/sermon.mp4` + `plan.json`); errors clearly if the sermon hasn't been
rendered.

1. **Select (skill judgment).** Claude reads `runs/<id>/transcript.json` (drifted
   timing is fine for *finding* moments) and writes `runs/<id>/shorts/candidates.json`
   — ~3–5 candidate moments.
2. **Review gate #1.** Claude shows the shortlist (title, timestamp, standalone
   rationale, context-integrity flag, confidence). The human marks which to render
   (or none). `--yes` does NOT bypass this gate.
3. **Per chosen candidate** (idempotent stages, artifacts under
   `runs/<id>/shorts/<slug>/`):
   1. `shorts-clip` — stream-copy a wide window out of `sermon.mp4` → `window.mp4`.
   2. re-transcribe the window **with word timestamps** → `words.json`; pin exact
      in/out (drift-free).
   3. `shorts-render` — captions + tracking + branded assemble → `short.mp4`.
   4. `shorts-upload` — Unlisted, `#Shorts` → returns the URL.
4. **Review gate #2.** Uploaded Unlisted; human reviews on YouTube and publishes.

## Selection

`runs/<id>/shorts/candidates.json`:

```json
{ "candidates": [
  { "slug": "where-do-you-run",
    "title": "Where Do You Run?",
    "source_start": 3865.0, "source_end": 3947.0,
    "rationale": "Self-contained hook: a question everyone feels, resolved in 80s.",
    "standalone_confidence": "high",
    "context_integrity": "ok",
    "chosen": false }
] }
```

Timestamps are approximate (from the drifted full transcript); the exact in/out are
pinned later from the drift-free window re-transcribe. Mapping into `sermon.mp4`:
`sermon_mp4_time = source_time − (plan.start − front_padding_seconds)`.

**"Lands standalone" criteria** (Claude applies during selection): a hook in the
first ~3s; a complete arc (setup → point → landing) not dependent on earlier
context; an ending on a resolution/punch, not mid-thought; and a **context-integrity**
check — a clip that could misrepresent the sermon out of context is surfaced with
`context_integrity: "flag"` + a note rather than silently offered.

**Length band:** target 30–75s; acceptable 20–90s; hard cap 180s (YouTube Shorts
max is 3 min). `validate` (in `shorts_select.py`) rejects <15s or >180s and warns
>90s — mirroring the sermon 10–45 min sanity band.

## Render pipeline (per chosen candidate)

- **`shorts-clip`** — `ffmpeg -ss <win_start> -to <win_end> -i sermon.mp4 -c copy
  window.mp4`, where the window is the candidate ± a wide pad (~5s each side) to
  survive drift. No new download.
- **Word-timestamp re-transcribe** — new `transcribe_words()` in
  `sermon_cut/transcribe.py` (faster-whisper, `vad_filter=True`,
  `condition_on_previous_text=False`, `word_timestamps=True`), injectable
  `model_factory`. Output `words.json`. Exact short in/out pinned from these words.
- **Captions (`shorts_captions.py`)** — phrase-boundary chunking (break before a
  small set of phrase-leading connectors once a chunk has ≥2 words, or on
  punctuation, cap ~4 words), then a scrim-card render: rounded translucent plate,
  **Nunito Bold** (vendored variable font, `set_variation_by_name("Bold")`), white
  text, amber active word for karaoke. Per-word states → qtrle alpha `overlay.mov`.
  Chunking and timeline construction are pure functions (unit-tested).
- **Tracking (`shorts_track.py`)** — MOG2 background subtraction on the fixed camera;
  per-frame robust **weighted-median** column center with an **outlier gate** (reject
  >`MAXJUMP` px jumps, hold last) and a minimum-foreground hold; then a **dead-zone
  follow** camera (holds still within ±`DEADZONE`), **velocity cap** (`MAX_VEL`), and
  zero-phase EMA smoothing. The crop is rendered in OpenCV and piped to ffmpeg
  (ffmpeg's crop-x expression can't hold a per-frame track). `cv2`/`numpy` are
  imported lazily *inside* the functions; the camera math (`ema`, dead-zone follow,
  velocity cap) is a pure function unit-tested without cv2.
- **Assemble (`shorts_render.py`)** — crop-pipe → overlay captions → `body.mp4`;
  then **cold-open fade-up from black** → **xfade** into the 9:16 outro card →
  **fade to black**, with audio `afade` under the outro. Both xfade branches are
  normalized (`fps=30,format=yuv420p,setsar=1,settb=AVTB`) — without this xfade fails
  to reinitialize (`-22`) because the body is 60fps and the card is timeless. Output
  `short.mp4`.
- **9:16 outro card** — built seamlessly from `assets/outro_card.png` scaled onto a
  near-black 1080×1920 canvas (bg matches, so it's seamless); optional
  `assets/outro_card_9x16.png` override if a bespoke card is dropped in.

All spike-tuned values live in a new **`[shorts]`** config table: caption size /
position / colors, tracking `DEADZONE` / `MAXJUMP` / `MAX_VEL` / EMA alphas, branding
`fade_up` / `dissolve` / `outro_hold` / `fade_out`, and crop geometry
(`crop_w/h/y`). Crop geometry is **tuned to this fixed camera** — documented as such.

## Upload

`shorts-upload` reuses `upload.py`'s `upload_video()` + auth. Metadata builder
(`shorts_render.py` or `upload.py`): title `"<Short title> — <Sermon title>"`,
description = short blurb + `Full sermon: <link>` + config footer + `#Shorts`,
category 29, **privacy `unlisted`** (enforced; no code path uploads Public).

To supply the "Full sermon" link, the **sermon** `upload` stage will persist
`runs/<id>/upload.json` (`{video_id, url, uploaded_at}`); `shorts-upload` reads it if
present (and links; omits the line if absent). This also implements the previously
deferred **upload idempotency receipt**.

## Modules & file layout

New:
- `sermon_cut/shorts_select.py` — candidate dataclass, load/save `candidates.json`,
  `validate` (length band), timestamp mapping helpers.
- `sermon_cut/shorts_captions.py` — chunking + overlay render.
- `sermon_cut/shorts_track.py` — tracking (lazy cv2) + pure camera math.
- `sermon_cut/shorts_render.py` — window extract, body render, branded assemble,
  metadata builder.
- `.claude/skills/sermon-shorts/SKILL.md` — orchestration + judgment + review gates.

Changed:
- `sermon_cut/transcribe.py` — add `transcribe_words()`.
- `sermon_cut/upload.py` — write `upload.json` receipt (sermon), read it (shorts).
- `sermon_cut/cli.py` / `__main__.py` — add `shorts-clip`, `shorts-render`,
  `shorts-upload` (and `shorts-validate`) subcommands.
- `config.example.toml` / `config.py` — `[shorts]` table + loader.
- `pyproject.toml` — add `opencv-python-headless`, `numpy`.

Artifacts: `runs/<id>/shorts/candidates.json`, and per short
`runs/<id>/shorts/<slug>/{window.mp4, words.json, overlay.mov, body.mp4, short.mp4}`;
shared `runs/<id>/shorts/outro_9x16.png`. Every stage idempotent (skips if its output
exists), consistent with the pipeline.

## Dependencies & invariants

- `opencv-python-headless` + `numpy` become required deps but are **imported lazily**
  inside tracking functions so `pytest` stays fully offline (heavy-deps-never-at-import
  invariant preserved).
- **Unlisted-only** upload preserved. **Windowed only** — Shorts are sub-clips of
  `sermon.mp4`; no stage downloads full video.

## Testing (TDD, offline)

Unit tests with injectable runners / no real binaries or GPU:
- `shorts_select.validate` — length band (reject <15s / >180s, warn >90s), mapping math.
- `shorts_captions` — phrase-boundary chunking (no mid-phrase breaks), timeline build.
- `shorts_track` — pure camera math: dead-zone hold, velocity cap, outlier gate,
  EMA (fed synthetic tracks; no cv2).
- `shorts_render` — assemble filter-string construction (offsets/fades), window
  time-mapping.
- `upload` — Shorts metadata builder (privacy unlisted, `#Shorts`, sermon link
  present/absent), `upload.json` receipt round-trip.

## Deferred / out of scope

- Auto-selecting without a human pick (kept human-gated on purpose).
- Per-short manual restyling (styles come from `[shorts]` config).
- Multi-camera / non-fixed-camera tracking (crop geometry is camera-specific).
- A bespoke hand-designed 9:16 outro card (optional override path exists).
