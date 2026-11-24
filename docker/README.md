# Deploying sermon-cut with Docker

One long-lived container runs the FastAPI dashboard, the job worker, and the
twice-daily discovery-watcher. `claude -p` judgment steps run headless under the
mounted Claude Code credentials, so a Claude subscription covers them. Put a
reverse proxy in front (nginx-proxy-manager below).

## Prerequisites (on the Docker host)

The checkout must be on `main` and carry the
**gitignored** runtime files (they are never in the image — they're bind-mounted):

- `config.toml` — church config
- `token.json` — long-lived YouTube OAuth token. Publish the Google Cloud OAuth
  app (or use an account that is a project member): a token from an app left in
  "Testing" expires after 7 days
- `client_secrets.json` — OAuth client
- `assets/*.png` — your church's branded intro/outro/ink cards
- `~/.claude/.credentials.json` + `~/.claude.json` — Claude Code, signed in

yt-dlp does **not** need to be fresh on the host anymore — the image owns it and
self-updates on every start.

## First deploy

```bash
cd sermon-cut                 # your checkout
git pull

# Seed mutable state (bind-mounting a missing file makes Docker create a
# DIRECTORY, which breaks the app). Only create if absent — never clobber a
# populated db.
mkdir -p runs
[ -e auto.db ] || python -m sermon_cut auto --help >/dev/null 2>&1 || true
[ -e auto.db ] || touch auto.db   # empty file is fine; the app creates the schema

cd docker
docker compose up -d --build
docker compose logs -f sermon-cut   # watch startup; Ctrl-C to detach
```

Smoke checks:

```bash
docker compose exec sermon-cut claude -p "reply OK" \
    --model claude-haiku-4-5-20251001            # signed in, no prompt
docker compose exec sermon-cut yt-dlp --version  # a nightly build, on PATH
curl -s localhost:8890/ | head                   # queue HTML
ls -la ../runs ../auto.db                         # owned by you (UID-1000 mapping)
```

## nginx-proxy-manager

Add a **Proxy Host**:

- Domain: a name on your private network, e.g. `sermoncut.example.net`
- Scheme: `http`
- Forward host: the Docker host's name (or its LAN IP; from the NPM container,
  `host.docker.internal` if NPM can't resolve the hostname)
- Forward port: `8890`
- Block common exploits: on
- (Optional) TLS via NPM's Let's Encrypt / your internal CA

Private/LAN only — never expose this publicly (the app has no auth by design).

## Redeploy after a code change

```bash
cd sermon-cut && git pull
cd docker && docker compose up -d --build
```

State (`runs/`, `auto.db`, the whisper model cache, credentials) survives — it all
lives in mounts/named volumes, not the image.

## Watcher schedule

The container scans the channel at **local noon and midnight** (`TZ` =
`America/Chicago`), enqueuing new Sunday services as `discovered`. It never
processes — you hit **Process** in the UI. Adjust the hours in
`sermon_cut/auto/schedule.py` (`fire_hours`) if needed.
