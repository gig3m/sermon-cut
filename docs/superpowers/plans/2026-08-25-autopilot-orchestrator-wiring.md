# Autopilot Orchestrator-Wiring (Phase 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewire the autopilot orchestrator to drive the durable render (from the pipeline-durability plan) at interactive quality, run straight-to-Unlisted with error status, thread speaker/title/options, read the sermon title from the church's on-screen slide when not supplied, and add a discovery watcher that populates the queue.

**Architecture:** The `/sermon-auto` skill emits a **rough draft** (`plan_draft.json`: rough boundaries + in/out phrases + chapter topic-phrases + title/speaker/date/description). The orchestrator's render step feeds those to `render_sermon`/`repin_chapters` (built last plan), which produce frame-accurate values, and writes the **finalized** `plan.json` the existing intro/outro/assemble/upload stages already consume. `discover_decide` no longer holds for approval — it renders every sermon straight to Unlisted, records failures as `status=error`, and threads speaker/title/options. A `watcher.py` scans the channel and inserts `discovered` rows.

**Tech Stack:** Python 3.12, stdlib (sqlite3/json/subprocess); pytest offline with injected runner/judge/enumerate. Reuses `sermon_cut/auto/render_robust.py`, `drift.py`, `shorts_select.select_short_moments`, `status.py`, existing CLI stages.

**Spec:** `docs/superpowers/specs/2026-08-25-autopilot-webui-design.md` (Phase 2).

## Global Constraints

- Privacy is always `unlisted`; no path uploads Public (copied from spec).
- Discovery is audio-only; full-video download happens only inside `render_sermon` (copied from spec).
- Heavy deps and binaries NEVER imported at module top-level — invoked via injected runner/judge or lazy import (copied from spec).
- Tests run with only pytest, fully offline — no network, GPU, or real binaries (copied from spec).
- Straight-to-Unlisted: no `needs_approval` gate; the Unlisted upload is the only review point. The confidence self-assessment is a logged note, never a gate.
- New code additive under `sermon_cut/auto/`; existing pipeline stages unchanged.
- Every collaborator (runner, judge, notifier, enumerate, transcriber) is injected so the orchestrator is fully offline-testable.

---

## File Structure

- Modify `sermon_cut/auto/status.py` — add `speaker`, `title_override`, `options` columns + `discovered` status (migration-safe).
- Modify `.claude/skills/sermon-auto/SKILL.md` — emit `plan_draft.json` (rough boundaries + phrases + topics) instead of a finalized `plan.json`.
- Create `sermon_cut/auto/title_vision.py` — `read_slide_title(sermon_path, workdir, *, judge)` (extract frame → `claude -p` vision → title string).
- Rewrite `sermon_cut/auto/orchestrator.py` — `render_publish` uses the durable render + writes finalized plan.json; `discover_decide` straight-to-Unlisted + error status + speaker/title/options; `make_shorts` uses `select_short_moments`.
- Create `sermon_cut/auto/watcher.py` — `scan_channel(enumerate_streams, conn, now) -> list[str]` inserts `discovered` rows.
- Tests: extend `tests/auto/test_status.py`, `tests/auto/test_orchestrator.py`; create `tests/auto/test_title_vision.py`, `tests/auto/test_watcher.py`.

---

## Task 1: status.py schema extension

**Files:**
- Modify: `sermon_cut/auto/status.py`
- Test: `tests/auto/test_status.py` (add cases)

**Interfaces:**
- Consumes: existing `status.py` (`STATUSES`, `_COLUMNS`, `Run`, `connect`, `init_db`, `upsert_run`, `get_run`, `list_runs`).
- Produces:
  - `STATUSES` gains `"discovered"`.
  - `Run` dataclass gains `speaker: str | None`, `title_override: str | None`, `options: dict` (JSON-encoded in DB, default `{}`).
  - `upsert_run(..., speaker=None, title_override=None, options=None)` — `options` stored as JSON.
  - `init_db` creates the three new columns; a `_migrate(conn)` adds them to a pre-existing table via `ALTER TABLE ... ADD COLUMN` guarded by a column-existence check, so an old `auto.db` upgrades in place.

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/auto/test_status.py
def test_discovered_status_allowed():
    conn = status.connect(":memory:")
    status.upsert_run(conn, "v", now="t0", status="discovered")
    assert status.get_run(conn, "v").status == "discovered"


def test_speaker_title_options_round_trip():
    conn = status.connect(":memory:")
    status.upsert_run(conn, "v", now="t0", status="discovered",
                      speaker="Guest Preacher", title_override="My Title",
                      options={"skip_shorts": True})
    r = status.get_run(conn, "v")
    assert r.speaker == "Guest Preacher"
    assert r.title_override == "My Title"
    assert r.options == {"skip_shorts": True}


def test_options_defaults_to_empty_dict():
    conn = status.connect(":memory:")
    status.upsert_run(conn, "v", now="t0", status="discovered")
    assert status.get_run(conn, "v").options == {}


def test_migrate_adds_columns_to_old_table(tmp_path):
    import sqlite3
    db = tmp_path / "old.db"
    old = sqlite3.connect(str(db))
    old.execute("""CREATE TABLE runs (video_id TEXT PRIMARY KEY, status TEXT NOT NULL,
        stage TEXT, plan_path TEXT, confidence_note TEXT, sermon_url TEXT,
        shorts_urls TEXT NOT NULL DEFAULT '[]', error TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    old.execute("INSERT INTO runs (video_id,status,created_at,updated_at) VALUES ('old','done','t','t')")
    old.commit(); old.close()
    conn = status.connect(db)   # connect() runs init_db which must migrate
    r = status.get_run(conn, "old")
    assert r.status == "done" and r.speaker is None and r.options == {}
    status.upsert_run(conn, "old", now="t1", speaker="X")
    assert status.get_run(conn, "old").speaker == "X"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/auto/test_status.py -q`
Expected: FAIL (unknown status "discovered" / TypeError on `speaker=`)

- [ ] **Step 3: Implement the schema changes**

In `sermon_cut/auto/status.py`:
1. Add `"discovered"` to `STATUSES` (put it first, it's the initial state).
2. Add `"speaker", "title_override", "options"` to `_COLUMNS`.
3. Add fields to `Run`: `speaker: str | None`, `title_override: str | None`, `options: dict`.
4. In `init_db`, extend the `CREATE TABLE` with `speaker TEXT`, `title_override TEXT`, `options TEXT NOT NULL DEFAULT '{}'`, then call `_migrate(conn)`.
5. Add `_migrate`:

```python
def _migrate(conn: sqlite3.Connection) -> None:
    have = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
    for col, decl in (("speaker", "TEXT"), ("title_override", "TEXT"),
                      ("options", "TEXT NOT NULL DEFAULT '{}'")):
        if col not in have:
            conn.execute(f"ALTER TABLE runs ADD COLUMN {col} {decl}")
    conn.commit()
```

6. In `upsert_run`, add params `speaker=None, title_override=None, options=None`; in the `values` dict add `"speaker": speaker, "title_override": title_override, "options": None if options is None else json.dumps(options)`.
7. In `_row_to_run`, map `speaker=row["speaker"]`, `title_override=row["title_override"]`, `options=json.loads(row["options"]) if row["options"] else {}`.

(Follow the exact existing patterns for the insert column list, the `_COLUMNS` partial-update loop, and the row mapping — the three new columns thread through identically to the existing ones.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/auto/test_status.py -q`
Expected: PASS

- [ ] **Step 5: Full suite + commit**

Run: `.venv/bin/python -m pytest -q`

```bash
git add sermon_cut/auto/status.py tests/auto/test_status.py
git commit -m "feat(auto): status schema — speaker/title_override/options + discovered state + migration"
```

---

## Task 2: `/sermon-auto` skill emits a rough draft (plan_draft.json)

**Files:**
- Modify: `.claude/skills/sermon-auto/SKILL.md`

**Interfaces:**
- Produces (contract the orchestrator consumes): after `claude -p /sermon-auto` runs in `runs/<id>/`, the dir contains `runs/<id>/auto_verdict.json` (unchanged: `in_point_ok`/`out_point_ok`/`no_reading_bleed`/`note`, or `{"skip":true,"note":...}`) AND `runs/<id>/plan_draft.json` shaped:
  ```json
  {"rough_start": 3800.0, "rough_end": 5600.0,
   "in_phrase": "good morning open your bibles",
   "out_phrase": "in Christ's name amen",
   "title": "", "speaker": "Clay Thornton", "date": "2026-08-16",
   "description": "…2–4 sentences…",
   "chapter_topics": [{"phrase": "first point", "label": "First point"}, …]}
  ```
  `rough_start`/`rough_end` are approximate source seconds (drift is fine — the render re-pins). `in_phrase`/`out_phrase` are short DISTINCTIVE word sequences at the true in/out points (the preacher's greeting; the closing "Amen"), used for drift-free pinning. `chapter_topics` are 3–6 distinctive phrases (each unique within the sermon) + labels. `title` is `""` (the orchestrator reads the slide) unless the transcript itself states a clean sermon title.

This task ships a skill doc, not code; its contract is exercised by Task 4's orchestrator tests (which fake the judge) and the eventual dry run.

- [ ] **Step 1: Rewrite the skill**

Replace `.claude/skills/sermon-auto/SKILL.md` step 3 and the file list so it writes `plan_draft.json` with the fields above (instead of a finalized `plan.json` with absolute `start`/`end`/`chapters`). Keep steps 1, 2 (skip path), and 4 (auto_verdict.json) as-is. The new step 3:

````markdown
3. Otherwise write `runs/<id>/plan_draft.json` — a ROUGH draft the render step
   refines (do NOT pin exact frames; the pipeline re-transcribes to do that):
   - `rough_start`, `rough_end` (approximate source seconds; ±30s is fine),
   - `in_phrase`: a short, distinctive word sequence spoken right at the
     preacher's opening greeting (e.g. "good morning open your bibles to") —
     NOT from the pre-sermon reading,
   - `out_phrase`: a short distinctive sequence at the very end, ending on the
     closing prayer's "amen" (e.g. "in Christ's name amen"),
   - `title`: "" (leave blank — the render step reads the church's on-screen
     slide) UNLESS the transcript itself clearly names the sermon title,
   - `speaker`: config `default_speaker` unless the transcript clearly names a
     guest,
   - `date`: from `source.info.json` `upload_date` (`YYYY-MM-DD`),
   - `description`: 2–4 sentences,
   - `chapter_topics`: 3–6 `{ "phrase": "<distinctive phrase unique within the
     sermon>", "label": "<chapter label>" }` in order.
````

- [ ] **Step 2: Commit**

```bash
git add .claude/skills/sermon-auto/SKILL.md
git commit -m "feat(auto): sermon-auto emits rough plan_draft (phrases + topics) for durable render"
```

---

## Task 3: title-via-vision (`title_vision.py`)

**Files:**
- Create: `sermon_cut/auto/title_vision.py`
- Test: `tests/auto/test_title_vision.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (an injected `runner` for the frame extract, an injected `judge` for the vision call).
- Produces:
  - `read_slide_title(sermon_path, workdir, *, at: float = 3.0, runner=subprocess.run, judge=None) -> str` — extracts a frame at `at` seconds into the sermon (`ffmpeg -ss <at> -i sermon -frames:v 1 workdir/title_frame.png`) via `runner`, then calls `judge(frame_path) -> str` (default shells to `claude -p /sermon-read-title` and reads `workdir/slide_title.txt`); returns the stripped title. Returns `""` if the judge yields nothing.

- [ ] **Step 1: Write the failing test**

```python
# tests/auto/test_title_vision.py
from pathlib import Path
from sermon_cut.auto import title_vision


def test_read_slide_title_extracts_frame_then_judges(tmp_path):
    cmds = []
    def runner(cmd, check=None, **kw):
        cmds.append([str(c) for c in cmd])
        Path(cmd[-1]).write_bytes(b"png")   # ffmpeg writes the frame
        return type("P", (), {"returncode": 0})()
    def judge(frame_path):
        assert Path(frame_path).exists()
        return "  Remain in Me  "
    title = title_vision.read_slide_title(tmp_path / "sermon.mp4", tmp_path,
                                          at=3.0, runner=runner, judge=judge)
    assert title == "Remain in Me"
    ff = cmds[0]
    assert ff[0] == "ffmpeg" and ff[ff.index("-ss") + 1] == "3.0"
    assert "-frames:v" in ff and ff[ff.index("-frames:v") + 1] == "1"


def test_read_slide_title_blank_when_judge_empty(tmp_path):
    def runner(cmd, check=None, **kw):
        Path(cmd[-1]).write_bytes(b"png"); return type("P", (), {})()
    assert title_vision.read_slide_title(tmp_path / "s.mp4", tmp_path,
                                         runner=runner, judge=lambda p: "") == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/auto/test_title_vision.py -q`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Implement**

```python
# sermon_cut/auto/title_vision.py
from __future__ import annotations

import subprocess
from pathlib import Path


def _default_judge(frame_path):
    wd = Path(frame_path).parent
    subprocess.run(["claude", "-p", "/sermon-read-title"], cwd=str(wd), check=True)
    out = wd / "slide_title.txt"
    return out.read_text() if out.exists() else ""


def read_slide_title(sermon_path, workdir, *, at: float = 3.0,
                     runner=subprocess.run, judge=None) -> str:
    workdir = Path(workdir)
    frame = workdir / "title_frame.png"
    runner(["ffmpeg", "-y", "-ss", str(at), "-i", str(sermon_path),
            "-frames:v", "1", str(frame)], check=True)
    judge = judge or _default_judge
    return (judge(frame) or "").strip()
```

> The `/sermon-read-title` skill (a one-line vision prompt: "read the church's on-screen sermon-title slide in this frame; write just the title to slide_title.txt") is a trivial skill doc; create it as `.claude/skills/sermon-read-title/SKILL.md` in this task with that instruction so the default path is complete.

- [ ] **Step 4: Run test + full suite + commit**

Run: `.venv/bin/python -m pytest tests/auto/test_title_vision.py -q` then `.venv/bin/python -m pytest -q`

```bash
git add sermon_cut/auto/title_vision.py tests/auto/test_title_vision.py .claude/skills/sermon-read-title/SKILL.md
git commit -m "feat(auto): title-via-vision (read church slide title from a frame)"
```

---

## Task 4: rewrite orchestrator — durable render, straight-to-Unlisted, error status, speaker/title/options

**Files:**
- Modify: `sermon_cut/auto/orchestrator.py`
- Test: `tests/auto/test_orchestrator.py` (rewrite the flow tests)

**Interfaces:**
- Consumes: `status` (Task 1); `render_robust.render_sermon`/`repin_chapters` and `RenderResult`; `shorts_select.select_short_moments`; `title_vision.read_slide_title` (Task 3); the `plan_draft.json`/`auto_verdict.json` contract (Task 2).
- Produces:
  - `discover_decide(video_url, *, root, conn, now, speaker=None, title=None, options=None, runner=_default_runner, judge=_default_judge, resolve_id, thumb_setter=None, transcriber=None, title_reader=None, shorts_judge=None) -> str` — fetch-audio → transcribe → judge (writes plan_draft + verdict) → on skip, `status=skipped`; else set `status=rendering` and call `render_publish(...)`. Wraps the whole body: on any exception, `status.upsert_run(status="error", error=str(e))` and re-raise. No `needs_approval` path.
  - `render_publish(video_id, *, root, conn, now, speaker=None, title=None, options=None, runner, source_url, transcriber=None, title_reader=None) -> str` — (1) unless `options.skip_thumbnail`, render+set the dated livestream card via `thumb_setter`/CLI; (2) read `plan_draft.json`; (3) `render_sermon(source_url, audio, wd, in_phrase, out_phrase, rough_start, rough_end, ...)` → RenderResult; (4) `title` = form title or `title_reader(sermon, wd)`; (5) `repin_chapters(sermon, chapter_topics, phys_start, wd)`; (6) write finalized `plan.json` (`start`=sermon_start, `end`=sermon_end, `title`, `speaker`(override or draft), `date`, `description`, `chapters`); (7) intro/outro/assemble/upload via existing CLI stages; set `status=ready_for_review`, `sermon_url`.
  - `make_shorts(video_id, *, root, conn, now, options=None, runner, shorts_judge=None) -> str` — unless `options.skip_shorts`, call `select_short_moments(transcript, plan, candidates_path, cfg, judge=shorts_judge)` then the existing shorts-clip/render/upload loop; `status=done`, `shorts_urls`.
- The `notifier` parameter is REMOVED from all three (no email; the UI/DB is the surface).

- [ ] **Step 1: Write the failing tests** (rewrite `tests/auto/test_orchestrator.py`)

```python
# tests/auto/test_orchestrator.py  (replace the flow tests)
import json
from pathlib import Path
from sermon_cut.auto import status, orchestrator


def _write(p, obj): p.parent.mkdir(parents=True, exist_ok=True); p.write_text(json.dumps(obj))


def _draft():
    return {"rough_start": 3800.0, "rough_end": 5600.0,
            "in_phrase": "good morning", "out_phrase": "amen",
            "title": "", "speaker": "Clay Thornton", "date": "2026-08-16",
            "description": "d", "chapter_topics": [{"phrase": "first point", "label": "First"}]}


def _ctx(tmp_path, verdict, band="IN_BAND", calls=None):
    calls = calls if calls is not None else []
    conn = status.connect(":memory:")
    def runner(args):
        calls.append([str(a) for a in args])
        if args[0] == "validate-plan":
            return type("R", (), {"stdout": "OK " + band, "returncode": 0})()
        if args[0] == "upload":
            _write(tmp_path / "runs" / "vid" / "upload.json",
                   {"video_id": "SERM", "url": "https://youtu.be/SERM"})
        return type("R", (), {"stdout": "", "returncode": 0})()
    def judge(wd):
        _write(Path(wd) / "auto_verdict.json", verdict)
        if "skip" not in verdict:
            _write(Path(wd) / "plan_draft.json", _draft())
    # fake durable render: writes sermon.mp4 + returns pinned boundaries
    def render_sermon(url, audio, wd, *, in_phrase, out_phrase, rough_start, rough_end, **kw):
        from sermon_cut.auto.render_robust import RenderResult
        (Path(wd) / "sermon.mp4").write_bytes(b"x")
        return RenderResult(Path(wd) / "sermon.mp4", 3799.0, 3800.0, 5600.0)
    def repin(sermon, topics, phys_start, wd, **kw):
        return [{"time": 3860, "label": topics[0]["label"]}]
    title_reader = lambda sermon, wd, **kw: "Read Title"
    def thumb_setter(video_id, wd): calls.append(["THUMB", str(video_id)])
    return dict(conn=conn, runner=runner, judge=judge, render_sermon=render_sermon,
                repin=repin, title_reader=title_reader, thumb_setter=thumb_setter, calls=calls)


def test_confident_week_renders_straight_to_unlisted(tmp_path, monkeypatch):
    c = _ctx(tmp_path, {"in_point_ok": True, "out_point_ok": True,
                        "no_reading_bleed": True, "note": "clean"})
    monkeypatch.setattr("sermon_cut.auto.render_robust.render_sermon", c["render_sermon"])
    monkeypatch.setattr("sermon_cut.auto.render_robust.repin_chapters", c["repin"])
    final = orchestrator.discover_decide(
        "https://youtu.be/vid", root=tmp_path, conn=c["conn"], now="t0",
        runner=c["runner"], judge=c["judge"], resolve_id=lambda u: "vid",
        thumb_setter=c["thumb_setter"], title_reader=c["title_reader"])
    assert final == "ready_for_review"
    run = status.get_run(c["conn"], "vid")
    assert run.status == "ready_for_review" and run.sermon_url == "https://youtu.be/SERM"
    # finalized plan.json written with pinned boundaries + read title
    plan = json.loads((tmp_path / "runs" / "vid" / "plan.json").read_text())
    assert plan["start"] == 3800.0 and plan["end"] == 5600.0
    assert plan["title"] == "Read Title" and plan["speaker"] == "Clay Thornton"
    assert plan["chapters"] == [{"time": 3860, "label": "First"}]
    assert any(a[0] == "assemble" for a in c["calls"])
    assert not any(a[0] == "clip" for a in c["calls"])   # durable render, not windowed clip


def test_low_confidence_still_renders_note_logged(tmp_path, monkeypatch):
    c = _ctx(tmp_path, {"in_point_ok": False, "out_point_ok": True,
                        "no_reading_bleed": False, "note": "shaky"}, band="OUT_OF_BAND")
    monkeypatch.setattr("sermon_cut.auto.render_robust.render_sermon", c["render_sermon"])
    monkeypatch.setattr("sermon_cut.auto.render_robust.repin_chapters", c["repin"])
    final = orchestrator.discover_decide(
        "https://youtu.be/vid", root=tmp_path, conn=c["conn"], now="t0",
        runner=c["runner"], judge=c["judge"], resolve_id=lambda u: "vid",
        thumb_setter=c["thumb_setter"], title_reader=c["title_reader"])
    assert final == "ready_for_review"          # renders anyway
    run = status.get_run(c["conn"], "vid")
    assert run.status == "ready_for_review"
    assert run.confidence_note and "held" in run.confidence_note  # note logged, not gated


def test_form_title_overrides_vision(tmp_path, monkeypatch):
    c = _ctx(tmp_path, {"in_point_ok": True, "out_point_ok": True,
                        "no_reading_bleed": True, "note": "clean"})
    monkeypatch.setattr("sermon_cut.auto.render_robust.render_sermon", c["render_sermon"])
    monkeypatch.setattr("sermon_cut.auto.render_robust.repin_chapters", c["repin"])
    orchestrator.discover_decide(
        "https://youtu.be/vid", root=tmp_path, conn=c["conn"], now="t0",
        speaker="Guest Bob", title="Form Title",
        runner=c["runner"], judge=c["judge"], resolve_id=lambda u: "vid",
        thumb_setter=c["thumb_setter"], title_reader=lambda *a, **k: "VISION")
    plan = json.loads((tmp_path / "runs" / "vid" / "plan.json").read_text())
    assert plan["title"] == "Form Title" and plan["speaker"] == "Guest Bob"


def test_skip_service_marks_skipped(tmp_path):
    c = _ctx(tmp_path, {"skip": True, "note": "singing service"})
    final = orchestrator.discover_decide(
        "https://youtu.be/vid", root=tmp_path, conn=c["conn"], now="t0",
        runner=c["runner"], judge=c["judge"], resolve_id=lambda u: "vid",
        thumb_setter=c["thumb_setter"], title_reader=c["title_reader"])
    assert final == "skipped"
    assert not any(a[0] == "assemble" for a in c["calls"])


def test_error_sets_error_status(tmp_path):
    c = _ctx(tmp_path, {"in_point_ok": True, "out_point_ok": True,
                        "no_reading_bleed": True, "note": "x"})
    def boom(args):
        c["calls"].append([str(a) for a in args])
        if args[0] == "fetch-audio":
            raise RuntimeError("network down")
        return type("R", (), {"stdout": "", "returncode": 0})()
    import pytest
    with pytest.raises(RuntimeError):
        orchestrator.discover_decide(
            "https://youtu.be/vid", root=tmp_path, conn=c["conn"], now="t0",
            runner=boom, judge=c["judge"], resolve_id=lambda u: "vid",
            thumb_setter=c["thumb_setter"], title_reader=c["title_reader"])
    run = status.get_run(c["conn"], "vid")
    assert run.status == "error" and "network down" in run.error


def test_make_shorts_uses_select_and_marks_done(tmp_path):
    conn = status.connect(":memory:")
    status.upsert_run(conn, "vid", now="t0", status="ready_for_review")
    _write(tmp_path / "runs" / "vid" / "plan.json", {"start": 1})
    _write(tmp_path / "runs" / "vid" / "transcript.json", [])
    calls = []
    def runner(args):
        sargs = [str(a) for a in args]
        calls.append(sargs)
        if sargs[0] == "shorts-upload":            # fake the per-short receipt
            wd = Path(sargs[sargs.index("--workdir") + 1])
            _write(wd / "upload.json", {"video_id": "SH", "url": "https://youtu.be/SH"})
        return type("R", (), {"stdout": "", "returncode": 0})()
    def shorts_judge(transcript_path, plan):        # one chosen, in-band candidate
        return [{"slug": "clip1", "title": "C1", "source_start": 100.0, "source_end": 140.0,
                 "rationale": "r", "standalone_confidence": "high",
                 "context_integrity": "ok", "chosen": True}]
    final = orchestrator.make_shorts("vid", root=tmp_path, conn=conn, now="t1",
                                     runner=runner, shorts_judge=shorts_judge)
    assert final == "done"
    assert status.get_run(conn, "vid").shorts_urls == ["https://youtu.be/SH"]
    assert not any(a[0] == "shorts-select" for a in calls)  # uses select_short_moments, not CLI
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/auto/test_orchestrator.py -q`
Expected: FAIL (signature/behavior changes not yet implemented)

- [ ] **Step 3: Rewrite `orchestrator.py`**

Rewrite the three functions per the Interfaces block. Key implementation points (complete code an implementer fills against the tests):
- Remove the `notifier` param everywhere and the `needs_approval` branch.
- `discover_decide` wraps its body in `try/except Exception as e:` → `status.upsert_run(conn, vid, now=now, status="error", error=str(e))` then `raise`.
- After the judge, still compute `confidence.evaluate(band, assessment)` but only to derive the `confidence_note` (log it via `confidence_note=note`); do NOT branch on it — always call `render_publish`.
- `render_publish` reads `plan_draft.json`, calls `render_robust.render_sermon(...)` (imported at module top: `from . import render_robust` is fine — render_robust's own heavy deps stay lazy) and `render_robust.repin_chapters(...)`, resolves the title (`title` arg if truthy else `title_reader(sermon_path, wd)`), writes finalized `plan.json`, then runs the existing `intro`/`outro`/`assemble`/`upload` CLI stages via `runner` exactly as today.
- The dated-livestream-thumbnail step uses an injected `thumb_setter(video_id, wd)` (default: render the ink card + set it — mirror the interactive path / the existing `livestream-thumbs --ids` CLI call), guarded by `not options.get("skip_thumbnail")`.
- `make_shorts` guarded by `not options.get("skip_shorts")`; uses `select_short_moments` with the injected `shorts_judge`, then the existing shorts-clip/render/upload loop (unchanged from current code).

- [ ] **Step 4: Run tests + full suite**

Run: `.venv/bin/python -m pytest tests/auto/test_orchestrator.py -q` then `.venv/bin/python -m pytest -q`

- [ ] **Step 5: Commit**

```bash
git add sermon_cut/auto/orchestrator.py tests/auto/test_orchestrator.py
git commit -m "feat(auto): orchestrator uses durable render, straight-to-Unlisted, error status, speaker/title/options"
```

---

## Task 5: discovery watcher (`watcher.py`)

**Files:**
- Create: `sermon_cut/auto/watcher.py`
- Test: `tests/auto/test_watcher.py`

**Interfaces:**
- Consumes: `status` (Task 1); `sermon_cut.livestream_thumbs.is_sunday_service` (existing).
- Produces:
  - `scan_channel(*, enumerate_streams, conn, now) -> list[str]` — `enumerate_streams() -> Iterable[(video_id, title)]`; for each Sunday-service title (`is_sunday_service`) not already in the DB, insert a `discovered` row (storing nothing but id/status/timestamps); returns the list of newly-inserted ids. Idempotent: a re-scan inserts nothing for known ids.

- [ ] **Step 1: Write the failing test**

```python
# tests/auto/test_watcher.py
from sermon_cut.auto import status, watcher


def _streams():
    return [("v1", "Sunday Morning Class & Worship - August 16, 2026"),
            ("wed", "Wed Bible Class - August 12, 2026"),
            ("v2", "Sunday Morning Class & Worship - August 9, 2026")]


def test_scan_inserts_only_new_sunday_services():
    conn = status.connect(":memory:")
    new = watcher.scan_channel(enumerate_streams=_streams, conn=conn, now="t0")
    assert set(new) == {"v1", "v2"}                 # Wed class excluded
    assert status.get_run(conn, "v1").status == "discovered"
    assert status.get_run(conn, "wed") is None


def test_scan_is_idempotent():
    conn = status.connect(":memory:")
    watcher.scan_channel(enumerate_streams=_streams, conn=conn, now="t0")
    again = watcher.scan_channel(enumerate_streams=_streams, conn=conn, now="t1")
    assert again == []                              # nothing new second time
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/auto/test_watcher.py -q`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Implement**

```python
# sermon_cut/auto/watcher.py
from __future__ import annotations

from . import status
from ..livestream_thumbs import is_sunday_service


def scan_channel(*, enumerate_streams, conn, now) -> list[str]:
    new = []
    for vid, title in enumerate_streams():
        if not is_sunday_service(title):
            continue
        if status.get_run(conn, vid) is not None:
            continue
        status.upsert_run(conn, vid, now=now, status="discovered")
        new.append(vid)
    return new
```

> `is_sunday_service` is stdlib-safe (title regex). Confirm it imports without heavy deps when reading `livestream_thumbs.py`; if that module top-imports something heavy, import `is_sunday_service` lazily inside `scan_channel` instead.

- [ ] **Step 4: Run test + full suite + commit**

Run: `.venv/bin/python -m pytest tests/auto/test_watcher.py -q` then `.venv/bin/python -m pytest -q`

```bash
git add sermon_cut/auto/watcher.py tests/auto/test_watcher.py
git commit -m "feat(auto): discovery watcher populates the queue with new Sunday services"
```

---

## Out of scope (follow-on plans)

- **CLI wiring** for the new orchestrator params + a `watch` subcommand — folded into the Phase-3 web-UI plan (the UI is the real trigger surface; a thin CLI is a convenience the web plan adds).
- **Web UI** (Phase 3) and **Docker + NPM** (Phase 4), gated by the Phase-0 in-container `claude -p` spike.

## Self-Review notes

- Spec coverage (Phase 2): speaker/title/options = Task 1 (schema) + Task 4 (threading); title-via-vision = Task 3; durable-render rewire + livestream-thumb step + straight-to-Unlisted + error status = Task 4; discovery watcher = Task 5. The rough-draft contract enabling the durable render = Task 2.
- Invariants: full-download only inside `render_sermon` (Task 4 calls it, never `clip`); no `needs_approval`/email; all collaborators injected → offline tests; new modules stdlib at top level.
- Type consistency: `render_sermon`/`repin_chapters`/`RenderResult` used per the pipeline-durability plan's signatures; `select_short_moments(transcript, plan, out, cfg, *, judge)` per that plan; `status.upsert_run` new kwargs match Task 1; `is_sunday_service` per existing `livestream_thumbs.py`.
