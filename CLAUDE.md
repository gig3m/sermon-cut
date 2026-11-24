# sermon-cut

Local, no-recurring-cost pipeline that extracts the 20–30 min sermon from a
~2-hour Sunday-service YouTube livestream, prepends an intro card, and uploads
it back to YouTube as **Unlisted** for review before publishing.

**Hard constraint:** no cost beyond a Claude Max subscription. Everything runs
locally with free tools (yt-dlp, faster-whisper, ffmpeg) plus the free YouTube
Data API quota and a Canva connection. Never add a paid/recurring dependency.

## How to run it

Driven by the **`/sermon-cut`** skill (`.claude/skills/sermon-cut/SKILL.md`):

```
/sermon-cut <youtube-url | latest> [--yes] [--speaker "Name"]
```

- `latest` resolves the channel's newest upload; `--yes` skips the confirm gate;
  `--speaker "Name"` overrides the default preacher for guest weeks.
- The skill orchestrates the CLI stages and does the one judgment step itself —
  reading the transcript to locate the sermon and draft metadata.

On-demand, after a sermon run exists, the **`/sermon-shorts`** skill
(`.claude/skills/sermon-shorts/SKILL.md`) cuts vertical YouTube Shorts from it:

```
/sermon-shorts <run-id>
```

Runs **unattended**: Claude auto-selects up to 3 of the strongest standalone
moments (quality-gated — 0 is a valid outcome), renders them, and uploads each
**Unlisted**. There is no candidate-picking prompt; the Unlisted upload is the
review point (the user reviews and publishes on YouTube).

Separately, the **`/livestream-thumbs`** skill
(`.claude/skills/livestream-thumbs/SKILL.md`) sets branded ink title-card
thumbnails on the channel's **full Sunday livestreams** (the `/streams` tab — NOT
the extracted sermons). It scans the channel, detects which Sunday services still
are not wearing OUR dated card. Detection is an image-distance match against
the card we would render (`card_distance`/`is_our_card`, 64×36 grayscale mean
abs diff, threshold 1.0 — measured: same card re-encoded by YouTube 0.08, a
different date 2.8, the placeholder 8.3, an auto frame 74.7). 🚨 The earlier
"dark + near-grayscale == branded" heuristic was WRONG: the church sets a
generic dark "Livestream" placeholder at stream time, which passed as branded,
so 2026-09-06's stream was skipped. No custom-vs-auto API flag exists. Renders
`"<date> Livestream"` on `assets/intro_bg_ink.png`, and sets it. Runs **unattended**; never overwrites an
existing card; needs no re-auth (`youtube.upload` covers `thumbnails.set`) and
never downloads the full video. `--dry-run` scans without setting.

The CLI underneath (each stage is independently runnable):

```
.venv/bin/python -m sermon_cut <subcommand> --workdir runs/<video-id> [...]
```
Subcommands: `fetch-audio`, `resolve-latest`, `transcribe`, `validate-plan`,
`clip`, `intro`, `outro`, `assemble`, `upload`, `shorts-validate`, `shorts-clip`,
`shorts-render`, `shorts-upload`, `livestream-thumbs`.

## Architecture (two passes — never moves the full 2hr video)

1. **Discovery (cheap):** `fetch-audio` (yt-dlp, audio-only) → `transcribe`
   (faster-whisper → `transcript.json`/`.srt`) → Claude reads the transcript and
   writes `plan.json` (start/end/title/speaker/date/description/chapters) →
   `validate-plan` (rejects sermons outside a 10–45 min sanity band).
2. **Render (targeted):** `clip` (yt-dlp `--download-sections` grabs ONLY the
   sermon window + padding) → `intro` (Pillow composites the sermon
   title/speaker/date onto `assets/intro_bg.png` → ffmpeg loops it to a short
   clip) → `outro` (loops the static `assets/outro_card.png` into a bumper) →
   `assemble` (ffmpeg `xfade`/`acrossfade` cross-dissolves + fade to/from black
   + loudnorm) → `upload` (YouTube Data API, Unlisted). The uploaded title is
   `plan.title — Month D, YYYY`; keep `plan.title` clean (date is composed in at
   upload + on the card). `upload` also sets `title_card.png` as the custom
   thumbnail (else YouTube auto-picks a frame, often the intro→sermon dissolve).

Per-run artifacts live under `runs/<video-id>/`. **`runs` is a symlink to
`/mnt/scratch/sermon-cut-runs`** on the host's WD Purple scratch HDD (moved
2026-09-16 to keep ~1 GB/week of video off the QLC OS NVMe). The compose file
bind-mounts `../runs`, which Docker resolves
through the symlink, so nothing else changed. The gitignore rule is `/runs`
(no trailing slash) because `runs/` only matches a directory, not a symlink.
Files:
`audio.m4a`, `source.info.json`, `transcript.json/.srt`, `plan.json`,
`sermon.mp4`, `intro.mp4`, `outro.mp4`, `final.mp4`. Every stage is **idempotent**
— it skips work whose output artifact already exists, so a re-run resumes.

Module map (`sermon_cut/`): `config.py`, `timefmt.py`, `fetch_audio.py`,
`transcribe.py`, `plan.py`, `clip_video.py`, `title_card.py`, `intro.py`,
`assemble.py`, `upload.py`, `youtube_auth.py`, `cli.py`, `__main__.py`,
`shorts_select.py`, `shorts_render.py`, `shorts_track.py`, `shorts_captions.py`,
`livestream_thumbs.py`.
`title_card.py` also builds the single-line ink livestream card
(`build_livestream_card`); `livestream_thumbs.py` holds the scan/detect logic
(`is_sunday_service`, `card_distance`, `is_our_card`, `find_candidates`,
`run`) with injectable enumerate/fetch/set for offline tests.
`title_card.py` builds the per-sermon title PNG (`build_title_card`); `intro.py`
`make_bumper` loops any still (dynamic intro card or static outro card) into a
bumper clip; `assemble.py` weaves intro→sermon→outro with cross-dissolves and
probes clip durations (`duration_probe`) to compute the transition offsets.

### Shorts (on-demand, per sermon run)

`/sermon-shorts <run-id>` cuts vertical (9:16) YouTube Shorts out of an
already-rendered `sermon.mp4` — no re-download of the source video, ever.
Claude auto-selects **up to 3** of the strongest standalone moments from the
sermon transcript (hook, self-contained arc, clean landing) into
`runs/<id>/shorts/candidates.json` — quality-gated (0–3; 0 is valid), with a
`context_integrity` filter that **excludes** any clip that would misrepresent the
sermon out of context (dropped, not uploaded, since nothing is human-vetted
before it lands). It runs **unattended** — the Unlisted upload is the review
point, not a pre-render pick gate. Selected candidates then run:

- `shorts-validate` — checks candidate length against the configured band
  (`target_min`/`target_max`, `hard_min`/`hard_max` in `[shorts]`).
- `shorts-clip` (`shorts_render.extract_window`) — stream-copies the padded
  window straight out of `sermon.mp4`.
- `shorts-render` — re-transcribes the window for word-level timestamps
  (`transcribe_words`), builds karaoke-style captions (`shorts_captions.py`:
  chunk → per-state PNG → qtrle alpha `overlay.mov`), tracks the speaker with
  OpenCV foreground detection to drive a smoothed 9:16 crop
  (`shorts_track.py`: `measure_centers`/`smooth_camera`/`crop_x_from_center`/
  `render_tracked_body`), then appends the branded vertical outro card and
  assembles with fades/dissolve (`shorts_render.assemble_short`).
- `shorts-upload` — uploads Unlisted, title `"<clip title> — <sermon title>"`,
  description includes `#Shorts` and a "Full sermon:" link back to the sermon's
  own `upload.json` receipt if it exists.

Adds `opencv-python-headless` and `numpy` as deps (tracking only; lazily
imported inside `shorts_track.py`, same pattern as the other heavy deps).
Crop geometry (`crop_w`/`crop_h`/`crop_y` in `[shorts]`) is tuned to this
church's fixed camera framing — re-tune if the camera or stage layout changes.
Every upload (sermon or short) writes an `upload.json` receipt
(`{video_id, url}`) into its workdir via `write_upload_receipt`.

## Invariants — do not break these

- **Privacy is always `unlisted`.** No code path uploads Public. `upload` is the
  only outbound action; everything before it is local and reversible.
- **Tests run with only pytest.** Heavy deps (faster_whisper, googleapiclient,
  google-auth*, PIL, cv2, numpy) and external binaries (yt-dlp, ffmpeg) are NEVER
  imported at module top-level — they're invoked via an injectable `runner`/client
  or imported lazily inside a function. Keep new stages the same way. (`title_card.py`
  keeps its top level stdlib-only; PIL is imported inside the draw functions;
  `shorts_track.py` imports cv2/numpy lazily inside its functions too.)
- **Discovery is audio-only; video download is windowed.** Don't fetch the full
  video anywhere except `clip`, which must stay `--download-sections`.
- **Transcription auto-detects its device — never hardcode one.**
  `transcribe.resolve_device()` picks `("cuda", "int8_float16")` when
  `ctranslate2.get_cuda_device_count() > 0`, else `("cpu", "int8")`, so the same
  image runs on a GPU-less host; `SERMON_CUT_WHISPER_DEVICE` /
  `SERMON_CUT_WHISPER_COMPUTE` override it. `ctranslate2` is imported lazily
  inside the probe, per the pytest-only invariant above.
- **`config.toml`'s `whisper_model` is the single source of truth for the model.**
  The `transcribe` subcommand reads it (`--model` still overrides, a missing
  config falls back to `medium`), so it governs both the sermon transcript and
  the shorts word-transcribe. It was `medium` and hardcoded in argparse before;
  bumping the config alone did nothing.

### GPU (added 2026-09-05 on an RTX 2080 SUPER; the box has an RTX 5070 / Blackwell since 2026-09-20)
Figures in this section are from the 2080 SUPER. Re-measured on the 5070 (2026-09-20, in this container):
medium/`int8_float16` 42.5x realtime at 1602 MiB, large-v3/`int8_float16` 27.7x.
`large-v3` on `int8_float16` — measured 9.9s for a 110s clip in the deployed
container (11.1x realtime incl. model load, peak 2.3 GB VRAM) versus 57.8s for
the old `medium`/CPU/`int8` path. 🚨 Three pieces must line up and **each fails
silently alone**: the device selection above; `nvidia-cublas-cu12` +
`nvidia-cudnn-cu12` pip wheels in the image with their lib dirs on
`LD_LIBRARY_PATH` (**CTranslate2 4.8 needs cuDNN 9; the host only has cuDNN 8 —
do not rely on the host libs**); and `capabilities: [gpu, compute, utility]` in
`docker/docker-compose.yml`, where **`compute` is required** — `utility` alone
gives NVML/monitoring but cannot run CUDA. **Verify with
`docker exec sermon-cut python -c "import ctranslate2;
print(ctranslate2.get_cuda_device_count())"` → `1` and `nvidia-smi` showing the
process holding VRAM mid-run — never with a clean log or an HTTP 200.**
Full detail: `docs/homelab-setup.md` §5.

**The GPU is shared with Ollama (2026-11-09).** exodon's `gemma4:12b` sits at
~9 GB of the 5070's 12 GB between requests, and whisper then dies with
`CUDA failed with error out of memory` (the run row only says exit status 1).
When `SERMON_CUT_OLLAMA_URL` is set (compose passes it through from `docker/.env`;
Ollama listens only on the LAN IP), `_default_model_factory` calls
`free_ollama_vram` before loading on CUDA: `keep_alive:0` for every model in
`/api/ps`, then polls `/api/ps` up to 120s, because a model mid-request is
acknowledged at once but stays resident until that request ends. Best-effort:
Ollama down or still busy → whisper tries anyway. It fails silently if the var
is missing, so verify with `docker exec sermon-cut printenv SERMON_CUT_OLLAMA_URL`.
Side effects: it can cut short another client's in-flight request, and a client
calling Ollama mid-transcription reloads its model (partly offloaded to CPU).

### Title policy (2026-09-06)
The uploaded title comes from THREE sources, resolved in `title_vision.resolve_title`:
a dashboard form override wins outright; otherwise the transcript judge
(`/sermon-auto`) ALWAYS drafts a descriptive title, and the slide reader
(`/sermon-read-title`, fed `title_context.json` = the draft + frames sampled at
3s…600s of `sermon.mp4`) replaces it ONLY on a `high`-confidence match written to
`slide_title.json`. 🚨 Why: the 2026-09-06 slide read "JESUS / Colossians 1–3" —
a series banner — and shipped as the title "Jesus"; and the slide was blank
until ~205s, so a single 3s frame saw nothing. A one/two-word heading is always
`low`. Verify a title decision by reading `slide_title.json`'s `note`, which
names the frame and the reasoning.

### Claude CLI drift in the image (2026-09-06)
The container's `claude` is baked at image build (`ARG CLAUDE_CODE_VERSION` in
the Dockerfile) while `~/.claude/settings.json` is MOUNTED from the host, so the
host's `model` setting must be one the container's CLI accepts. When the host
CLI updates and the model moves, every headless `claude -p` in the container
exits 1 with `API Error: 400 Claude Code X does not support this model` (only in
`docker logs`; the run row just says exit status 1 at stage `decide`). Fix: bump
the ARG, `docker compose build && up -d`, verify `docker exec sermon-cut claude
--version`, then Retry the run from the dashboard (discovery is idempotent, so
it resumes at decide).

## One-time setup (per machine — required before it can run end-to-end)

1. `brew install yt-dlp ffmpeg`
2. `python3 -m venv .venv && .venv/bin/pip install -U pip -e .`
3. `cp config.example.toml config.toml` and fill in `church_name`,
   `youtube_channel_id`, `default_speaker`, `canva_template_id`,
   `description_footer`.
4. Google Cloud: enable **YouTube Data API v3**, create an **OAuth client →
   Desktop app**, download it as `client_secrets.json` in the repo root. First
   upload opens a browser to authorize; the refresh token caches to `token.json`.
5. Drop `assets/intro_bg.png` (1920×1080 branded background — logo/name up top,
   clear zone below for the sermon text). `assets/fonts/Lora.ttf` + `Nunito.ttf`
   are vendored (OFL). A static `assets/intro_card.png` is only needed for the
   fallback path.

Gitignored (never committed): `config.toml`, `client_secrets.json`,
`token.json`, `runs/`, media files, `.venv/`.

## Tests

`.venv/bin/python -m pytest -q` — fully offline (no network, GPU, or real
binaries). Follow TDD for new work; keep the injectable-runner pattern.

## Gotchas from real runs (timing, ops, batch)

- **Whisper timestamps drift ~10–15s (non-uniformly) on ~2hr audio**, especially
  after long music gaps — the full-file `transcript.json` is unreliable for
  precise cuts. At render, RE-TRANSCRIBE THE CLIP (short → drift-free) to pin the
  sermon start and chapters. Map back: `source_time = clip_source_start + clip_time`.
- **Sermon in-point:** start on the preacher's first words; EXCLUDE the pre-sermon
  Scripture reading (usually read by a child), but INCLUDE an intro video if one
  plays (e.g. a remote greeting). The reading often butts against the greeting with
  no gap, so keep the front pad tight — clip wide, then trim.
- **Render trim workflow:** clip with a wide front pad (`--front-padding 20–25`) to
  survive drift → re-transcribe clip → stream-copy trim the front at the keyframe
  just before the target (`ffmpeg -ss <kf> -i clip -c copy -avoid_negative_ts
  make_zero`; keyframes land ~every 4.17s, so a ~2–4s silent lead is typical) → set
  `plan.start = trimmed_clip_source_start + front_padding_seconds` and re-pin
  chapters (absolute source seconds).
- **Verify every render:** `ffprobe` the final for duration AND a valid moov atom —
  a killed/interrupted assemble leaves a truncated file ("moov atom not found").
  Extract an opening frame to confirm it opens on the preacher, not the reading.
- **Don't run 3+ heavy ffmpeg/whisper jobs concurrently** — assembles get killed
  mid-write and corrupt the output. Serialize the encodes; one whisper re-transcribe
  may overlap one ffmpeg.
- **`clip` (`--download-sections` + `--force-keyframes-at-cuts`) can be brutally slow
  or hang — don't wait it out; use the full-download-then-trim fallback.** Two
  compounding causes: (a) `--force-keyframes-at-cuts` re-encodes the whole window at
  ~0.3–0.5× realtime (~50 min for a 24-min sermon); (b) the section fetch is done by
  ffmpeg hitting the `videoplayback` URL directly, which skips yt-dlp's n-signature
  deciphering and gets throttled to ~1× playback (≈ the video bitrate), and ffmpeg
  has no read timeout so a stalled connection hangs forever. Fast, reliable path that
  keeps A/V synced: **`yt-dlp -f <bestvideo> -o full.mp4` (native downloader — deciphers
  n-sig, runs at multi-MB/s)**, then locally `ffmpeg -c copy` mux with the
  already-downloaded `audio.m4a` and window-cut with `ffmpeg -ss <start> -i muxed -t
  <dur> -c copy -avoid_negative_ts make_zero` (single input seek → keyframe-safe, no
  desync; the ~2–4s pre-greeting lead from keyframe snapping is fine given the gap
  before the sermon). This downloads the full video (a deliberate exception to the
  windowed-download invariant, same as the post_live workaround) but finishes in
  ~2 min vs stalling. **Do NOT** raw-`aria2c` the `-g` URL: parallel range requests
  on YouTube's `mp4_dash` single-file format return corrupt frames (NAL errors)
  even though the file size/duration look right.
- **Batch multiple services:** discovery (fetch-audio + transcribe + draft each
  plan, one analysis subagent per transcript) → review all plans at once → then
  render+upload each with encodes serialized.
- **ffmpeg concat demuxer resolves relative paths against the concat file's own
  directory, not cwd.** `shorts_captions.render_overlay` writes cwd-relative
  paths into `concat.txt`; when `workdir` is itself a relative path, ffmpeg
  double-prefixes and fails with "No such file or directory" — only shows up
  running the real binary from a nested workdir, not under the mocked-runner
  unit tests. Fixed by writing `.resolve()`d (absolute) paths into the concat
  file.
- **Shorts captions must clamp to `[0, dur]`.** The tracking window carries ±pad,
  so `words.json` (window-relative) has words before the clip start (negative
  time) and past the end. If `build_caption_timeline` doesn't clamp, `overlay.mov`
  spans the whole padded window; the audio is `-ss/-t`-trimmed to `dur` but
  ffmpeg's `overlay` (default `eof_action=repeat`) then freezes the last video
  frame and stretches the clip to window length, desyncing captions from audio
  and burying the hook behind ~5s of lead-in. Caught only by watching a Short
  whose moment is deep in the sermon (the Task-15 smoke used the sermon-opening
  hook, where the lead-in is still the preacher, so it slipped through). Fixed by
  clamping the timeline and blanking words that begin before the clip.
- **Shorts pin the body cut verbatim at `source_start`/`source_end` — no
  word-snapping.** Picking those seconds off the segment-level `transcript.json`
  reliably opens mid-sentence (cutting off the hook) or overshoots the landing
  into the next sentence. Pin them at word level first: after `shorts-clip`,
  `transcribe_words` the window (or read the `words.json` the render produces),
  put `source_start` a hair before the first hook word and `source_end` a hair
  after the last punch word (in the gap before the next sentence). Changing the
  boundaries invalidates `window.mp4`/`words.json`/`overlay.mov`/`body.mp4`/
  `caps/`/`short.mp4` — all idempotent-by-existence, so delete them before
  re-running `shorts-clip` + `shorts-render`. Verify each finished short by
  transcribing its own audio (first/last words).
- **Run `shorts-render` (and `assemble`) as a tracked background job, not
  `nohup … &` under a `while pgrep; do sleep; done` poll-loop.** When that poll
  Bash call hits its own timeout it SIGTERMs its process group and kills the
  nohup'd child too — ffmpeg dies mid-mux ("Immediate exit requested" / "Error
  muxing a packet"), leaving a corrupt output that then blocks the idempotent
  re-run until deleted.

## Status / what's deferred

- Built via TDD, merged to `main` (104 tests passing).
- **Shipped:** dynamic per-sermon title card (`title_card.py`, Pillow, auto-fit +
  fail-fast `TitleTooLongError`); intro→sermon→outro weave with `xfade`/`acrossfade`
  cross-dissolves + fade to/from black (`assemble.py`); asymmetric
  `front_padding_seconds`/`back_padding_seconds`; drift-corrected chapter offsets;
  custom thumbnails on upload; `upload.json` receipt written on every upload
  (sermon and shorts); the Shorts pipeline (`/sermon-shorts`, see Architecture
  above) — human-gated selection, captions, subject tracking, branded vertical
  outro, Unlisted upload with `#Shorts` + full-sermon link. Verified end-to-end
  on real footage (`runs/CahMfXJcFCE`). Specs in `docs/superpowers/specs/`.
- Branded art (`assets/intro_bg.png`, `assets/outro_card.png`) is produced once via
  Claude Design; `canva_template_id` is unused.
- **OAuth caveat:** the app stays in Testing mode, so the refresh token can expire
  after ~7 days — a re-auth may be needed between weeks. `youtube.upload` scope
  covers both `videos.insert` and `thumbnails.set`; custom thumbnails need a
  phone-verified channel.
- Deferred Minor polish: friendlier error on unknown config keys, an
  `encoding="utf-8"` sweep on file writes, and the currently-unused `plan.date`
  field.

Design spec and implementation plan: `docs/superpowers/`.
