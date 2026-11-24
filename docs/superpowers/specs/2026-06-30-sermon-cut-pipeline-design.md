# Sermon-Cut Pipeline — Design

**Date:** 2026-06-30
**Status:** Approved design, ready for implementation planning

## Goal

Turn a ~2-hour Sunday-service YouTube livestream archive into a polished
20–30 minute sermon video — intro card on the front, clean metadata — uploaded
back to YouTube as **Unlisted** for the user to review and publish.

Fully automatable, hands-off-capable, running entirely on the user's Mac with
free tooling. No cost beyond the Claude Max subscription.

## Core constraints

- **No new recurring cost.** Local tools only (`yt-dlp`, `faster-whisper`,
  `ffmpeg`) plus the free YouTube Data API quota and the already-connected Canva
  account.
- **Minimize bandwidth/CPU.** Never move or re-encode the full 2-hour video.
  Discover on cheap audio; render only the sermon window.
- **Tools + skill model.** A `/sermon-cut` skill orchestrates a handful of
  small, single-purpose, independently-testable scripts. Claude supplies the
  judgment (finding the sermon, drafting metadata); scripts do the mechanical
  work.

## Key design decision: two passes

The pipeline never downloads the whole 2-hour 1080p video.

1. **Discovery pass (cheap):** download audio only → transcribe locally → Claude
   reads the timestamped transcript to locate the sermon and draft metadata.
2. **Render pass (targeted):** download *only* the sermon window of the video
   via `yt-dlp --download-sections`, build the intro, concat, upload.

## Data flow

```
/sermon-cut <youtube-url | "latest"> [--yes] [--speaker "Name"]
        │
        ▼
[1] fetch_audio   yt-dlp → audio-only .m4a of the full service
        │
        ▼
[2] transcribe    faster-whisper (medium) → timestamped transcript (.json + .srt)
        │
        ▼
[3] find_sermon   Claude reads transcript → { start, end, title, speaker, date,
        │         description, chapters }   (the skill's reasoning, not a script)
        │
        │  ── CONFIRM GATE (on by default; --yes skips) ──
        │     show detected start/end + drafted title/speaker; wait for OK
        ▼
[4] clip_video    yt-dlp --download-sections "*start-end" (+padding) → sermon.mp4
        │
        ▼
[5] make_intro    Canva MCP fills brand template (title/speaker/date) → export PNG
        │         → ffmpeg → intro.mp4 (~5s, matched resolution/fps/audio)
        ▼
[6] assemble      ffmpeg concat intro.mp4 + sermon.mp4, normalize audio → final.mp4
        │
        ▼
[7] upload        YouTube Data API → Unlisted, with title/description/chapters
                  → prints the video URL for the user to review and publish
```

## Components

Each script is single-purpose, takes explicit inputs, writes explicit outputs to
a per-run working directory, and is runnable/testable on its own.

| # | Component | Type | Input | Output | Depends on |
|---|-----------|------|-------|--------|------------|
| 1 | `fetch_audio` | script | YouTube URL (or "latest" → channel lookup) | `audio.m4a`, `source.info.json` | yt-dlp |
| 2 | `transcribe` | script | `audio.m4a` | `transcript.json`, `transcript.srt` | faster-whisper |
| 3 | `find_sermon` | Claude reasoning | `transcript.json` | `plan.json` (start/end/title/speaker/date/description/chapters) | — |
| 4 | `clip_video` | script | URL, start, end, padding | `sermon.mp4` | yt-dlp, ffmpeg |
| 5 | `make_intro` | script + Canva MCP | title/speaker/date | `intro.mp4` | Canva MCP, ffmpeg |
| 6 | `assemble` | script | `intro.mp4`, `sermon.mp4` | `final.mp4` | ffmpeg |
| 7 | `upload` | script | `final.mp4`, metadata | YouTube video URL | YouTube Data API |

### The skill (`/sermon-cut`)

Orchestrates 1→7 in order. Holds the only "judgment" step (3) inline. Manages
the per-run working directory, the confirm gate, and `--yes` / `--speaker`
flags. Reports the final Unlisted URL.

## Parameters (decided)

- **Source:** YouTube livestream archive (pull via yt-dlp).
- **Find sermon:** auto-detect from transcript.
- **Confirm gate:** on by default, skippable with `--yes`.
- **Whisper model:** `medium` (good accuracy, ~5–12 min for 2hr on Apple Silicon).
- **Intro card:** per-week dynamic text (title/speaker/date) via a Canva brand
  template, exported per run.
- **Speaker name:** default preacher from config; `--speaker "Name"` override for
  guests. The confirm gate also surfaces it for correction.
- **Upload:** Unlisted; user reviews and publishes manually.
- **Bonus:** Claude drafts YouTube title, description, and chapter markers from
  the transcript.

## Config (`config.toml`, set once at setup)

- `church_name`, `youtube_channel_id`
- `default_speaker` — assumed preacher when `--speaker` not passed
- `canva_template_id` — the brand template to fill for the intro
- `intro_seconds` (default 5)
- `clip_padding_seconds` (default 8 — pre/post padding around the detected cut)
- `whisper_model` (default `medium`)
- `description_footer` — boilerplate appended to every YouTube description
  (service times, links, etc.)

## One-time setup (free)

1. Install tooling: `brew install yt-dlp ffmpeg`; `pip install faster-whisper`
   (Apple-Silicon build).
2. **YouTube upload auth:** create a Google Cloud project, enable the YouTube
   Data API v3, create an **OAuth client (Desktop app)**, run a one-time auth
   flow to mint a refresh token stored locally (`~/.config/sermon-cut/`). Free;
   ~10 minutes; done once. Daily quota (10,000 units) easily covers a weekly
   ~1,600-unit upload.
3. **Canva:** create the intro brand template with named fields for title /
   speaker / date; record its `canva_template_id` in config.
4. Fill in `config.toml`.

## Error handling & safety

- **Working dir per run:** all artifacts under `runs/<video-id>/`; idempotent —
  re-running resumes from existing artifacts (skip transcribe if transcript
  exists, etc.).
- **Confirm gate** is the primary guard against a mis-cut sermon reaching even
  Unlisted; Unlisted-then-publish is the second guard.
- **Sermon-detection sanity checks:** reject a detected segment outside a sane
  length band (e.g. <10 or >45 min) → fall back to confirm gate even under
  `--yes`, with a clear warning.
- **Upload is the last and only outbound step.** Everything before it is local
  and reversible. Nothing is ever uploaded Public by this pipeline.
- **Quota/credential failures** surface a clear message; the rendered `final.mp4`
  remains on disk for a manual upload retry.

## Out of scope (YAGNI for v1)

- Fully autonomous cron/channel-watch trigger (clean seam left; easy to add on
  top once the manual flow is trusted).
- Burned-in captions / multi-language.
- Lower-thirds or any editing beyond the single intro card.
- Thumbnail generation (can revisit after v1).

## Future seams

- A scheduled trigger that polls the channel for new completed livestreams and
  invokes the same pipeline with `--yes`.
- Promote the Canva card to also generate a YouTube thumbnail.
- Optional caption upload (we already produce an `.srt`).
