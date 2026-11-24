---
name: sermon-read-title
description: Use when the auto runner invokes the headless title step (claude -p /sermon-read-title) inside a runs/<id>/ that has title_context.json. Reads the church's on-screen sermon-title slide and judges whether it names the same sermon as the transcript-drafted title.
---

# Sermon read-title (unattended)

`title_context.json` in the current directory holds `draft_title` (the title
the transcript judge drafted) and `frames` (PNG frames sampled across the first
~10 minutes of the sermon clip). Write `slide_title.json` and exit.

## Steps

1. Read every frame in `frames`. The title slide is the picture-in-picture box
   at top right (roughly x 1340–1875, y 20–320 of a 1920×1080 frame). It may be
   blank/dark in the early frames — the screen is sometimes off during the
   greeting. Use the first frame where it shows a title slide (not a
   verse/outline slide). Crop and upscale it if the text is small.
2. `slide_title` = the slide's heading, plus its scripture line if it has one,
   joined as `Heading: Scripture` (e.g. `Jesus: Colossians 1–3`). Title case;
   no speaker, date, or series branding. `""` if no title slide is visible in
   any frame.
3. `confidence` — compare `slide_title` with `draft_title`:
   - `high` only when the slide's heading is itself a descriptive sermon title
     (three or more words, or a phrase rather than a single topic word) AND it
     names the same sermon as the draft — same idea in specific words, not
     merely the same broad subject.
   - `low` otherwise: a one- or two-word heading (`Jesus`, `Grace`, a book
     name), a series banner, no slide found, or a heading whose idea does not
     match the draft.
   Low confidence means the pipeline keeps the draft; high means the slide
   replaces it. When in doubt, `low` — a descriptive draft beats a terse slide.
4. Write `slide_title.json` with EXACTLY these keys:
   `{"slide_title": "<string>", "confidence": "high"|"low", "note": "<one line: which frame, what the slide said, why this confidence>"}`

Do NOT edit any other file, run any pipeline stage, or read the transcript to
invent a title — the draft already exists; your job is the slide and the match.
