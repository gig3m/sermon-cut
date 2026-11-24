---
name: sermon-auto
description: Use when the auto runner invokes the headless judgment step (claude -p /sermon-auto) inside a runs/<id>/ that already has audio.m4a and transcript.json. Not for interactive use; /sermon-cut is the attended path.
---

# Sermon Auto (unattended)

Runs **headless** (`claude -p`) inside an existing `runs/<id>/` that already has
`audio.m4a` and `transcript.json`. Does the judgment step of `/sermon-cut` with
**no prompts** and writes two files (`plan_draft.json`, `auto_verdict.json`), then exits.

## Steps

1. Read `runs/<id>/transcript.json`. Identify the single sustained teaching block
   (the sermon): opens on the preacher's greeting (NOT a pre-sermon Scripture
   reading, usually read by a child), closes on the closing prayer's "Amen".
   Exclude the reading; include an intro video/greeting if one plays.
2. If there is no sermon (e.g. a 5th-Sunday singing service), write
   `runs/<id>/auto_verdict.json` = `{"skip": true, "note": "<why>"}` and STOP.
3. Otherwise write `runs/<id>/plan_draft.json` — a ROUGH draft the render step
   refines (do NOT pin exact frames; the pipeline re-transcribes to do that):
   - `rough_start`, `rough_end` (approximate source seconds; ±30s is fine),
   - `in_phrase`: a short, distinctive word sequence spoken right at the
     preacher's opening greeting (e.g. "good morning open your bibles to") —
     NOT from the pre-sermon reading,
   - `out_phrase`: a short distinctive sequence at the very end, ending on the
     closing prayer's "amen" (e.g. "in Christ's name amen"),
   - `title`: ALWAYS draft one — a descriptive sermon title of 3–7 words in
     Title Case that states the sermon's central claim or image (e.g. "Christ
     Is Your Life", "The God Who Knows Every Name"). Never a bare topic word
     ("Jesus", "Grace") and never the scripture reference alone. If the
     preacher states the title out loud, use that. The render step reads the
     church's on-screen slide and replaces this draft ONLY when it confidently
     names the same sermon; a low-confidence match keeps your draft, so it
     must stand on its own as a YouTube title.
   - `speaker`: config `default_speaker` unless the transcript clearly names a
     guest,
   - `date`: from `source.info.json` `upload_date` (`YYYY-MM-DD`),
   - `description`: 2–4 sentences,
   - `chapter_topics`: 3–6 `{ "phrase": "<distinctive phrase unique within the
     sermon>", "label": "<chapter label>" }` in order.
4. Self-assess and write `runs/<id>/auto_verdict.json` with EXACTLY these keys:
   - `in_point_ok` (bool): true only if you are confident the clip opens on the
     preacher's greeting, not the reading.
   - `out_point_ok` (bool): true only if you are confident it closes on the
     prayer/"Amen", not mid-thought or into announcements.
   - `no_reading_bleed` (bool): set from the AUDIO GAP CHECK below — never from
     the transcript.
   - `note` (string): one-line rationale. Include the measured gap (e.g.
     "19.3s silence 3776–3796 before the greeting"), or "no gap ≥3s found".
   Be honest — a false flag here is what routes the week to human approval.

## Audio gap check (required before writing `no_reading_bleed`)

Whisper segments HIDE pauses. A single segment routinely spans the reading's
last words and the preacher's "good morning" ("...in him all things hold
together good morning so great to see you"), so adjacency in `transcript.json`
is NOT evidence that the reading runs into the sermon. Measure the audio:

```
ffmpeg -hide_banner -nostats -ss $((rough_start-40)) -t 80 -i audio.m4a \
  -af silencedetect=noise=-50dB:d=1.5 -f null - 2>&1 | grep silence_
```

Reported times are relative to `-ss` — add `rough_start-40` back. Then:

- A silence of **≥3s** whose end lies within ~10s BEFORE the `in_phrase`
  words → `no_reading_bleed: true` (the render backs off 1s from the pinned
  in-phrase, into that gap).
- No such silence, or `ffmpeg` fails → `no_reading_bleed: false`.
- The 40s window absorbs Whisper's 10–15s timestamp drift; if the in-phrase
  itself sits outside it, widen the window rather than guessing.

Do NOT run any CLI stage, upload, or render. Discovery/judgment only.
