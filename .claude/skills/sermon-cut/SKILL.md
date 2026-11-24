---
name: sermon-cut
description: Extract the sermon from a 2-hour church livestream archive and upload it to YouTube as Unlisted. Use when the user wants to process a Sunday service recording into a sermon video. Accepts a YouTube URL or "latest", optional --yes (skip confirm gate) and --speaker "Name" (guest preacher).
---

# Sermon Cut

Run the pipeline from the repo root to turn a full service recording into
a sermon video. The Python package does the mechanical work; YOU do the judgment
step (reading the transcript to locate the sermon and draft metadata).

## Setup check (first run only)

Confirm `.venv`, `config.toml`, `client_secrets.json`, and
`token.json` exist. If not, point the user at `README.md` setup. Always invoke the
CLI as `.venv/bin/python -m sermon_cut ...`.

## Arguments

- Positional: a YouTube watch URL, or the word `latest`.
- `--yes` — skip the confirm gate.
- `--speaker "Name"` — override the default preacher (guest weeks).

## Steps

1. **Resolve source.** If the user said `latest`, run `resolve-latest <channel_id>`
   (channel id from `config.toml`) to get the URL.
2. **Pick a run dir.** Use `runs/<video-id>/` under the project. Get the video id
   from the URL (or after fetch-audio). Pass `--workdir runs/<video-id>` to every
   stage.
3. **Fetch audio:** `fetch-audio <url>`.
4. **Transcribe:** `transcribe --audio runs/<id>/audio.m4a`. This can take several
   minutes; tell the user.
5. **Find the sermon (YOUR judgment).** Read `runs/<id>/transcript.json`. Identify
   the single sustained teaching block — the sermon (typically 20–30 min of one
   speaker, after music/announcements). Determine its `start`/`end` (seconds).
   **Set `start` at the preacher's first words (greeting), NOT at a preceding
   Scripture reading** — the reading is usually done by someone else (often a
   child) and reads as jarring; exclude it. Because the clip adds
   `front_padding_seconds` before `start`, and a reading often runs right up to
   the greeting with no gap, set `start` = greeting time + `front_padding_seconds`
   so the padded clip lands exactly on the greeting. Draft a `title`, a 2–4
   sentence `description`, and 3–6 `chapters` (each a `{time, label}` within the
   sermon). Set `speaker` to `--speaker` if given, else the config
   `default_speaker`. Set `date` from the source upload date (in
   `source.info.json` `upload_date`, format `YYYY-MM-DD`). Write all of this to
   `runs/<id>/plan.json`.
6. **Validate:** `validate-plan --plan runs/<id>/plan.json`. If it prints
   `OUT_OF_BAND`, the detected length is suspicious (<10 or >45 min) — do NOT
   honor `--yes`; force the confirm gate and explain why.
7. **Confirm gate** (unless `--yes` and `IN_BAND`): show the user the detected
   start/end (as HH:MM:SS), length, title, speaker, and chapters. Ask them to
   approve or adjust. Apply edits to `plan.json` and re-validate.
8. **Clip the video:** `clip <url> --start <start> --end <end> --front-padding <front_padding_seconds> --back-padding <back_padding_seconds>` (pass the padding values from `config.toml`; keep front tight so a pre-sermon reading isn't pulled in).
9. **Intro card.** Build a per-sermon title card from the plan:
   `intro --plan runs/<id>/plan.json --background assets/intro_bg.png --seconds <intro_seconds>`
   (pass the `intro_seconds` value from `config.toml`). This composites the
   sermon title/speaker/date onto the branded background via Pillow. If it fails
   with `TitleTooLongError`, the title is too long to render at a readable size —
   STOP and ask the user for a shorter `plan.title`, update it, then re-run.
   Fallback: if `assets/intro_bg.png` is missing, use the static card instead
   with `intro --image assets/intro_card.png --seconds <intro_seconds>`.
10. **Outro card.** If `assets/outro_card.png` exists, build the outro bumper:
    `outro --image assets/outro_card.png --seconds <outro_seconds>` (from
    `config.toml`). Skip this step if there is no outro card.
11. **Assemble** (fade up → dissolve into sermon → dissolve to outro → fade out):
    `assemble --intro runs/<id>/intro.mp4 --sermon runs/<id>/sermon.mp4 --outro runs/<id>/outro.mp4 --dissolve <dissolve_seconds> --fade <fade_seconds>`
    (pass `dissolve_seconds`/`fade_seconds` from `config.toml`). Omit `--outro`
    if no outro bumper was built — it degrades to a two-clip weave.
12. **Upload:** `upload --video runs/<id>/final.mp4 --plan runs/<id>/plan.json`.
    Sets `runs/<id>/title_card.png` as the custom thumbnail automatically (so
    YouTube doesn't auto-pick an ugly mid-dissolve frame). Override with
    `--thumbnail <path>` if needed.
    Report the returned `https://youtu.be/...` URL and remind the user it is
    **Unlisted** — they review and publish.

## Rules

- Never set privacy to anything but unlisted (the CLI enforces this).
- Stages are idempotent; on retry, completed artifacts are reused.
- If any stage errors, stop and report; the run dir keeps all artifacts for retry.
