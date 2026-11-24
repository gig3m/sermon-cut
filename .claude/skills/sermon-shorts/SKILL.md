---
name: sermon-shorts
description: Generate up to 3 vertical (9:16) YouTube Shorts from an already-processed sermon run and upload them Unlisted, fully unattended. Use when the user wants short clips cut from a sermon that's already been rendered by /sermon-cut. Accepts a run id. Runs without prompting — Claude auto-selects the strongest standalone moments; the Unlisted upload is the review point (the user reviews and publishes on YouTube).
---

# Sermon Shorts

Run the pipeline from the repo root to turn a **finished sermon run** into
up to **three** branded, captioned, subject-tracked 9:16 Shorts, **uploaded
Unlisted for review**. This runs **unattended** — do not stop to ask the user
which moments to clip. The Python package does the mechanical work (captions,
camera tracking, branding, upload); YOU do the judgment step (auto-selecting the
strongest standalone moments and titling them). The Unlisted upload is the review
gate: the user watches the finished Shorts on YouTube and publishes or deletes
each one.

## Prerequisite (check first)

This skill operates on a sermon that `/sermon-cut` has already rendered. Confirm
both exist before doing anything else:

- `runs/<run-id>/sermon.mp4`
- `runs/<run-id>/plan.json`

If either is missing, STOP and tell the user to run `/sermon-cut` for that video
first — Shorts are sub-clips of `sermon.mp4`, there is no standalone shorts-only
path.

## Setup check (first run only)

Confirm `.venv`, `config.toml` (with a `[shorts]` table —
see `config.example.toml` for the caption/tracking/branding defaults),
`client_secrets.json`, and `token.json` exist. Always invoke the CLI as
`.venv/bin/python -m sermon_cut ...`.

## Arguments

- Positional: `<run-id>` — the `runs/<run-id>/` directory of a completed sermon.

The skill runs unattended: no confirmation prompt, no candidate-picking step. It
auto-selects, renders, and uploads Unlisted, then reports the URLs.

## Steps

1. **Pick paths.** `runs/<run-id>/sermon.mp4`, `runs/<run-id>/plan.json`,
   `runs/<run-id>/transcript.json` (from the sermon run), and
   `runs/<run-id>/shorts/candidates.json` (new, this skill writes it).

2. **Step A — Select the strongest standalone moments (YOUR judgment, no user
   input).** Read `runs/<run-id>/transcript.json`. Drifted timing is fine here —
   you're finding moments, not pinning frames (that happens later, drift-free,
   from a re-transcribe of the extracted window). Choose **up to 3** moments that
   genuinely "land standalone", applying a real quality bar:
   - a hook in the first ~3 seconds;
   - a complete arc (setup → point → landing) that doesn't lean on earlier sermon
     context to make sense;
   - an ending on a resolution or punch, not mid-thought;
   - **cut tight.** Prefer the shortest version that still lands: open on the
     hook, end on the punch, and trim the middle — you can usually drop part of
     an illustration or a repeated beat and still make sense. Illustration-driven
     points sprawl past 80s if you keep everything; aim to get them under ~60s.

   **Quality gate — up to 3, not exactly 3.** Only include a moment if it clears
   the bar above. Prefer **fewer strong Shorts over three mediocre ones**: a rich
   sermon may yield 3, a teaching-heavy one with no clean pull-quotes may yield 1
   — or **0**, in which case render nothing and tell the user why (e.g. no
   self-contained moment, or a singing-only service with no sermon). Rank by
   standalone strength and keep the top ≤3.

   **Context-integrity filter (this REPLACES the old human gate).** Since these
   upload without a human first approving each pick, a clip that could
   **misrepresent the sermon's point out of context is EXCLUDED, not uploaded** —
   drop it from the set rather than shipping something misleading, even to
   Unlisted. Only auto-select moments that read honestly on their own. (If a
   borderline moment is the only candidate, it's better to ship one fewer Short.)

   Target length ~30–60s (aim under 60s; `shorts-validate` flags anything over
   60s as `WARN_LONG`), acceptable up to ~75s when the arc genuinely needs it,
   hard cap 180s (YouTube Shorts max is 3 min).

   Write `runs/<run-id>/shorts/candidates.json` with only the moments you
   selected, each marked `"chosen": true`:
   ```json
   { "candidates": [
     { "slug": "where-do-you-run",
       "title": "Where Do You Run?",
       "source_start": 3865.0, "source_end": 3947.0,
       "rationale": "Self-contained hook: a question everyone feels, resolved in 80s.",
       "standalone_confidence": "high",
       "context_integrity": "ok",
       "chosen": true }
   ] }
   ```
   `source_start`/`source_end` are in the same absolute source-video seconds as
   `plan.start`/`plan.end` (i.e. transcript.json time, not sermon.mp4-relative
   time). `slug` is a filesystem-safe id — it becomes the per-short subdirectory
   name. Every candidate you write is one you're rendering; there is no separate
   pick step.

3. **Sanity-check the band:**
   `shorts-validate --candidates runs/<run-id>/shorts/candidates.json --config config.toml --workdir runs/<run-id>`
   Prints `<slug> <BAND>` per candidate (`IN_BAND`, `WARN_LONG`, `TOO_SHORT`,
   `TOO_LONG`). If a selected candidate is `TOO_SHORT`/`TOO_LONG`, adjust its
   `source_start`/`source_end` to bring it into band and re-check (this is your
   own correction, not a user prompt). `WARN_LONG` is fine to proceed with.

4. **Per selected candidate**, run the four render/upload stages with
   `--workdir runs/<run-id>/shorts/<slug>` (a fresh subdirectory per short —
   artifacts don't collide across candidates). Render + upload each without
   pausing:

   a. **Clip the window** (stream-copy out of the already-rendered sermon, no
      re-download):
      ```
      shorts-clip --sermon runs/<run-id>/sermon.mp4 --plan runs/<run-id>/plan.json \
        --candidate <slug> --candidates runs/<run-id>/shorts/candidates.json \
        --config config.toml --workdir runs/<run-id>/shorts/<slug>
      ```
      Writes `window.mp4` (candidate window ± pad, to survive drift when the
      window is re-transcribed next). Raises if the candidate is out of the
      length band.

   b. **Render** (re-transcribes the window with word timestamps for drift-free
      in/out, builds captions, tracks the subject, crops to 9:16, appends the
      branded outro, assembles):
      ```
      shorts-render --plan runs/<run-id>/plan.json --candidate <slug> \
        --candidates runs/<run-id>/shorts/candidates.json --config config.toml \
        --outro-card assets/outro_card.png --font-dir assets/fonts \
        --workdir runs/<run-id>/shorts/<slug>
      ```
      `--outro-card` and `--font-dir` are optional — they default to
      `assets/outro_card.png` and `assets/fonts`; omit them unless overriding.
      This can take a few minutes each (re-transcribe + tracking pass).
      **Serialize the renders** — run one candidate's `shorts-render` fully
      before starting the next (concurrent heavy ffmpeg/whisper jobs corrupt
      output; see CLAUDE.md). Prints the path to `short.mp4`.

   c. **Upload** (Unlisted, `#Shorts`, links back to the full sermon if it's
      already uploaded):
      ```
      shorts-upload --video runs/<run-id>/shorts/<slug>/short.mp4 --candidate <slug> \
        --candidates runs/<run-id>/shorts/candidates.json --plan runs/<run-id>/plan.json \
        --sermon-run runs/<run-id> --config config.toml \
        --workdir runs/<run-id>/shorts/<slug>
      ```
      `--sermon-run runs/<run-id>` points at the sermon run's own `upload.json`
      receipt (written when `/sermon-cut`'s `upload` stage ran); if the sermon
      hasn't been uploaded yet, the description simply omits the "Full sermon:"
      link — this isn't a blocker. Prints the short's `https://youtu.be/...` URL.

5. **Report (the review point).** List every rendered short's
   `https://youtu.be/...` URL with its title, and remind the user each is
   **Unlisted** — they review on YouTube and publish (or delete) each one. If you
   selected 0 moments, say so and why. This report replaces the old pre-render
   review gate: the user's decision happens on the finished, Unlisted clips.

## Rules

- **Runs unattended.** Do not stop to ask which moments to clip — auto-select up
  to 3 and ship them Unlisted. The Unlisted upload is the review gate.
- **Up to 3, quality-gated.** Never pad to 3 with weak clips; 0–3 is the range,
  and 0 is a valid, honest outcome.
- **Context integrity is enforced by exclusion.** A moment that could mislead out
  of context is dropped, not uploaded — because no human vets the pick before it
  lands.
- Never set privacy to anything but unlisted (the CLI enforces this on every
  upload path, sermon and shorts alike).
- Shorts are sub-clips of the already-rendered `sermon.mp4` — no stage
  re-downloads or re-fetches the source video.
- Stages are idempotent; on retry, completed artifacts under
  `runs/<run-id>/shorts/<slug>/` are reused.
- If any stage errors, stop and report; the run dir keeps all artifacts for
  retry.
