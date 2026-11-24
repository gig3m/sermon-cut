# Autopilot Web UI (Phase 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A small private web dashboard on the homelab where Kyle sees discovered Sunday services, sets speaker/title/options and hits Process, and the box runs the whole pipeline straight-to-Unlisted — with live status, Unlisted links, Retry, and Rescan.

**Architecture:** A `queued` job state + `enqueue`/`claim_next` helpers on the existing `auto.db`. A background **worker** loop claims queued jobs and runs the Phase-2 orchestrator (`discover_decide` → `make_shorts`) with the real collaborators, recording status as it goes. A **FastAPI + Jinja2** app renders the queue/detail views and turns Process/Retry/Rescan buttons into DB writes; it never blocks on the work — the worker does it. Web and worker communicate only through `auto.db`.

**Tech Stack:** Python 3.12; FastAPI + Jinja2 + uvicorn (dashboard only — imported by the dashboard package, never by the pipeline); stdlib sqlite3/threading for the worker; pytest + FastAPI `TestClient` (httpx) offline. Reuses `sermon_cut/auto/{status,orchestrator,watcher}` and `sermon_cut/cli._ls_enumerate`.

**Spec:** `docs/superpowers/specs/2026-08-25-autopilot-webui-design.md` (Phase 3).

## Global Constraints

- Privacy is always `unlisted`; no path uploads Public (copied from spec).
- Discovery is audio-only; full-video download happens only inside `render_sermon` (copied from spec).
- The pipeline's heavy deps/binaries are NEVER imported at module top-level (copied from spec). FastAPI/Jinja/uvicorn are dashboard-only deps — the `sermon_cut` pipeline packages must not import them.
- Tests run with only pytest, fully offline — no network, GPU, or real binaries (copied from spec). Dashboard routes are tested with `TestClient` against a seeded temp `auto.db`; the worker with an injected orchestrate fn.
- Straight-to-Unlisted: Process runs the whole pipeline; the Unlisted upload is the only review point. No email.
- Web layer writes intent (a `queued` row); the worker does the work; both communicate only through `auto.db`.
- Private behind nginx-proxy-manager / the homelab network — no in-app auth.

## File Structure

- Modify `sermon_cut/auto/status.py` — add `"queued"` to `STATUSES`; add `enqueue` + `claim_next`.
- Create `sermon_cut/auto/worker.py` — `process_one` (claim + run orchestrator) + `serve_forever` loop.
- Create `dashboard/__init__.py`, `dashboard/app.py` (FastAPI app factory + routes), `dashboard/templates/base.html`, `queue.html`, `detail.html`.
- Create `dashboard/server.py` — starts uvicorn serving the app + launches the worker thread; wires the real channel enumerate.
- Modify `pyproject.toml` — add a `[project.optional-dependencies] dashboard` group (fastapi, uvicorn[standard], jinja2) so the pipeline install stays lean; add httpx to test deps.
- Tests: `tests/auto/test_queue.py`, `tests/auto/test_worker.py`, `tests/dashboard/__init__.py`, `tests/dashboard/test_app.py`.

---

## Task 1: Queue helpers (`enqueue` / `claim_next`)

**Files:**
- Modify: `sermon_cut/auto/status.py`
- Test: `tests/auto/test_queue.py`

**Interfaces:**
- Consumes: existing `status.py` (`STATUSES`, `upsert_run`, `get_run`, `Run`, `_row_to_run`).
- Produces:
  - `STATUSES` gains `"queued"`.
  - `enqueue(conn, video_id, *, now, speaker=None, title=None, options=None) -> None` — sets the run to `status="queued"` and stores `speaker`, `title_override=title`, `options` (via `upsert_run`).
  - `claim_next(conn, *, now) -> Run | None` — atomically transitions the oldest `queued` run to `status="discovering"` and returns it (as a `Run`); returns `None` if none queued. Single-worker; a `BEGIN IMMEDIATE` transaction around SELECT-oldest-then-UPDATE is sufficient.

- [ ] **Step 1: Write the failing tests**

```python
# tests/auto/test_queue.py
from sermon_cut.auto import status


def test_enqueue_sets_queued_with_params():
    conn = status.connect(":memory:")
    status.upsert_run(conn, "v", now="t0", status="discovered")
    status.enqueue(conn, "v", now="t1", speaker="Guest", title="T", options={"skip_shorts": True})
    r = status.get_run(conn, "v")
    assert r.status == "queued" and r.speaker == "Guest"
    assert r.title_override == "T" and r.options == {"skip_shorts": True}


def test_claim_next_returns_oldest_queued_and_marks_it():
    conn = status.connect(":memory:")
    status.upsert_run(conn, "a", now="t0", status="discovered")
    status.upsert_run(conn, "b", now="t0", status="discovered")
    status.enqueue(conn, "a", now="t1")
    status.enqueue(conn, "b", now="t2")            # b queued later
    claimed = status.claim_next(conn, now="t3")
    assert claimed.video_id == "a"                 # oldest queued first
    assert status.get_run(conn, "a").status == "discovering"
    assert status.get_run(conn, "b").status == "queued"


def test_claim_next_empty_returns_none():
    conn = status.connect(":memory:")
    assert status.claim_next(conn, now="t0") is None


def test_claim_next_does_not_return_same_row_twice():
    conn = status.connect(":memory:")
    status.upsert_run(conn, "a", now="t0", status="discovered")
    status.enqueue(conn, "a", now="t1")
    first = status.claim_next(conn, now="t2")
    second = status.claim_next(conn, now="t3")
    assert first.video_id == "a" and second is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/auto/test_queue.py -q`
Expected: FAIL (unknown status / AttributeError enqueue)

- [ ] **Step 3: Implement**

In `sermon_cut/auto/status.py`: add `"queued"` to `STATUSES`, then:

```python
def enqueue(conn, video_id, *, now, speaker=None, title=None, options=None) -> None:
    upsert_run(conn, video_id, now=now, status="queued",
               speaker=speaker, title_override=title,
               options=options if options is not None else {})


def claim_next(conn, *, now) -> "Run | None":
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT video_id FROM runs WHERE status='queued' ORDER BY updated_at, video_id LIMIT 1"
        ).fetchone()
        if row is None:
            conn.execute("COMMIT")
            return None
        vid = row["video_id"]
        conn.execute("UPDATE runs SET status='discovering', updated_at=? WHERE video_id=?",
                     (now, vid))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return get_run(conn, vid)
```

(If `enqueue` passing `title_override` as a keyword doesn't match `upsert_run`'s signature, use the actual keyword `upsert_run` exposes — read the existing `upsert_run` params; Phase 2 added `speaker`, `title_override`, `options`.)

- [ ] **Step 4: Run tests to verify they pass + full suite + commit**

Run: `.venv/bin/python -m pytest tests/auto/test_queue.py -q` then `.venv/bin/python -m pytest -q`

```bash
git add sermon_cut/auto/status.py tests/auto/test_queue.py
git commit -m "feat(auto): queued state + enqueue/claim_next job helpers"
```

---

## Task 2: Worker (`worker.py`)

**Files:**
- Create: `sermon_cut/auto/worker.py`
- Test: `tests/auto/test_worker.py`

**Interfaces:**
- Consumes: `status.claim_next`/`upsert_run`/`get_run` (Task 1); `orchestrator.discover_decide`/`make_shorts` (Phase 2).
- Produces:
  - `process_one(conn, *, root, now, orchestrate=None) -> str | None` — `claim_next`; if none → `None`. Else run `orchestrate(run, conn=conn, root=root, now=now)`; on exception, `status.upsert_run(status="error", error=str(e))` (do NOT re-raise — the worker keeps serving). Returns the claimed `video_id`. Default `orchestrate` = `_default_orchestrate` (below).
  - `_default_orchestrate(run, *, conn, root, now)` — calls `orchestrator.discover_decide(f"https://www.youtube.com/watch?v={run.video_id}", root=root, conn=conn, now=now, speaker=run.speaker, title=run.title_override, options=run.options, resolve_id=lambda u: run.video_id)`; then, unless `run.options.get("skip_shorts")` and only if the resulting status is `ready_for_review`, `orchestrator.make_shorts(run.video_id, root=root, conn=conn, now=now, options=run.options)`.
  - `serve_forever(conn, *, root, now_fn, interval=10.0, stop=None) -> None` — loop: `process_one`; if it returned None, sleep `interval` (interruptible via the `stop` threading.Event); exit when `stop.is_set()`. `now_fn()` supplies the ISO timestamp per iteration.

- [ ] **Step 1: Write the failing tests**

```python
# tests/auto/test_worker.py
import threading
from sermon_cut.auto import status, worker


def test_process_one_claims_and_runs_orchestrate():
    conn = status.connect(":memory:")
    status.upsert_run(conn, "v", now="t0", status="discovered", speaker="Bob",
                      title_override="T", options={"skip_shorts": True})
    status.enqueue(conn, "v", now="t1", speaker="Bob", title="T", options={"skip_shorts": True})
    seen = {}
    def orchestrate(run, *, conn, root, now):
        seen["vid"] = run.video_id; seen["speaker"] = run.speaker; seen["opts"] = run.options
        status.upsert_run(conn, run.video_id, now=now, status="ready_for_review",
                          sermon_url="https://youtu.be/X")
    vid = worker.process_one(conn, root="/tmp/x", now="t2", orchestrate=orchestrate)
    assert vid == "v" and seen["vid"] == "v" and seen["speaker"] == "Bob"
    assert status.get_run(conn, "v").sermon_url == "https://youtu.be/X"


def test_process_one_empty_queue_returns_none():
    conn = status.connect(":memory:")
    assert worker.process_one(conn, root="/tmp", now="t0", orchestrate=lambda *a, **k: None) is None


def test_process_one_records_error_and_keeps_serving():
    conn = status.connect(":memory:")
    status.upsert_run(conn, "v", now="t0", status="discovered")
    status.enqueue(conn, "v", now="t1")
    def boom(run, *, conn, root, now): raise RuntimeError("render blew up")
    vid = worker.process_one(conn, root="/tmp", now="t2", orchestrate=boom)   # must NOT raise
    assert vid == "v"
    r = status.get_run(conn, "v")
    assert r.status == "error" and "render blew up" in r.error


def test_serve_forever_stops_on_event():
    conn = status.connect(":memory:")
    stop = threading.Event()
    calls = {"n": 0}
    def orchestrate(run, **k): pass
    # empty queue → loop sleeps; set stop immediately so it exits fast
    def now_fn():
        calls["n"] += 1
        if calls["n"] >= 2: stop.set()
        return f"t{calls['n']}"
    worker.serve_forever(conn, root="/tmp", now_fn=now_fn, interval=0.01, stop=stop)
    assert stop.is_set()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/auto/test_worker.py -q`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Implement**

```python
# sermon_cut/auto/worker.py
from __future__ import annotations

import time

from . import status


def _default_orchestrate(run, *, conn, root, now):
    from . import orchestrator
    final = orchestrator.discover_decide(
        f"https://www.youtube.com/watch?v={run.video_id}", root=root, conn=conn, now=now,
        speaker=run.speaker, title=run.title_override, options=run.options,
        resolve_id=lambda u: run.video_id)
    if final == "ready_for_review" and not (run.options or {}).get("skip_shorts"):
        orchestrator.make_shorts(run.video_id, root=root, conn=conn, now=now, options=run.options)


def process_one(conn, *, root, now, orchestrate=None) -> str | None:
    orchestrate = orchestrate or _default_orchestrate
    run = status.claim_next(conn, now=now)
    if run is None:
        return None
    try:
        orchestrate(run, conn=conn, root=root, now=now)
    except Exception as e:  # keep the worker alive; the run is marked failed
        status.upsert_run(conn, run.video_id, now=now, status="error", error=str(e))
    return run.video_id


def serve_forever(conn, *, root, now_fn, interval: float = 10.0, stop=None) -> None:
    import threading
    stop = stop or threading.Event()
    while not stop.is_set():
        did = process_one(conn, root=root, now=now_fn())   # real _default_orchestrate
        if did is None:                                    # empty queue → sleep, re-check stop
            stop.wait(interval)
```

> `serve_forever` always uses the production `_default_orchestrate`. `test_serve_forever_stops_on_event` drives it against an empty queue (so `process_one` returns None and the loop sleeps then re-checks `stop`); its `now_fn` sets the event on the 2nd call so the loop terminates.

- [ ] **Step 4: Run tests + full suite + commit**

Run: `.venv/bin/python -m pytest tests/auto/test_worker.py -q` then `.venv/bin/python -m pytest -q`

```bash
git add sermon_cut/auto/worker.py tests/auto/test_worker.py
git commit -m "feat(auto): background worker — claim queued job, run orchestrator, record errors"
```

---

## Task 3: Dashboard read views (queue + detail)

**Files:**
- Modify: `pyproject.toml` (add the `dashboard` optional-deps group + httpx test dep)
- Create: `dashboard/__init__.py`, `dashboard/app.py`, `dashboard/templates/base.html`, `dashboard/templates/queue.html`, `dashboard/templates/detail.html`
- Test: `tests/dashboard/__init__.py`, `tests/dashboard/test_app.py`

**Interfaces:**
- Consumes: `status.connect`/`list_runs`/`get_run` (existing).
- Produces:
  - `create_app(*, db_path, root, enumerate_streams=None) -> FastAPI` — an app factory (so tests inject a temp DB + a fake enumerate). Holds `db_path`/`root`/`enumerate_streams` in app state; opens a short-lived `status.connect(db_path)` per request.
  - `GET /` → 200, renders the queue (all runs newest-first, status badge, link to detail).
  - `GET /run/{video_id}` → 200 detail (stage/status, confidence note, sermon + shorts links when present, error + Retry, and the Process form when status is `discovered`); 404 if unknown.

- [ ] **Step 1: Write the failing tests**

```python
# tests/dashboard/test_app.py
from fastapi.testclient import TestClient
from sermon_cut.auto import status
from dashboard.app import create_app


def _client(tmp_path, enumerate_streams=None):
    db = tmp_path / "auto.db"
    conn = status.connect(db)
    status.upsert_run(conn, "vid1", now="t0", status="discovered")
    status.upsert_run(conn, "vid2", now="t1", status="ready_for_review",
                      sermon_url="https://youtu.be/S", shorts_urls=["https://youtu.be/A"])
    conn.close()
    app = create_app(db_path=db, root=tmp_path, enumerate_streams=enumerate_streams)
    return TestClient(app)


def test_queue_lists_runs(tmp_path):
    r = _client(tmp_path).get("/")
    assert r.status_code == 200
    assert "vid1" in r.text and "vid2" in r.text
    assert "discovered" in r.text and "ready_for_review" in r.text


def test_detail_shows_process_form_for_discovered(tmp_path):
    r = _client(tmp_path).get("/run/vid1")
    assert r.status_code == 200
    assert "vid1" in r.text
    assert 'name="speaker"' in r.text and "Process" in r.text     # form present


def test_detail_shows_links_for_ready(tmp_path):
    r = _client(tmp_path).get("/run/vid2")
    assert r.status_code == 200
    assert "https://youtu.be/S" in r.text and "https://youtu.be/A" in r.text


def test_detail_unknown_404(tmp_path):
    assert _client(tmp_path).get("/run/nope").status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/dashboard/test_app.py -q`
Expected: FAIL (ModuleNotFoundError dashboard)

- [ ] **Step 3: Implement the app + templates**

`dashboard/app.py`:

```python
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from sermon_cut.auto import status

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def create_app(*, db_path, root, enumerate_streams=None) -> FastAPI:
    app = FastAPI(title="Sermon Cut")
    app.state.db_path = db_path
    app.state.root = root
    app.state.enumerate_streams = enumerate_streams

    @app.get("/", response_class=HTMLResponse)
    def queue(request: Request):
        conn = status.connect(app.state.db_path)
        runs = status.list_runs(conn); conn.close()
        return _TEMPLATES.TemplateResponse(request, "queue.html", {"runs": runs})

    @app.get("/run/{video_id}", response_class=HTMLResponse)
    def detail(request: Request, video_id: str):
        conn = status.connect(app.state.db_path)
        run = status.get_run(conn, video_id); conn.close()
        if run is None:
            raise HTTPException(status_code=404)
        return _TEMPLATES.TemplateResponse(request, "detail.html", {"run": run})

    return app
```

`dashboard/templates/base.html`:

```html
<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sermon Cut</title>
<style>
 body{font-family:system-ui,sans-serif;max-width:820px;margin:1rem auto;padding:0 1rem}
 table{border-collapse:collapse;width:100%} td,th{padding:.5rem;border-bottom:1px solid #ddd;text-align:left}
 .badge{padding:.15rem .5rem;border-radius:.4rem;font-size:.85rem;background:#eee}
 .badge.ready_for_review,.badge.done{background:#d7f5dd} .badge.error{background:#f9d7d7}
 .badge.queued,.badge.discovering,.badge.rendering{background:#fff2cc}
 a{color:#2a6} button{padding:.4rem .8rem} label{display:block;margin:.5rem 0}
 form.inline{display:inline}
</style></head><body>
<h1><a href="/">Sermon Cut</a></h1>
{% block body %}{% endblock %}
</body></html>
```

`dashboard/templates/queue.html`:

```html
{% extends "base.html" %}{% block body %}
<form class="inline" method="post" action="/rescan"><button>Rescan now</button></form>
<table><tr><th>Date/Title</th><th>Status</th><th></th></tr>
{% for r in runs %}
<tr>
  <td>{{ r.title_override or r.video_id }}</td>
  <td><span class="badge {{ r.status }}">{{ r.status }}{% if r.stage %} · {{ r.stage }}{% endif %}</span></td>
  <td><a href="/run/{{ r.video_id }}">open</a></td>
</tr>
{% endfor %}
</table>
{% endblock %}
```

`dashboard/templates/detail.html`:

```html
{% extends "base.html" %}{% block body %}
<h2>{{ run.title_override or run.video_id }}</h2>
<p><span class="badge {{ run.status }}">{{ run.status }}{% if run.stage %} · {{ run.stage }}{% endif %}</span></p>
{% if run.confidence_note %}<p><em>{{ run.confidence_note }}</em></p>{% endif %}
{% if run.error %}<p style="color:#a00">Error: {{ run.error }}</p>
  <form method="post" action="/run/{{ run.video_id }}/retry"><button>Retry</button></form>{% endif %}
{% if run.sermon_url %}<p>Sermon (Unlisted): <a href="{{ run.sermon_url }}">{{ run.sermon_url }}</a></p>{% endif %}
{% if run.shorts_urls %}<p>Shorts:</p><ul>{% for u in run.shorts_urls %}<li><a href="{{ u }}">{{ u }}</a></li>{% endfor %}</ul>{% endif %}
{% if run.status == "discovered" %}
<h3>Process</h3>
<form method="post" action="/run/{{ run.video_id }}/process">
 <label>Speaker <input name="speaker" value="{{ run.speaker or '' }}"></label>
 <label>Title (blank = read the slide) <input name="title" value=""></label>
 <label><input type="checkbox" name="skip_thumbnail"> Skip thumbnail</label>
 <label><input type="checkbox" name="skip_shorts"> Skip Shorts</label>
 <label><input type="checkbox" name="force"> Force re-run</label>
 <button>Process</button>
</form>
{% endif %}
{% endblock %}
```

Add to `pyproject.toml` (do NOT add these to the base `dependencies` — the pipeline stays lean):

```toml
[project.optional-dependencies]
dashboard = ["fastapi", "uvicorn[standard]", "jinja2"]
test = ["pytest", "httpx"]
```

Also silence the harmless third-party `TestClient`/httpx deprecation warning so test output stays pristine — add to the existing `[tool.pytest.ini_options]` in `pyproject.toml`:

```toml
filterwarnings = [
    "ignore:Using `httpx` with `starlette.testclient`.*:DeprecationWarning",
]
```

- [ ] **Step 4: Run tests + full suite + commit**

Run: `.venv/bin/python -m pytest tests/dashboard/test_app.py -q` then `.venv/bin/python -m pytest -q`

```bash
git add pyproject.toml dashboard/ tests/dashboard/
git commit -m "feat(dashboard): FastAPI app + queue/detail read views"
```

---

## Task 4: Dashboard actions (Process / Retry / Rescan)

**Files:**
- Modify: `dashboard/app.py`
- Test: `tests/dashboard/test_app.py` (add cases)

**Interfaces:**
- Consumes: `status.connect`/`enqueue`/`get_run` (Task 1); `watcher.scan_channel` (Phase 2); the app's `enumerate_streams` state.
- Produces:
  - `POST /run/{video_id}/process` — form fields `speaker`, `title`, `skip_thumbnail`, `skip_shorts`, `force` (checkboxes present ⇒ true). Builds `options={"skip_thumbnail":.., "skip_shorts":.., "force":..}`, calls `status.enqueue(conn, video_id, now=<utcnow iso>, speaker=speaker or None, title=title or None, options=options)`, redirects (303) to `/run/{video_id}`.
  - `POST /run/{video_id}/retry` — `status.enqueue(conn, video_id, now=..., speaker=<existing>, title=<existing title_override>, options=<existing options>)` (re-queue with the row's stored params), redirect to detail.
  - `POST /rescan` — `watcher.scan_channel(enumerate_streams=app.state.enumerate_streams, conn=conn, now=...)`; redirect to `/`. If `enumerate_streams` is None, no-op (still redirect).

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/dashboard/test_app.py
def test_process_enqueues_with_options(tmp_path):
    c = _client(tmp_path)
    r = c.post("/run/vid1/process",
               data={"speaker": "Guest Bob", "title": "My Title", "skip_shorts": "on"},
               follow_redirects=False)
    assert r.status_code == 303
    conn = status.connect(tmp_path / "auto.db")
    run = status.get_run(conn, "vid1")
    assert run.status == "queued" and run.speaker == "Guest Bob"
    assert run.title_override == "My Title" and run.options.get("skip_shorts") is True
    assert run.options.get("skip_thumbnail") is False


def test_retry_requeues_with_stored_params(tmp_path):
    db = tmp_path / "auto.db"
    conn = status.connect(db)
    status.upsert_run(conn, "vid1", now="t0", status="error", error="boom",
                      speaker="Prev", title_override="Prev T", options={"skip_shorts": True})
    conn.close()
    from dashboard.app import create_app
    c = TestClient(create_app(db_path=db, root=tmp_path))
    r = c.post("/run/vid1/retry", follow_redirects=False)
    assert r.status_code == 303
    run = status.get_run(status.connect(db), "vid1")
    assert run.status == "queued" and run.speaker == "Prev" and run.options == {"skip_shorts": True}


def test_rescan_inserts_discovered(tmp_path):
    streams = lambda: [("newvid", "Sunday Morning Class & Worship - August 30, 2026"),
                       ("wed", "Wed Bible Class - August 26, 2026")]
    c = _client(tmp_path, enumerate_streams=streams)
    r = c.post("/rescan", follow_redirects=False)
    assert r.status_code == 303
    conn = status.connect(tmp_path / "auto.db")
    assert status.get_run(conn, "newvid").status == "discovered"
    assert status.get_run(conn, "wed") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/dashboard/test_app.py -q`
Expected: FAIL (405/404 — routes not defined)

- [ ] **Step 3: Implement the POST routes**

Add to `create_app` in `dashboard/app.py` (import `Form`, `RedirectResponse`, `datetime`, `watcher`):

```python
from fastapi import Form
from fastapi.responses import RedirectResponse
from datetime import datetime, timezone
from sermon_cut.auto import watcher

def _now(): return datetime.now(timezone.utc).isoformat()

    @app.post("/run/{video_id}/process")
    def process(video_id: str, speaker: str = Form(""), title: str = Form(""),
                skip_thumbnail: str = Form(None), skip_shorts: str = Form(None),
                force: str = Form(None)):
        options = {"skip_thumbnail": skip_thumbnail is not None,
                   "skip_shorts": skip_shorts is not None,
                   "force": force is not None}
        conn = status.connect(app.state.db_path)
        status.enqueue(conn, video_id, now=_now(), speaker=speaker or None,
                       title=title or None, options=options); conn.close()
        return RedirectResponse(f"/run/{video_id}", status_code=303)

    @app.post("/run/{video_id}/retry")
    def retry(video_id: str):
        conn = status.connect(app.state.db_path)
        run = status.get_run(conn, video_id)
        if run is None:
            conn.close(); raise HTTPException(404)
        status.enqueue(conn, video_id, now=_now(), speaker=run.speaker,
                       title=run.title_override, options=run.options); conn.close()
        return RedirectResponse(f"/run/{video_id}", status_code=303)

    @app.post("/rescan")
    def rescan():
        if app.state.enumerate_streams is not None:
            conn = status.connect(app.state.db_path)
            watcher.scan_channel(enumerate_streams=app.state.enumerate_streams,
                                 conn=conn, now=_now()); conn.close()
        return RedirectResponse("/", status_code=303)
```

- [ ] **Step 4: Run tests + full suite + commit**

Run: `.venv/bin/python -m pytest tests/dashboard/test_app.py -q` then `.venv/bin/python -m pytest -q`

```bash
git add dashboard/app.py tests/dashboard/test_app.py
git commit -m "feat(dashboard): Process/Retry/Rescan actions enqueue jobs"
```

---

## Task 5: Server entry — uvicorn + worker thread + real enumerate

**Files:**
- Create: `dashboard/server.py`
- Test: `tests/dashboard/test_app.py` (add a wiring smoke test — no real server)

**Interfaces:**
- Consumes: `create_app` (Task 3); `worker.serve_forever` (Task 2); `sermon_cut.cli._ls_enumerate` + `sermon_cut.config.load_config`.
- Produces:
  - `build(*, db_path, root, channel_id) -> tuple[FastAPI, Callable[[], None]]` — returns the app (with a real `enumerate_streams = lambda: _ls_enumerate(channel_id)`) and a `start_worker()` that spawns a daemon thread running `worker.serve_forever(status.connect(db_path), root=root, now_fn=<utcnow iso>)`. Kept a plain builder so it's unit-testable without binding a socket.
  - `main()` — reads config (`load_config("config.toml")` for `youtube_channel_id`), computes `db_path`/`root`, calls `build`, `start_worker()`, then `uvicorn.run(app, host="0.0.0.0", port=8890)`. This is the container entrypoint.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/dashboard/test_app.py
def test_build_wires_app_and_worker(tmp_path):
    from dashboard import server
    app, start_worker = server.build(db_path=tmp_path / "auto.db", root=tmp_path,
                                     channel_id="TESTCH")
    # app has a real enumerate bound; start_worker is callable but we don't run the loop here
    assert callable(start_worker)
    assert app.state.enumerate_streams is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/dashboard/test_app.py::test_build_wires_app_and_worker -q`
Expected: FAIL (ModuleNotFoundError dashboard.server)

- [ ] **Step 3: Implement**

```python
# dashboard/server.py
from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path

from sermon_cut.auto import status, worker
from sermon_cut.cli import _ls_enumerate
from .app import create_app


def _now(): return datetime.now(timezone.utc).isoformat()


def build(*, db_path, root, channel_id):
    app = create_app(db_path=db_path, root=root,
                     enumerate_streams=lambda: _ls_enumerate(channel_id))

    def start_worker():
        conn = status.connect(db_path)
        t = threading.Thread(
            target=worker.serve_forever,
            kwargs=dict(conn=conn, root=root, now_fn=_now), daemon=True)
        t.start()
        return t

    return app, start_worker


def main():  # container entrypoint
    import uvicorn
    from sermon_cut.config import load_config
    cfg = load_config(Path("config.toml"))
    app, start_worker = build(db_path=Path("auto.db"), root=Path("."),
                              channel_id=cfg.youtube_channel_id)
    start_worker()
    uvicorn.run(app, host="0.0.0.0", port=8890)


if __name__ == "__main__":
    main()
```

> Note: a sqlite connection created in `main`'s process is handed to the worker thread; `status.connect` opens with default settings — if the worker thread's use raises `ProgrammingError: SQLite objects created in a thread...`, pass `check_same_thread=False` in a dedicated worker connection (open it inside `start_worker`'s thread target instead, or via `sqlite3.connect(..., check_same_thread=False)`). Implement the thread to open its own connection inside the thread to avoid cross-thread sharing; the test only asserts wiring, so this detail is exercised by the Phase-4 dry run.

- [ ] **Step 4: Run test + full suite + commit**

Run: `.venv/bin/python -m pytest tests/dashboard/test_app.py -q` then `.venv/bin/python -m pytest -q`

```bash
git add dashboard/server.py tests/dashboard/test_app.py
git commit -m "feat(dashboard): server entry — uvicorn + worker thread + channel enumerate"
```

---

## Out of scope (Phase 4)

- Docker packaging + nginx-proxy-manager proxy host + the in-container `claude -p` go/no-go spike + volume mounts + the discovery-watcher cron. The `dashboard.server:main` entrypoint and the by-hand `auto` CLI are what Phase 4 containerizes.

## Self-Review notes

- Spec coverage (Phase 3): queue state + enqueue/claim = Task 1; worker loop = Task 2; queue+detail views = Task 3; Process/Retry/Rescan actions = Task 4; server entry + worker thread + real enumerate = Task 5.
- Invariants: FastAPI/Jinja/uvicorn are dashboard-only (optional-deps group; the `sermon_cut` pipeline never imports them); worker runs the Phase-2 orchestrator with real collaborators; straight-to-Unlisted; web/worker talk only through `auto.db`; all tests offline (TestClient + injected orchestrate/enumerate).
- Type consistency: `status.enqueue`/`claim_next` (Task 1) used by worker (Task 2) and app (Task 4); `worker.serve_forever(conn, *, root, now_fn, interval, stop)` used by server (Task 5); `create_app(*, db_path, root, enumerate_streams)` used by tests + server; `watcher.scan_channel(enumerate_streams=, conn=, now=)` per Phase 2; `_ls_enumerate(channel_id)` per cli.py.
- Known cross-thread sqlite caveat flagged in Task 5 for the Phase-4 dry run (open the worker's connection inside its own thread).
