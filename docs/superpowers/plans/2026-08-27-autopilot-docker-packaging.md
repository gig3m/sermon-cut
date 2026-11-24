# Autopilot Docker Packaging (Phase 4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans
> to implement this plan task-by-task. Much of this plan is infrastructure
> authoring + interactive deploy on the Docker host, which cannot be
> delegated to a subagent running against the repo on the dev machine. Steps use checkbox
> (`- [ ]`) syntax for tracking.

**Goal:** Package the built autopilot (dashboard + worker + discovery-watcher +
sermon_cut pipeline + `claude -p` judgment) into one long-lived Docker container
on the Docker host, fronted by nginx-proxy-manager at `sermoncut.example.net`, with a
twice-daily discovery scan and persistent volumes.

**Architecture:** A single `python:3.12-slim` image carrying the package, ffmpeg,
node + the Claude Code CLI, and yt-dlp (self-updated on start). One container runs
uvicorn (FastAPI dashboard) + the background job worker + a scheduler thread that
scans the channel at local noon & midnight. Gitignored runtime files and mutable
state are bind/named volumes so redeploys are stateless. Runs as UID 1000 so
artifacts stay user-owned. `claude -p` authenticates off the mounted Max
credentials ($0) — proven by the Phase 0 spike.

**Tech Stack:** Docker + docker-compose, nginx-proxy-manager (existing), Python
3.12, FastAPI/uvicorn, Node 22 + `@anthropic-ai/claude-code`, ffmpeg, yt-dlp
nightly.

**Spec:** `docs/superpowers/specs/2026-08-25-autopilot-webui-design.md` (§
Architecture/deployment, § Phasing item 4). Phase 0 go/no-go is DONE — GO.

## Global Constraints

- **No recurring cost beyond Claude Max.** `claude -p` runs under Max with the
  mounted credentials and no `ANTHROPIC_API_KEY`. Never add a paid dependency.
- **Privacy is always Unlisted.** Packaging changes no upload path.
- **Tests run with only pytest, fully offline.** Heavy deps and binaries are never
  imported at module top-level. The one new code module (scheduler) is pure-stdlib
  and its scheduling math is a pure, injectable-`now` function.
- **Container runs as UID/GID 1000:1000** so `runs/`, `auto.db`, and any files
  written into `~/.claude` stay owned by the host user on the host (root-in-container
  writes root-owned files — observed in the spike).
- **`~/.claude` is mounted read-WRITE** (not read-only as an earlier spec draft
  said): the Max OAuth token refreshes periodically and Claude writes state; a
  read-only mount eventually breaks auth.
- **The pipeline shells out to PATH `yt-dlp`** — the freshened yt-dlp must be on
  PATH inside the container, not only in a venv the pipeline doesn't use.
- **Timezone is `America/Chicago`** (church-local) so the watcher fires at the
  intended local noon/midnight; `tzdata` installed, `TZ` set in compose.

---

### Task 1: Discovery-watcher scheduler

Adds a scheduler thread that scans the channel at local noon and midnight,
reusing the existing `watcher.scan_channel`. The scheduling math is a pure
function with an injected `now`; the loop and thread wiring are thin.

**Files:**
- Create: `sermon_cut/auto/schedule.py`
- Test: `tests/auto/test_schedule.py`
- Modify: `dashboard/server.py` (start the scheduler thread in `build`/`main`)

**Interfaces:**
- Consumes: `watcher.scan_channel(*, enumerate_streams, conn, now) -> list[str]`;
  `status.connect(db_path)`.
- Produces: `seconds_until_next_fire(now: datetime, fire_hours=(0, 12)) -> float`
  (seconds from `now` to the next local wall-clock hour in `fire_hours`, always
  > 0); `run_scheduler(*, enumerate_streams, db_path, now_fn, sleep, stop=None,
  fire_hours=(0, 12))` (loop: sleep until next fire, open a thread-local conn, call
  `scan_channel`, repeat; `stop`/`sleep` injectable for tests).

- [ ] **Step 1: Write the failing test for the scheduling math**

```python
# tests/auto/test_schedule.py
from datetime import datetime
from sermon_cut.auto.schedule import seconds_until_next_fire


def test_before_noon_fires_at_noon():
    now = datetime(2026, 8, 27, 9, 0, 0)  # 09:00
    assert seconds_until_next_fire(now) == 3 * 3600  # -> 12:00


def test_after_noon_fires_at_midnight():
    now = datetime(2026, 8, 27, 15, 0, 0)  # 15:00
    assert seconds_until_next_fire(now) == 9 * 3600  # -> 24:00 (next midnight)


def test_exactly_on_a_fire_hour_goes_to_the_next_one():
    now = datetime(2026, 8, 27, 12, 0, 0)  # exactly noon
    # never returns 0 — advance to the next fire (midnight)
    assert seconds_until_next_fire(now) == 12 * 3600


def test_just_before_midnight():
    now = datetime(2026, 8, 27, 23, 59, 0)
    assert seconds_until_next_fire(now) == 60
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/auto/test_schedule.py -v`
Expected: FAIL (module/function not defined).

- [ ] **Step 3: Implement `seconds_until_next_fire`**

```python
# sermon_cut/auto/schedule.py
from __future__ import annotations

from datetime import datetime, timedelta


def seconds_until_next_fire(now: datetime, fire_hours=(0, 12)) -> float:
    """Seconds from `now` to the next wall-clock hour in `fire_hours`.

    Always strictly positive — landing exactly on a fire hour advances to the
    next one, so a zero-length sleep can never spin the loop.
    """
    hours = sorted(fire_hours)
    for h in hours:
        candidate = now.replace(hour=h, minute=0, second=0, microsecond=0)
        if candidate > now:
            return (candidate - now).total_seconds()
    # past the last fire hour today -> first fire hour tomorrow
    first = now.replace(hour=hours[0], minute=0, second=0, microsecond=0)
    return (first + timedelta(days=1) - now).total_seconds()
```

- [ ] **Step 4: Run it to verify it passes**

Run: `.venv/bin/python -m pytest tests/auto/test_schedule.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Write the failing test for the loop**

```python
# append to tests/auto/test_schedule.py
from sermon_cut.auto.schedule import run_scheduler


def test_run_scheduler_scans_each_cycle_until_stopped(tmp_path):
    from sermon_cut.auto import status

    db = tmp_path / "auto.db"
    status.connect(db).close()  # create schema

    streams = [("vidA", "Sunday Morning Worship 8-23-2026")]
    calls = {"n": 0}

    def fake_sleep(_secs):
        calls["n"] += 1
        if calls["n"] >= 2:
            stop.set()

    import threading
    stop = threading.Event()
    run_scheduler(
        enumerate_streams=lambda: streams,
        db_path=db,
        now_fn=lambda: datetime(2026, 8, 27, 9, 0, 0),
        sleep=fake_sleep,
        stop=stop,
    )
    # first cycle enqueued the Sunday service
    conn = status.connect(db)
    assert status.get_run(conn, "vidA") is not None
```

- [ ] **Step 6: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/auto/test_schedule.py::test_run_scheduler_scans_each_cycle_until_stopped -v`
Expected: FAIL (`run_scheduler` not defined).

- [ ] **Step 7: Implement `run_scheduler`**

```python
# append to sermon_cut/auto/schedule.py
from . import status, watcher


def run_scheduler(*, enumerate_streams, db_path, now_fn, sleep,
                  stop=None, fire_hours=(0, 12)) -> None:
    """Sleep until the next fire hour, scan the channel, repeat.

    `sleep`/`now_fn`/`stop` are injected so the loop is testable offline.
    Each cycle opens its own sqlite connection (thread-local safety).
    """
    while stop is None or not stop.is_set():
        sleep(seconds_until_next_fire(now_fn(), fire_hours))
        if stop is not None and stop.is_set():
            break
        conn = status.connect(db_path)
        try:
            watcher.scan_channel(
                enumerate_streams=enumerate_streams, conn=conn, now=now_fn())
        finally:
            conn.close()
```

Note: `now_fn` returns a `datetime` for `seconds_until_next_fire`, but
`scan_channel` records `now` as the run timestamp. In tests both are the same
injected value; in `server.py` pass a `now_fn` returning `datetime.now()` (local)
and let `scan_channel` stringify as it already does via `upsert_run`. Confirm
`upsert_run` accepts a `datetime` the same way the worker's `_now` string is used
— if `scan_channel`/`upsert_run` expects an ISO string, wrap: pass
`now=now_fn().isoformat()` to `scan_channel` and keep the raw `datetime` only for
the sleep math. (Check `status.upsert_run` signature during implementation and
match it exactly.)

- [ ] **Step 8: Run the loop test to verify it passes**

Run: `.venv/bin/python -m pytest tests/auto/test_schedule.py -v`
Expected: PASS (5 tests).

- [ ] **Step 9: Wire the scheduler into the dashboard server**

Modify `dashboard/server.py`: in `build`, add a `start_scheduler()` alongside
`start_worker()` that launches a daemon thread running `run_scheduler` with
`sleep=time.sleep`, `now_fn=lambda: datetime.now()` (naive local time — the
container's `TZ` makes it church-local), `enumerate_streams` the same
`lambda: _ls_enumerate(channel_id)`, and `db_path`. Return both starters (or
start both inside `main`). In `main`, call `start_worker()` and
`start_scheduler()` before `uvicorn.run`. Keep the existing worker untouched.

```python
# dashboard/server.py — inside build(), after start_worker is defined
import time
from sermon_cut.auto import schedule

def start_scheduler():
    def _run():
        schedule.run_scheduler(
            enumerate_streams=lambda: _ls_enumerate(channel_id),
            db_path=db_path,
            now_fn=lambda: datetime.now(),   # local; TZ set by container
            sleep=time.sleep,
        )
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t

return app, start_worker, start_scheduler
```

Update `main()` to unpack three values and start both threads. Update the existing
`build()` unpack sites — grep for `build(` (currently `dashboard/server.py:main`
and any dashboard test that calls `build`). The route-level tests use
`create_app` directly and are unaffected; only `build` callers change.

- [ ] **Step 10: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (all prior tests + 5 new). Fix any `build()` unpack breakage.

- [ ] **Step 11: Commit**

```bash
git add sermon_cut/auto/schedule.py tests/auto/test_schedule.py dashboard/server.py
git commit -m "feat(autopilot): twice-daily discovery-watcher scheduler thread"
```

---

### Task 2: Container image (Dockerfile + entrypoint + .dockerignore)

Builds the image that carries the whole pipeline and the Claude CLI, and an
entrypoint that freshens yt-dlp (it rots against YouTube) before launching.

**Files:**
- Create: `Dockerfile`
- Create: `docker/entrypoint.sh`
- Create: `.dockerignore`

**Interfaces:**
- Produces: an image whose default command runs `docker/entrypoint.sh`, which
  self-updates yt-dlp then `exec`s `python -m dashboard.server` (uvicorn on 8890 +
  worker + scheduler). WORKDIR `/app` holds the repo (including `.claude/skills/`
  so `claude -p` finds `/sermon-auto`). HOME `/home/appuser` (writable by UID
  1000) receives the `~/.claude` mount.

- [ ] **Step 1: Write `.dockerignore`**

Keep the build context small and never bake secrets/artifacts into the image:

```
.venv/
runs/
*.mp4
*.m4a
*.db
token.json*
client_secrets.json
config.toml
.git/
__pycache__/
*.pyc
experiments/
assets/_pre_coc_backup/
docs/
```

Note: `config.toml`, `token.json`, `client_secrets.json`, and `assets/` are
mounted at runtime (Task 3), NOT baked in. `.claude/skills/` is NOT ignored — it
must ship in the image.

- [ ] **Step 2: Write the Dockerfile**

```dockerfile
FROM python:3.12-slim-bookworm

# System deps: ffmpeg for render, node for the Claude CLI, tzdata for the
# church-local watcher schedule, ca-certs + curl for the node install.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg tzdata ca-certificates curl gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Claude Code CLI (judgment steps run headless under Max)
RUN npm i -g @anthropic-ai/claude-code

# Non-root user matching the host user (UID 1000) so mounted files stay
# user-owned. HOME is writable so the ~/.claude mount + token refresh work.
RUN useradd -m -u 1000 -s /bin/bash appuser
ENV HOME=/home/appuser

WORKDIR /app
COPY . /app

# Install the package + dashboard extras + yt-dlp (on PATH — the pipeline shells
# to `yt-dlp`, not a venv). System-site install so PATH picks it up as appuser.
RUN pip install --no-cache-dir -U pip \
    && pip install --no-cache-dir -e ".[dashboard]" \
    && pip install --no-cache-dir -U --pre "yt-dlp[default]"

RUN chown -R appuser:appuser /app
USER appuser

EXPOSE 8890
ENTRYPOINT ["/app/docker/entrypoint.sh"]
```

- [ ] **Step 3: Write `docker/entrypoint.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail

# yt-dlp rots against YouTube — freshen to the latest nightly on every start.
# Best-effort: a network hiccup here must not stop the dashboard from serving.
pip install --user -U --pre "yt-dlp[default]" 2>&1 | tail -1 || \
    echo "yt-dlp self-update skipped (offline?)"

exec python -m dashboard.server
```

Make it executable in git: `chmod +x docker/entrypoint.sh` before committing.
(`--user` install lands in `$HOME/.local` which is writable by appuser and ahead
of the system yt-dlp on PATH; verify PATH ordering in Task 4 — if the system
yt-dlp shadows it, drop `--user` and rely on a writable site or a venv.)

- [ ] **Step 4: Commit**

```bash
chmod +x docker/entrypoint.sh
git add Dockerfile docker/entrypoint.sh .dockerignore
git commit -m "feat(autopilot): container image + yt-dlp-self-update entrypoint"
```

(No pytest gate — this task is verified by the real build in Task 4.)

---

### Task 3: Compose stack + deploy docs

Defines the runtime: volumes, UID, TZ, port, and the NPM front. Documents the
one-time host setup so a redeploy is repeatable.

**Files:**
- Create: `docker/docker-compose.yml`
- Create: `docker/README.md`

**Interfaces:**
- Consumes: the image from Task 2; host files `~/.claude`, `~/.claude.json`,
  `config.toml`, `token.json`, `client_secrets.json`, `assets/` on the Docker host.
- Produces: a stack whose one service exposes 8890 on the host for NPM to proxy.

- [ ] **Step 1: Write `docker/docker-compose.yml`**

```yaml
services:
  sermon-cut:
    build:
      context: ..
      dockerfile: Dockerfile
    image: sermon-cut:latest
    container_name: sermon-cut
    user: "1000:1000"
    environment:
      TZ: America/Chicago
    ports:
      - "8890:8890"          # NPM proxies sermoncut.example.net -> here
    volumes:
      # Max credentials (read-write: OAuth token refreshes) + CLI config
      - ${HOME}/.claude:/home/appuser/.claude
      - ${HOME}/.claude.json:/home/appuser/.claude.json
      # Gitignored runtime config/auth/assets (bind, live off the repo checkout)
      - ${HOME}/projects/sermon-cut/config.toml:/app/config.toml:ro
      - ${HOME}/projects/sermon-cut/token.json:/app/token.json
      - ${HOME}/projects/sermon-cut/client_secrets.json:/app/client_secrets.json:ro
      - ${HOME}/projects/sermon-cut/assets:/app/assets:ro
      # Mutable state — persist across redeploys
      - ${HOME}/projects/sermon-cut/runs:/app/runs
      - ${HOME}/projects/sermon-cut/auto.db:/app/auto.db
      - whisper-cache:/home/appuser/.cache
    restart: unless-stopped

volumes:
  whisper-cache:
```

Notes for implementation:
- `token.json` is mounted read-WRITE (the YouTube refresh token rotates).
- `auto.db` must exist on the host before first `up` (bind-mounting a missing file
  makes Docker create a *directory*). Task 4 seeds it.
- `assets/` and `client_secrets.json` are read-only (never mutated at runtime).

- [ ] **Step 2: Write `docker/README.md`**

Document, in order: (1) prerequisites on the Docker host (the gitignored files present
in `~/projects/sermon-cut`, `~/.claude` authorized under Max, nightly yt-dlp not
required on host anymore — the image owns it); (2) seed empty state:
`touch ~/projects/sermon-cut/auto.db` (or run one `python -m sermon_cut auto ...`
to create the schema) and ensure `runs/` exists; (3)
`cd ~/projects/sermon-cut/docker && docker compose up -d --build`; (4) the NPM
proxy host: new Proxy Host, domain `sermoncut.example.net`, scheme `http`, forward
host the Docker host (or the host's LAN IP / `host.docker.internal`), forward port
`8890`, block common exploits on, optional TLS via NPM; (5) logs
`docker compose logs -f sermon-cut`; (6) redeploy after a code change:
`git pull && docker compose up -d --build`.

- [ ] **Step 3: Commit**

```bash
git add docker/docker-compose.yml docker/README.md
git commit -m "feat(autopilot): compose stack + deploy docs (sermoncut.example.net)"
```

---

### Task 4: Build, deploy, and dry-run on the Docker host (interactive)

Not a subagent task — run interactively on the Docker host. Verifies the image
builds, the container serves, `claude -p` works in-container under Max, yt-dlp is
fresh on PATH, the watcher schedule is armed, and a real past service processes
end-to-end straight to Unlisted.

- [ ] **Step 1:** `git pull` on the Docker host (`~/projects/sermon-cut` → `main`)
  so the checkout has Phase 4. Confirm the gitignored runtime files are present:
  `config.toml`, `token.json`, `client_secrets.json`, `assets/*.png`.
- [ ] **Step 2:** Seed state: ensure `runs/` exists and `auto.db` exists as a file
  (`touch auto.db` only if absent — never clobber a populated one).
- [ ] **Step 3:** `cd docker && docker compose up -d --build`. Watch the build for
  wheel-build failures (faster-whisper/ctranslate2, opencv should use manylinux
  wheels; if a source build is triggered, add the needed `-dev`/build deps).
- [ ] **Step 4:** In-container smoke: `docker compose exec sermon-cut claude -p
  "reply OK" --model claude-haiku-4-5-20251001` → returns without prompting
  (Max auth via mount). And `docker compose exec sermon-cut yt-dlp --version` →
  a nightly build; confirm it's the PATH one the pipeline will call.
- [ ] **Step 5:** Dashboard reachable: `curl -s localhost:8890/` returns the queue
  HTML. Then wire the NPM proxy host (`sermoncut.example.net` → `:8890`) and confirm
  it loads in a browser on the LAN.
- [ ] **Step 6:** Confirm the scheduler armed and files stay user-owned:
  `docker compose logs sermon-cut` shows startup with no scheduler crash;
  `ls -la runs auto.db` on the host shows host-user ownership (UID-1000 mapping
  working), not `root`.
- [ ] **Step 7:** Full dry-run on a past service: Rescan (or enqueue a known past
  Sunday `video_id`), hit **Process**, and watch it go
  discovered → processing (stages) → done, producing an Unlisted sermon (and
  Shorts unless skipped). Verify the receipts/links in the Detail view. If it
  errors, use **Retry** and confirm it resumes at the first missing artifact.
- [ ] **Step 8:** Update the `homelab deployment` memory: Phase 4
  shipped, the stack lives at `~/projects/sermon-cut/docker`, NPM host
  `sermoncut.example.net`, watcher noon/midnight America/Chicago, redeploy =
  `git pull && docker compose up -d --build`.

---

## Self-Review

- **Spec coverage:** compose stack at the documented location ✓ (Task 3 — note the
  spec said `~/docker/sermon-cut/`; this plan uses `~/projects/sermon-cut/docker`
  so the compose sits beside the checkout it bind-mounts — a deliberate deviation,
  flagged in Task 3/4); single container running dashboard+watcher+runner ✓ (Tasks
  1–2); volumes incl. whisper cache + runs + auto.db ✓ (Task 3); yt-dlp self-update
  on start ✓ (Task 2); NPM proxy host ✓ (Task 3–4); claude -p under Max in
  container ✓ (Phase 0 spike + Task 4 re-verify); dry-run on a past service ✓
  (Task 4).
- **Placeholder scan:** none — every code/file step carries its full content; the
  interactive Task 4 steps are commands, not TODOs.
- **Type consistency:** `seconds_until_next_fire`/`run_scheduler` signatures match
  between Task 1's definition and the `server.py` wiring; `scan_channel` is called
  with its real keyword signature (`enumerate_streams`, `conn`, `now`); the `now`
  string-vs-datetime seam is called out explicitly in Task 1 Step 7 to match
  `status.upsert_run` at implementation time.
