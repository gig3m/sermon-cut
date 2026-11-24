# Autopilot Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the headless autonomous core of sermon-cut — a status store, a confidence gate, an email notifier, an unattended discovery skill, and a three-phase orchestrator — so the pipeline can run by hand on the homelab, auto-decide confident weeks, and email on the ones needing approval.

**Architecture:** New additive package `sermon_cut/auto/` reusing the existing CLI stages. The orchestrator drives three jobs — Phase 1 `discover_decide`, Phase 2 `render_publish` (sermon + livestream thumbnail), Phase 3 `make_shorts` — each invoking existing `python -m sermon_cut` subcommands via an injectable runner, invoking Claude's judgment via an injectable "judge", recording state in a SQLite `auto.db`, and sending Resend email via an injectable notifier. Every collaborator is injected, so the whole orchestrator is unit-tested offline with fakes.

**Tech Stack:** Python 3.13, stdlib `sqlite3` + `urllib` (no new runtime deps), pytest (offline), Claude Code headless (`claude -p`), Resend HTTP API, `keys exec` for secrets.

## Global Constraints

- Privacy is always `unlisted` — no code path uploads Public (copied from spec).
- Discovery is audio-only; full video download happens only in `clip` (copied from spec).
- Heavy deps (faster_whisper, googleapiclient, PIL, cv2, numpy) and binaries (yt-dlp, ffmpeg) are NEVER imported at module top-level — invoked via injected runner/client or lazy import inside a function (copied from spec).
- Tests run with only pytest, fully offline — no network, GPU, or real binaries (copied from spec).
- New code is additive under `sermon_cut/auto/`; existing stages/skills/`assets/` are reused, not modified in behavior.
- Secrets are read from the environment only; the process is launched under `keys exec RESEND_API_KEY -- …` so no secret touches disk or logs.
- Timestamps are passed in explicitly (an ISO-8601 `now: str`) to keep functions deterministic and testable.

---

## File Structure

- Create `sermon_cut/auto/__init__.py` — package marker.
- Create `sermon_cut/auto/status.py` — SQLite status index (one row per weekly run).
- Create `sermon_cut/auto/confidence.py` — pure confidence gate combining validate-plan band + Claude self-assessment.
- Create `sermon_cut/auto/notify.py` — Resend email wrapper (`send_email`).
- Create `sermon_cut/auto/orchestrator.py` — three-phase job runner with injected collaborators.
- Create `.claude/skills/sermon-auto/SKILL.md` — unattended discovery skill run by `claude -p`.
- Modify `sermon_cut/cli.py` — add `auto` subcommands (`auto discover`, `auto render`, `auto shorts`).
- Create `tests/auto/__init__.py`, `tests/auto/test_status.py`, `tests/auto/test_confidence.py`, `tests/auto/test_notify.py`, `tests/auto/test_orchestrator.py`.
- Create `docs/homelab-setup.md` — one-time homelab ops (re-auth, headless Claude token, cron).

---

## Task 1: Status store (`status.py`)

**Files:**
- Create: `sermon_cut/auto/__init__.py`
- Create: `sermon_cut/auto/status.py`
- Test: `tests/auto/__init__.py`, `tests/auto/test_status.py`

**Interfaces:**
- Produces:
  - `STATUSES: tuple[str, ...]` = `("discovering","needs_approval","rendering","ready_for_review","done","error","skipped")`
  - `@dataclass Run(video_id, status, stage, plan_path, confidence_note, sermon_url, shorts_urls: list[str], error, created_at, updated_at)`
  - `connect(db_path) -> sqlite3.Connection` (also runs `init_db`)
  - `init_db(conn) -> None`
  - `upsert_run(conn, video_id, *, now, status=None, stage=None, plan_path=None, confidence_note=None, sermon_url=None, shorts_urls=None, error=None) -> None`
  - `get_run(conn, video_id) -> Run | None`
  - `list_runs(conn) -> list[Run]` (newest `updated_at` first)

- [ ] **Step 1: Create package markers**

```python
# sermon_cut/auto/__init__.py
```
```python
# tests/auto/__init__.py
```

- [ ] **Step 2: Write the failing test**

```python
# tests/auto/test_status.py
from sermon_cut.auto import status


def test_upsert_insert_then_update_preserves_created_at():
    conn = status.connect(":memory:")
    status.upsert_run(conn, "vid1", now="2026-07-30T10:00:00", status="discovering", stage="fetch_audio")
    status.upsert_run(conn, "vid1", now="2026-07-30T10:05:00", status="rendering", stage="clip",
                      sermon_url=None)
    run = status.get_run(conn, "vid1")
    assert run.video_id == "vid1"
    assert run.status == "rendering"
    assert run.stage == "clip"
    assert run.created_at == "2026-07-30T10:00:00"
    assert run.updated_at == "2026-07-30T10:05:00"


def test_shorts_urls_round_trip_as_list():
    conn = status.connect(":memory:")
    status.upsert_run(conn, "v", now="t0", status="done", shorts_urls=["https://a", "https://b"])
    assert status.get_run(conn, "v").shorts_urls == ["https://a", "https://b"]


def test_get_run_missing_returns_none():
    conn = status.connect(":memory:")
    assert status.get_run(conn, "nope") is None


def test_list_runs_newest_first():
    conn = status.connect(":memory:")
    status.upsert_run(conn, "old", now="2026-07-01T00:00:00", status="done")
    status.upsert_run(conn, "new", now="2026-07-30T00:00:00", status="done")
    ids = [r.video_id for r in status.list_runs(conn)]
    assert ids == ["new", "old"]


def test_partial_update_leaves_other_columns_untouched():
    conn = status.connect(":memory:")
    status.upsert_run(conn, "v", now="t0", status="ready_for_review", sermon_url="https://youtu.be/x")
    status.upsert_run(conn, "v", now="t1", stage="shorts")   # only stage changes
    run = status.get_run(conn, "v")
    assert run.sermon_url == "https://youtu.be/x"
    assert run.status == "ready_for_review"
    assert run.stage == "shorts"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/auto/test_status.py -q`
Expected: FAIL (`ModuleNotFoundError: sermon_cut.auto.status`)

- [ ] **Step 4: Write the implementation**

```python
# sermon_cut/auto/status.py
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

STATUSES = (
    "discovering", "needs_approval", "rendering",
    "ready_for_review", "done", "error", "skipped",
)

_COLUMNS = (
    "status", "stage", "plan_path", "confidence_note",
    "sermon_url", "shorts_urls", "error",
)


@dataclass
class Run:
    video_id: str
    status: str
    stage: str | None
    plan_path: str | None
    confidence_note: str | None
    sermon_url: str | None
    shorts_urls: list[str]
    error: str | None
    created_at: str
    updated_at: str


def connect(db_path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS runs (
            video_id        TEXT PRIMARY KEY,
            status          TEXT NOT NULL,
            stage           TEXT,
            plan_path       TEXT,
            confidence_note TEXT,
            sermon_url      TEXT,
            shorts_urls     TEXT NOT NULL DEFAULT '[]',
            error           TEXT,
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        )
        """
    )
    conn.commit()


def upsert_run(conn: sqlite3.Connection, video_id: str, *, now: str,
               status: str | None = None, stage: str | None = None,
               plan_path: str | None = None, confidence_note: str | None = None,
               sermon_url: str | None = None, shorts_urls: list[str] | None = None,
               error: str | None = None) -> None:
    if status is not None and status not in STATUSES:
        raise ValueError(f"unknown status {status!r}")
    values = {
        "status": status, "stage": stage, "plan_path": plan_path,
        "confidence_note": confidence_note, "sermon_url": sermon_url,
        "shorts_urls": None if shorts_urls is None else json.dumps(shorts_urls),
        "error": error,
    }
    existing = conn.execute("SELECT 1 FROM runs WHERE video_id = ?", (video_id,)).fetchone()
    if existing is None:
        conn.execute(
            """INSERT INTO runs (video_id, status, stage, plan_path, confidence_note,
                                 sermon_url, shorts_urls, error, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (video_id, values["status"] or "discovering", values["stage"],
             values["plan_path"], values["confidence_note"], values["sermon_url"],
             values["shorts_urls"] or "[]", values["error"], now, now),
        )
    else:
        sets, params = [], []
        for col in _COLUMNS:
            if values[col] is not None:
                sets.append(f"{col} = ?")
                params.append(values[col])
        sets.append("updated_at = ?")
        params.append(now)
        params.append(video_id)
        conn.execute(f"UPDATE runs SET {', '.join(sets)} WHERE video_id = ?", params)
    conn.commit()


def _row_to_run(row: sqlite3.Row) -> Run:
    return Run(
        video_id=row["video_id"], status=row["status"], stage=row["stage"],
        plan_path=row["plan_path"], confidence_note=row["confidence_note"],
        sermon_url=row["sermon_url"], shorts_urls=json.loads(row["shorts_urls"]),
        error=row["error"], created_at=row["created_at"], updated_at=row["updated_at"],
    )


def get_run(conn: sqlite3.Connection, video_id: str) -> Run | None:
    row = conn.execute("SELECT * FROM runs WHERE video_id = ?", (video_id,)).fetchone()
    return _row_to_run(row) if row else None


def list_runs(conn: sqlite3.Connection) -> list[Run]:
    rows = conn.execute("SELECT * FROM runs ORDER BY updated_at DESC").fetchall()
    return [_row_to_run(r) for r in rows]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/auto/test_status.py -q`
Expected: PASS (5 passed)

- [ ] **Step 6: Commit**

```bash
git add sermon_cut/auto/__init__.py sermon_cut/auto/status.py tests/auto/__init__.py tests/auto/test_status.py
git commit -m "feat(auto): SQLite status store for weekly runs"
```

---

## Task 2: Confidence gate (`confidence.py`)

**Files:**
- Create: `sermon_cut/auto/confidence.py`
- Test: `tests/auto/test_confidence.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces:
  - `@dataclass SelfAssessment(in_point_ok: bool, out_point_ok: bool, no_reading_bleed: bool, note: str)`
  - `evaluate(*, band: str, assessment: SelfAssessment) -> tuple[bool, str]` — `band` is `"IN_BAND"` or `"OUT_OF_BAND"` (the `validate-plan` output). Confident iff `band == "IN_BAND"` and all three assessment flags are True. Returns `(confident, note)`; the note is the assessment note when confident, else `"held: <reasons>"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/auto/test_confidence.py
from sermon_cut.auto.confidence import SelfAssessment, evaluate


def _ok(note="clean open on greeting, closes on Amen"):
    return SelfAssessment(in_point_ok=True, out_point_ok=True, no_reading_bleed=True, note=note)


def test_confident_when_in_band_and_all_flags_ok():
    confident, note = evaluate(band="IN_BAND", assessment=_ok())
    assert confident is True
    assert note == "clean open on greeting, closes on Amen"


def test_held_when_out_of_band():
    confident, note = evaluate(band="OUT_OF_BAND", assessment=_ok())
    assert confident is False
    assert note.startswith("held:")
    assert "length OUT_OF_BAND" in note


def test_held_when_reading_bleed():
    a = SelfAssessment(in_point_ok=False, out_point_ok=True, no_reading_bleed=False, note="x")
    confident, note = evaluate(band="IN_BAND", assessment=a)
    assert confident is False
    assert "in-point uncertain" in note
    assert "scripture reading may bleed in" in note


def test_held_when_out_point_uncertain():
    a = SelfAssessment(in_point_ok=True, out_point_ok=False, no_reading_bleed=True, note="x")
    confident, note = evaluate(band="IN_BAND", assessment=a)
    assert confident is False
    assert "out-point uncertain" in note
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/auto/test_confidence.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Write the implementation**

```python
# sermon_cut/auto/confidence.py
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SelfAssessment:
    in_point_ok: bool        # opens on the preacher's greeting, not a Scripture reading
    out_point_ok: bool       # closes on the prayer / "Amen"
    no_reading_bleed: bool   # the pre-sermon reading does not bleed into the clip
    note: str                # Claude's one-line rationale


def evaluate(*, band: str, assessment: SelfAssessment) -> tuple[bool, str]:
    reasons: list[str] = []
    if band != "IN_BAND":
        reasons.append(f"length {band}")
    if not assessment.in_point_ok:
        reasons.append("in-point uncertain")
    if not assessment.no_reading_bleed:
        reasons.append("scripture reading may bleed in")
    if not assessment.out_point_ok:
        reasons.append("out-point uncertain")
    if reasons:
        return False, "held: " + "; ".join(reasons)
    return True, assessment.note
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/auto/test_confidence.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add sermon_cut/auto/confidence.py tests/auto/test_confidence.py
git commit -m "feat(auto): confidence gate combining band + self-assessment"
```

---

## Task 3: Email notifier (`notify.py`)

**Files:**
- Create: `sermon_cut/auto/notify.py`
- Test: `tests/auto/test_notify.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `send_email(subject: str, html: str, *, to: str, sender: str, api_key: str | None = None, opener=None) -> str` — POSTs to `https://api.resend.com/emails`; `api_key` defaults to `os.environ["RESEND_API_KEY"]`; `opener` defaults to `urllib.request.urlopen` (injectable for tests). Returns the Resend message id.

- [ ] **Step 1: Write the failing test**

```python
# tests/auto/test_notify.py
import io
import json

import pytest

from sermon_cut.auto import notify


class _FakeResp:
    def __init__(self, payload): self._p = payload
    def read(self): return json.dumps(self._p).encode()
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_send_email_posts_expected_payload_and_returns_id():
    captured = {}

    def fake_opener(req, timeout=0):
        captured["url"] = req.full_url
        captured["auth"] = req.headers["Authorization"]
        captured["body"] = json.loads(req.data)
        return _FakeResp({"id": "email_123"})

    mid = notify.send_email("Subj", "<b>hi</b>", to="k@x.co", sender="a@church.net",
                            api_key="re_test", opener=fake_opener)
    assert mid == "email_123"
    assert captured["url"] == "https://api.resend.com/emails"
    assert captured["auth"] == "Bearer re_test"
    assert captured["body"] == {"from": "a@church.net", "to": ["k@x.co"],
                                "subject": "Subj", "html": "<b>hi</b>"}


def test_api_key_defaults_to_env(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_env")
    seen = {}

    def fake_opener(req, timeout=0):
        seen["auth"] = req.headers["Authorization"]
        return _FakeResp({"id": "x"})

    notify.send_email("s", "h", to="t@x.co", sender="a@x.co", opener=fake_opener)
    assert seen["auth"] == "Bearer re_env"


def test_missing_env_key_raises(monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    with pytest.raises(KeyError):
        notify.send_email("s", "h", to="t@x.co", sender="a@x.co", opener=lambda *a, **k: None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/auto/test_notify.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Write the implementation**

```python
# sermon_cut/auto/notify.py
from __future__ import annotations

import json
import os
import urllib.request

_RESEND_URL = "https://api.resend.com/emails"


def send_email(subject: str, html: str, *, to: str, sender: str,
               api_key: str | None = None, opener=None) -> str:
    """Send an email via the Resend HTTP API and return the message id.

    api_key defaults to os.environ["RESEND_API_KEY"] — inject the process env with
    `keys exec RESEND_API_KEY -- …` so the secret never touches disk or logs.
    opener defaults to urllib.request.urlopen and is injectable for offline tests.
    """
    if api_key is None:
        api_key = os.environ["RESEND_API_KEY"]
    body = json.dumps({"from": sender, "to": [to], "subject": subject, "html": html}).encode()
    req = urllib.request.Request(
        _RESEND_URL, data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    opener = opener or urllib.request.urlopen
    with opener(req, timeout=20) as resp:
        return json.loads(resp.read())["id"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/auto/test_notify.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add sermon_cut/auto/notify.py tests/auto/test_notify.py
git commit -m "feat(auto): Resend email notifier (env key, injectable opener)"
```

---

## Task 4: Unattended discovery skill (`sermon-auto`)

**Files:**
- Create: `.claude/skills/sermon-auto/SKILL.md`

**Interfaces:**
- Produces (contract the orchestrator relies on): after `claude -p` runs this skill against `runs/<id>/`, the working dir contains a valid `runs/<id>/plan.json` AND `runs/<id>/auto_verdict.json` shaped exactly:
  ```json
  {"in_point_ok": true, "out_point_ok": true, "no_reading_bleed": true,
   "note": "opens on greeting; closes on the closing prayer Amen"}
  ```
  (all four keys required; booleans + string). If the service is a 5th-Sunday singing service with no sermon, it writes `runs/<id>/auto_verdict.json` = `{"skip": true, "note": "singing service, no sermon"}` and no plan.

This task ships a skill document, not code, so it has no pytest cycle; its contract is exercised by Task 5's orchestrator tests (which fake the judge) and by the Phase-6 dry run.

- [ ] **Step 1: Write the skill**

````markdown
# Sermon Auto (unattended)

Runs **headless** (`claude -p`) inside an existing `runs/<id>/` that already has
`audio.m4a` and `transcript.json`. Does the judgment step of `/sermon-cut` with
**no prompts** and writes two files, then exits.

## Steps

1. Read `runs/<id>/transcript.json`. Identify the single sustained teaching block
   (the sermon): opens on the preacher's greeting (NOT a pre-sermon Scripture
   reading, usually read by a child), closes on the closing prayer's "Amen".
   Exclude the reading; include an intro video/greeting if one plays.
2. If there is no sermon (e.g. a 5th-Sunday singing service), write
   `runs/<id>/auto_verdict.json` = `{"skip": true, "note": "<why>"}` and STOP.
3. Otherwise draft `runs/<id>/plan.json` exactly as `/sermon-cut` does:
   `start`, `end` (seconds), `title` (clean, no date), `speaker`
   (config `default_speaker` unless the transcript clearly names a guest),
   `date` (from `source.info.json` `upload_date`, `YYYY-MM-DD`), a 2–4 sentence
   `description`, and 3–6 `chapters` ({time, label} within the sermon).
   Set `start` = greeting time + `front_padding_seconds`.
4. Self-assess and write `runs/<id>/auto_verdict.json` with EXACTLY these keys:
   - `in_point_ok` (bool): true only if you are confident the clip opens on the
     preacher's greeting, not the reading.
   - `out_point_ok` (bool): true only if you are confident it closes on the
     prayer/"Amen", not mid-thought or into announcements.
   - `no_reading_bleed` (bool): true only if the pre-sermon reading cannot bleed
     into the padded in-point.
   - `note` (string): one-line rationale.
   Be honest — a false flag here is what routes the week to human approval.

Do NOT run any CLI stage, upload, or render. Discovery/judgment only.
````

- [ ] **Step 2: Commit**

```bash
git add .claude/skills/sermon-auto/SKILL.md
git commit -m "feat(auto): unattended sermon-auto discovery skill"
```

---

## Task 5: Three-phase orchestrator (`orchestrator.py`)

**Files:**
- Create: `sermon_cut/auto/orchestrator.py`
- Test: `tests/auto/test_orchestrator.py`

**Interfaces:**
- Consumes: `status` (Task 1), `confidence.SelfAssessment`/`evaluate` (Task 2), `notify.send_email` (Task 3), the `sermon-auto` verdict contract (Task 4).
- Produces:
  - `discover_decide(video_url, *, root, conn, now, runner, judge, notifier, resolve_id) -> str` — runs fetch-audio + transcribe, invokes `judge(workdir)`, reads the verdict, runs `validate-plan`, applies `confidence.evaluate`. On skip → status `skipped`. On confident → status `rendering` then calls `render_publish`. On unsure → status `needs_approval` + `notifier` email. Returns the final status. `resolve_id(video_url) -> str` maps a URL to a video id (injectable).
  - `render_publish(video_id, *, root, conn, now, runner, notifier) -> str` — runs clip→intro→outro→assemble→upload (sermon) + livestream-thumb, sets `sermon_url` from the upload receipt, status `ready_for_review`, emails the sermon link. Returns status.
  - `make_shorts(video_id, *, root, conn, now, runner, notifier) -> str` — Phase 3: runs the shorts stages, collects short URLs from receipts, sets `shorts_urls`, status `done`, emails the short links. Returns status.
  - `runner(args: list[str]) -> subprocess.CompletedProcess` — default `subprocess.run(["...python", "-m", "sermon_cut", *args], check=True, capture_output=True, text=True)`; injectable.
  - `judge(workdir: Path) -> None` — default shells out to `claude -p "/sermon-auto"` in `workdir`; injectable.

- [ ] **Step 1: Write the failing tests**

```python
# tests/auto/test_orchestrator.py
import json
from pathlib import Path

from sermon_cut.auto import status, orchestrator


def _write(p: Path, obj): p.parent.mkdir(parents=True, exist_ok=True); p.write_text(json.dumps(obj))


def _plan():
    return {"start": 3818.0, "end": 5702.0, "title": "T", "speaker": "S",
            "date": "2026-07-26", "description": "d", "chapters": []}


def _make_ctx(tmp_path, verdict, band="IN_BAND", calls=None):
    calls = calls if calls is not None else []
    conn = status.connect(":memory:")

    def runner(args):
        calls.append(args)
        if args[0] == "validate-plan":
            print_out = "OK " + band
            return type("R", (), {"stdout": print_out, "returncode": 0})()
        if args[0] == "upload":
            _write(tmp_path / "runs" / "vid" / "upload.json",
                   {"video_id": "SERM", "url": "https://youtu.be/SERM"})
            return type("R", (), {"stdout": "https://youtu.be/SERM", "returncode": 0})()
        return type("R", (), {"stdout": "", "returncode": 0})()

    def judge(workdir):
        _write(Path(workdir) / "auto_verdict.json", verdict)
        if "skip" not in verdict:
            _write(Path(workdir) / "plan.json", _plan())

    emails = []
    def notifier(subject, html): emails.append((subject, html))
    return conn, runner, judge, notifier, calls, emails


def test_confident_week_renders_and_marks_ready(tmp_path):
    conn, runner, judge, notifier, calls, emails = _make_ctx(
        tmp_path, {"in_point_ok": True, "out_point_ok": True, "no_reading_bleed": True, "note": "clean"})
    final = orchestrator.discover_decide(
        "https://youtu.be/vid", root=tmp_path, conn=conn, now="t0",
        runner=runner, judge=judge, notifier=notifier, resolve_id=lambda u: "vid")
    assert final == "ready_for_review"
    run = status.get_run(conn, "vid")
    assert run.status == "ready_for_review"
    assert run.sermon_url == "https://youtu.be/SERM"
    assert any(a[0] == "assemble" for a in calls)
    assert any("ready" in s.lower() or "review" in s.lower() for s, _ in emails)


def test_unsure_week_holds_for_approval_and_emails(tmp_path):
    conn, runner, judge, notifier, calls, emails = _make_ctx(
        tmp_path, {"in_point_ok": False, "out_point_ok": True, "no_reading_bleed": False, "note": "x"})
    final = orchestrator.discover_decide(
        "https://youtu.be/vid", root=tmp_path, conn=conn, now="t0",
        runner=runner, judge=judge, notifier=notifier, resolve_id=lambda u: "vid")
    assert final == "needs_approval"
    assert status.get_run(conn, "vid").status == "needs_approval"
    assert not any(a[0] == "assemble" for a in calls)   # did NOT render
    assert any("approv" in s.lower() or "ok" in s.lower() for s, _ in emails)


def test_singing_service_is_skipped(tmp_path):
    conn, runner, judge, notifier, calls, emails = _make_ctx(
        tmp_path, {"skip": True, "note": "singing service"})
    final = orchestrator.discover_decide(
        "https://youtu.be/vid", root=tmp_path, conn=conn, now="t0",
        runner=runner, judge=judge, notifier=notifier, resolve_id=lambda u: "vid")
    assert final == "skipped"
    assert not any(a[0] in ("clip", "assemble") for a in calls)


def test_make_shorts_sets_urls_and_done(tmp_path):
    conn = status.connect(":memory:")
    status.upsert_run(conn, "vid", now="t0", status="ready_for_review")
    calls = []

    def runner(args):
        calls.append(args)
        if args[0] == "shorts-upload":
            # emulate a receipt written per short
            wd = Path([a for a in args if "shorts/" in str(a)][0])
            _write(Path(wd) / "upload.json", {"video_id": "SH", "url": "https://youtu.be/SH"})
        return type("R", (), {"stdout": "", "returncode": 0})()

    # one candidate present
    _write(tmp_path / "runs" / "vid" / "shorts" / "candidates.json",
           {"candidates": [{"slug": "clip1", "chosen": True}]})
    emails = []
    final = orchestrator.make_shorts("vid", root=tmp_path, conn=conn, now="t1",
                                     runner=runner, notifier=lambda s, h: emails.append(s))
    assert final == "done"
    run = status.get_run(conn, "vid")
    assert run.status == "done"
    assert run.shorts_urls == ["https://youtu.be/SH"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/auto/test_orchestrator.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Write the implementation**

```python
# sermon_cut/auto/orchestrator.py
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from . import status
from .confidence import SelfAssessment, evaluate


def _default_runner(args):
    return subprocess.run([sys.executable, "-m", "sermon_cut", *map(str, args)],
                          check=True, capture_output=True, text=True)


def _default_judge(workdir):
    subprocess.run(["claude", "-p", "/sermon-auto"], cwd=str(workdir), check=True)


def _band_from_output(stdout: str) -> str:
    return "IN_BAND" if "IN_BAND" in stdout else "OUT_OF_BAND"


def _read_receipt_url(workdir: Path) -> str | None:
    p = workdir / "upload.json"
    return json.loads(p.read_text())["url"] if p.exists() else None


def discover_decide(video_url, *, root, conn, now, runner=_default_runner,
                    judge=_default_judge, notifier, resolve_id) -> str:
    root = Path(root)
    vid = resolve_id(video_url)
    wd = root / "runs" / vid
    wd.mkdir(parents=True, exist_ok=True)
    status.upsert_run(conn, vid, now=now, status="discovering", stage="fetch_audio")

    runner(["fetch-audio", video_url, "--workdir", wd])
    status.upsert_run(conn, vid, now=now, stage="transcribe")
    runner(["transcribe", "--audio", wd / "audio.m4a", "--workdir", wd])

    status.upsert_run(conn, vid, now=now, stage="decide")
    judge(wd)
    verdict = json.loads((wd / "auto_verdict.json").read_text())
    if verdict.get("skip"):
        status.upsert_run(conn, vid, now=now, status="skipped",
                          confidence_note=verdict.get("note", "skipped"))
        return "skipped"

    vp = runner(["validate-plan", "--plan", wd / "plan.json"])
    band = _band_from_output(vp.stdout)
    assessment = SelfAssessment(
        in_point_ok=bool(verdict["in_point_ok"]),
        out_point_ok=bool(verdict["out_point_ok"]),
        no_reading_bleed=bool(verdict["no_reading_bleed"]),
        note=verdict["note"],
    )
    confident, note = evaluate(band=band, assessment=assessment)
    if not confident:
        status.upsert_run(conn, vid, now=now, status="needs_approval",
                          plan_path=str(wd / "plan.json"), confidence_note=note)
        notifier("Sermon needs your OK", f"<p>{note}</p>")
        return "needs_approval"

    status.upsert_run(conn, vid, now=now, status="rendering", stage="clip",
                      plan_path=str(wd / "plan.json"), confidence_note=note)
    return render_publish(vid, root=root, conn=conn, now=now, runner=runner, notifier=notifier)


def render_publish(video_id, *, root, conn, now, runner=_default_runner, notifier) -> str:
    root = Path(root)
    wd = root / "runs" / video_id
    plan = wd / "plan.json"
    status.upsert_run(conn, video_id, now=now, status="rendering", stage="clip")
    runner(["clip", "--plan", plan, "--workdir", wd])
    status.upsert_run(conn, video_id, now=now, stage="intro")
    runner(["intro", "--plan", plan, "--background", "assets/intro_bg.png", "--workdir", wd])
    runner(["outro", "--image", "assets/outro_card.png", "--workdir", wd])
    status.upsert_run(conn, video_id, now=now, stage="assemble")
    runner(["assemble", "--intro", wd / "intro.mp4", "--sermon", wd / "sermon.mp4",
            "--outro", wd / "outro.mp4", "--workdir", wd])
    status.upsert_run(conn, video_id, now=now, stage="upload")
    runner(["upload", "--video", wd / "final.mp4", "--plan", plan, "--workdir", wd])
    sermon_url = _read_receipt_url(wd)
    # set the livestream's own title-card thumbnail (source video == video_id)
    runner(["livestream-thumbs", "--ids", video_id])
    status.upsert_run(conn, video_id, now=now, status="ready_for_review",
                      stage="done", sermon_url=sermon_url)
    notifier("Sermon ready for review",
             f'<p>Unlisted: <a href="{sermon_url}">{sermon_url}</a></p>')
    return "ready_for_review"


def make_shorts(video_id, *, root, conn, now, runner=_default_runner, notifier) -> str:
    root = Path(root)
    wd = root / "runs" / video_id
    cand_path = wd / "shorts" / "candidates.json"
    status.upsert_run(conn, video_id, now=now, stage="shorts")
    runner(["shorts-select", "--workdir", wd])  # writes candidates.json (see follow-on plan)
    cands = json.loads(cand_path.read_text()).get("candidates", []) if cand_path.exists() else []
    urls: list[str] = []
    for c in cands:
        if not c.get("chosen"):
            continue
        slug = c["slug"]
        sdir = wd / "shorts" / slug
        runner(["shorts-clip", "--candidate", slug, "--workdir", sdir])
        runner(["shorts-render", "--candidate", slug, "--workdir", sdir])
        runner(["shorts-upload", "--candidate", slug, "--workdir", sdir])
        url = _read_receipt_url(sdir)
        if url:
            urls.append(url)
    status.upsert_run(conn, video_id, now=now, status="done", stage="done", shorts_urls=urls)
    if urls:
        links = "".join(f'<li><a href="{u}">{u}</a></li>' for u in urls)
        notifier("Shorts ready for review", f"<ul>{links}</ul>")
    return "done"
```

> Note: `shorts-select` is a thin CLI wrapper around the auto-selection that today lives in the `/sermon-shorts` skill; wiring it as a subcommand is part of the follow-on dashboard/scheduler plan. Task 5's `make_shorts` test fakes the runner, so this task does not depend on that wrapper existing yet.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/auto/test_orchestrator.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Run the full suite (no regressions)**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (existing suite + new `tests/auto/`)

- [ ] **Step 6: Commit**

```bash
git add sermon_cut/auto/orchestrator.py tests/auto/test_orchestrator.py
git commit -m "feat(auto): three-phase orchestrator (discover/decide, render, shorts)"
```

---

## Task 6: CLI entry + homelab setup doc

**Files:**
- Modify: `sermon_cut/cli.py` (add an `auto` subparser group)
- Test: `tests/auto/test_orchestrator.py` (add a CLI-dispatch test) — or `tests/test_cli.py` if that's where CLI dispatch is tested
- Create: `docs/homelab-setup.md`

**Interfaces:**
- Consumes: `orchestrator.discover_decide` / `render_publish` / `make_shorts`, `status.connect`.
- Produces: CLI commands
  - `python -m sermon_cut auto discover <url|latest> [--db auto.db] [--root .]`
  - `python -m sermon_cut auto render <video_id> [--db auto.db]`
  - `python -m sermon_cut auto shorts <video_id> [--db auto.db]`
  Each builds the default collaborators (real runner/judge, `notify.send_email` bound to config `to`/`sender`, `status.connect(db)`) and calls the matching orchestrator function.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/auto/test_orchestrator.py
def test_cli_auto_discover_dispatches(monkeypatch, tmp_path):
    from sermon_cut import cli
    seen = {}
    def fake_discover(video_url, **kw):
        seen["url"] = video_url
        seen["db"] = kw["conn"] is not None
        return "skipped"
    monkeypatch.setattr("sermon_cut.auto.orchestrator.discover_decide", fake_discover)
    rc = cli.main(["auto", "discover", "https://youtu.be/x",
                   "--db", str(tmp_path / "auto.db"), "--root", str(tmp_path)])
    assert rc == 0
    assert seen["url"] == "https://youtu.be/x"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/auto/test_orchestrator.py::test_cli_auto_discover_dispatches -q`
Expected: FAIL (`SystemExit`/unknown command `auto`)

- [ ] **Step 3: Add the `auto` subparser to `cli.py`**

Locate `build_parser()` in `sermon_cut/cli.py` (where other subparsers like `livestream-thumbs` are added) and add, following the existing pattern:

```python
    a = sub.add_parser("auto")
    asub = a.add_subparsers(dest="auto_cmd", required=True)
    for name, idarg in (("discover", "url"), ("render", "video_id"), ("shorts", "video_id")):
        sp = asub.add_parser(name, parents=[common])
        sp.add_argument(idarg)
        sp.add_argument("--db", default="auto.db")
        sp.add_argument("--root", default=".")
        sp.add_argument("--config", default="config.toml")
```

Then in the command dispatch (where `elif args.cmd == "livestream-thumbs":` is), add:

```python
    elif args.cmd == "auto":
        from .auto import status, orchestrator, notify
        from .config import load_config
        cfg = load_config(Path(args.config))
        conn = status.connect(args.db)
        # notify.send_email needs to/sender — read from config (add to config.example.toml)
        notifier = lambda subject, html: notify.send_email(
            subject, html, to=cfg.notify_to, sender=cfg.notify_from)
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        if args.auto_cmd == "discover":
            def resolve_id(url):
                if url == "latest":
                    out = _default_runner_capture(["resolve-latest", cfg.youtube_channel_id])
                    url = out.strip()
                return url.rsplit("v=", 1)[-1].rsplit("/", 1)[-1]
            orchestrator.discover_decide(args.url, root=args.root, conn=conn, now=now,
                                         notifier=notifier, resolve_id=resolve_id)
        elif args.auto_cmd == "render":
            orchestrator.render_publish(args.video_id, root=args.root, conn=conn,
                                        now=now, notifier=notifier)
        elif args.auto_cmd == "shorts":
            orchestrator.make_shorts(args.video_id, root=args.root, conn=conn,
                                     now=now, notifier=notifier)
    return 0
```

Add `notify_to` and `notify_from` to `Config` in `sermon_cut/config.py` (with sensible required/optional handling matching the existing dataclass), and to `config.example.toml`. Add a tiny `_default_runner_capture` helper in `cli.py` that runs `python -m sermon_cut resolve-latest …` and returns stdout (mirror `orchestrator._default_runner`).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/auto/test_orchestrator.py::test_cli_auto_discover_dispatches -q`
Expected: PASS

- [ ] **Step 5: Write the homelab setup doc**

```markdown
# docs/homelab-setup.md

One-time setup to run autopilot unattended on the homelab.

## 1. OAuth (non-expiring token)
The OAuth consent screen is published to production (done 2026-07-30), so refresh
tokens no longer expire at 7 days. Re-auth once on the homelab:
  rm token.json
  .venv/bin/python -c "from pathlib import Path; from sermon_cut.youtube_auth import get_authenticated_service; get_authenticated_service(Path('client_secrets.json'), Path('token.json'))"
Click through the "unverified app" warning. The resulting token.json persists.

## 2. Headless Claude under Max ($0)
Install Claude Code on the homelab and authenticate to the Max subscription with a
long-lived token:
  claude setup-token
Verify headless works: `claude -p "print ok"` returns without prompting.

## 3. Secrets
Store the Resend key: `keys set RESEND_API_KEY` (paste value). Always launch
autopilot under: `keys exec RESEND_API_KEY -- .venv/bin/python -m sermon_cut auto …`

## 4. Run by hand (before scheduling)
  keys exec RESEND_API_KEY -- .venv/bin/python -m sermon_cut auto discover latest
Confident weeks render + upload Unlisted and email you; unsure weeks email for
approval and wait. Approve with:
  keys exec RESEND_API_KEY -- .venv/bin/python -m sermon_cut auto render <video_id>
Then Phase 3 shorts:
  keys exec RESEND_API_KEY -- .venv/bin/python -m sermon_cut auto shorts <video_id>

(The weekly cron and the web dashboard are the follow-on plan.)
```

- [ ] **Step 6: Run the full suite + commit**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS

```bash
git add sermon_cut/cli.py sermon_cut/config.py config.example.toml docs/homelab-setup.md tests/auto/test_orchestrator.py
git commit -m "feat(auto): CLI auto subcommands + homelab setup doc"
```

---

## Out of scope (follow-on plan)

- The **web dashboard** (status views, approve/edit/run-now/retry) — spec Phase 5.
- The **weekly cron scheduler** and a `shorts-select` CLI wrapper extracting the
  `/sermon-shorts` auto-selection into a subcommand — spec Phase 6.
- These build on the status DB + orchestrator delivered here and get their own
  plan once this core is proven by the by-hand dry run.

## Self-Review notes

- Spec coverage: status store (Task 1), confidence gate (Task 2), Resend notify
  (Task 3), unattended judgment skill (Task 4), three-phase orchestrator incl.
  Shorts-as-Phase-3 (Task 5), CLI + homelab OAuth/headless/secrets (Task 6).
  Dashboard + scheduler explicitly deferred.
- Invariants: privacy stays `unlisted` (upload stage unchanged); audio-only
  discovery preserved (orchestrator calls existing `fetch-audio`/`clip`); heavy
  deps untouched; all new tests offline via injected runner/judge/notifier/opener.
