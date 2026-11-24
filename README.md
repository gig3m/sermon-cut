# sermon-cut

Extract the sermon from a 2-hour church livestream archive, add an intro card,
and upload it to YouTube as Unlisted for review. Runs locally; no cost beyond a
Claude subscription.

There are two ways to run it: interactively with the Claude Code skills below,
or as a Docker service with a web dashboard that watches the channel and
queues each new Sunday service for one-click processing
([docker/README.md](docker/README.md)).

## How it works

Two passes so the full 2-hour video is never moved:
1. **Discovery** — download audio only, transcribe locally (faster-whisper),
   Claude reads the transcript to find the sermon and draft title/description/
   chapters.
2. **Render** — download only the sermon window (`yt-dlp --download-sections`),
   prepend an intro card, normalize audio, upload Unlisted.

Run it with the `/sermon-cut` skill (URL or `latest`; `--yes` to skip the confirm
gate; `--speaker "Name"` for guest weeks).

Once a sermon is rendered, the `/sermon-shorts <run-id>` skill can cut vertical
(9:16) YouTube Shorts from it — captioned, subject-tracked, branded, uploaded
Unlisted. Candidate moments are Claude's judgment call; a human always picks
which ones render. See `CLAUDE.md` for details.

## One-time setup

1. **System tools:** `yt-dlp` and `ffmpeg` from your package manager
   (`pacman -S yt-dlp ffmpeg`, `apt install ffmpeg` plus `pipx install yt-dlp`,
   or `brew install yt-dlp ffmpeg`). An NVIDIA GPU makes transcription much
   faster but isn't required.
2. **Python env:**
   ```bash
   cd sermon-cut
   python3 -m venv .venv
   .venv/bin/python -m pip install -U pip -e .
   ```
   Pulls in everything, including `opencv-python-headless` + `numpy` used only
   by the Shorts subject-tracking stage — no extra install step needed.
3. **Config:** `cp config.example.toml config.toml` and fill in `church_name`,
   `youtube_channel_id`, `default_speaker`, `canva_template_id`,
   `description_footer`.
4. **YouTube upload auth (free):**
   - Create a Google Cloud project; enable **YouTube Data API v3**.
   - Create an **OAuth client ID → Desktop app**; download it as
     `client_secrets.json` into the project root.
   - First upload opens a browser to authorize your channel; the refresh token
     is cached to `token.json`. Daily quota (10,000 units) covers a ~1,600-unit
     weekly upload.
5. **Intro card:** drop `assets/intro_card.png` (1920x1080). Per-week dynamic
   Canva generation is a planned upgrade (see below).

## Running the pipeline manually (without the skill)

Each stage is a CLI subcommand writing into a run dir:
```bash
P=.venv/bin/python
$P -m sermon_cut fetch-audio "https://youtu.be/VIDEOID" --workdir runs/VIDEOID
$P -m sermon_cut transcribe --audio runs/VIDEOID/audio.m4a --workdir runs/VIDEOID
# author runs/VIDEOID/plan.json from the transcript, then:
$P -m sermon_cut validate-plan --plan runs/VIDEOID/plan.json --workdir runs/VIDEOID
$P -m sermon_cut clip "https://youtu.be/VIDEOID" --start 3000 --end 4500 --workdir runs/VIDEOID
$P -m sermon_cut intro --image assets/intro_card.png --workdir runs/VIDEOID
$P -m sermon_cut assemble --intro runs/VIDEOID/intro.mp4 --sermon runs/VIDEOID/sermon.mp4 --workdir runs/VIDEOID
$P -m sermon_cut upload --video runs/VIDEOID/final.mp4 --plan runs/VIDEOID/plan.json --workdir runs/VIDEOID
```

## Future seam: per-week Canva intro card

The `make_intro` stage takes a finished PNG. To generate a per-week card,
add a step before it that fills the Canva brand template
(`canva_template_id`) with the sermon title/speaker/date via the Canva MCP,
exports a PNG to `runs/<id>/intro_card.png`, and passes that to `intro`.
This was intentionally deferred from v1.

## Tests

`.venv/bin/python -m pytest -v` — all stages are unit-tested with injected
fakes; tests never hit the network, a GPU, or real binaries.
