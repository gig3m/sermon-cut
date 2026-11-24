import json
from pathlib import Path
from sermon_cut.auto import status, orchestrator
from sermon_cut.auto.title_vision import SlideRead


def _write(p, obj): p.parent.mkdir(parents=True, exist_ok=True); p.write_text(json.dumps(obj))


def _draft(title=""):
    return {"rough_start": 3800.0, "rough_end": 5600.0,
            "in_phrase": "good morning", "out_phrase": "amen",
            "title": title, "speaker": "Clay Thornton", "date": "2026-08-16",
            "description": "d", "chapter_topics": [{"phrase": "first point", "label": "First"}]}


def _ctx(tmp_path, verdict, calls=None, draft_title=""):
    calls = calls if calls is not None else []
    conn = status.connect(":memory:")
    def runner(args):
        calls.append([str(a) for a in args])
        if args[0] == "upload":
            _write(tmp_path / "runs" / "vid" / "upload.json",
                   {"video_id": "SERM", "url": "https://youtu.be/SERM"})
        return type("R", (), {"stdout": "", "returncode": 0})()
    def judge(wd):
        _write(Path(wd) / "auto_verdict.json", verdict)
        if "skip" not in verdict:
            _write(Path(wd) / "plan_draft.json", _draft(draft_title))
    render_kw = {}
    # fake durable render: writes sermon.mp4 + returns pinned boundaries
    def render_sermon(url, audio, wd, *, in_phrase, out_phrase, rough_start, rough_end, **kw):
        from sermon_cut.auto.render_robust import RenderResult
        render_kw.update(kw)
        (Path(wd) / "sermon.mp4").write_bytes(b"x")
        return RenderResult(Path(wd) / "sermon.mp4", 3799.0, 3800.0, 5600.0)
    def repin(sermon, topics, phys_start, wd, **kw):
        return [{"time": 3860, "label": topics[0]["label"]}]
    title_reader = lambda sermon, wd, **kw: SlideRead("Read Title", "high")
    def thumb_setter(video_id, wd): calls.append(["THUMB", str(video_id)])
    return dict(conn=conn, runner=runner, judge=judge, render_sermon=render_sermon,
                repin=repin, title_reader=title_reader, thumb_setter=thumb_setter,
                calls=calls, render_kw=render_kw)


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
    # Regression: the CLI dispatcher `runner` (python -m sermon_cut <args>) must
    # NOT be threaded into render_sermon, which runs raw yt-dlp/ffmpeg with a
    # subprocess.run-style `runner(cmd, check=True)`. Passing the CLI runner in
    # crashed real renders with "_default_runner() got an unexpected keyword
    # argument 'check'".
    assert "runner" not in c["render_kw"]


def test_low_confidence_still_renders_note_logged(tmp_path, monkeypatch):
    c = _ctx(tmp_path, {"in_point_ok": False, "out_point_ok": True,
                        "no_reading_bleed": False, "note": "shaky"})
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
        thumb_setter=c["thumb_setter"], title_reader=lambda *a, **k: SlideRead("VISION", "high"))
    plan = json.loads((tmp_path / "runs" / "vid" / "plan.json").read_text())
    assert plan["title"] == "Form Title" and plan["speaker"] == "Guest Bob"


def test_low_confidence_slide_read_uses_the_drafted_title(tmp_path, monkeypatch):
    # The slide said "Jesus" (a one-word series header); the transcript draft is
    # descriptive. Low confidence in the match -> the draft wins.
    c = _ctx(tmp_path, {"in_point_ok": True, "out_point_ok": True,
                        "no_reading_bleed": True, "note": "clean"},
             draft_title="Christ Is Your Life")
    monkeypatch.setattr("sermon_cut.auto.render_robust.render_sermon", c["render_sermon"])
    monkeypatch.setattr("sermon_cut.auto.render_robust.repin_chapters", c["repin"])
    seen = {}
    def reader(sermon, wd, **kw):
        seen.update(kw); return SlideRead("Jesus", "low")
    orchestrator.discover_decide(
        "https://youtu.be/vid", root=tmp_path, conn=c["conn"], now="t0",
        runner=c["runner"], judge=c["judge"], resolve_id=lambda u: "vid",
        thumb_setter=c["thumb_setter"], title_reader=reader)
    plan = json.loads((tmp_path / "runs" / "vid" / "plan.json").read_text())
    assert plan["title"] == "Christ Is Your Life"
    assert seen["draft_title"] == "Christ Is Your Life"   # the judge compares against it


def test_high_confidence_slide_read_wins_over_the_draft(tmp_path, monkeypatch):
    c = _ctx(tmp_path, {"in_point_ok": True, "out_point_ok": True,
                        "no_reading_bleed": True, "note": "clean"},
             draft_title="God Knows Your Name")
    monkeypatch.setattr("sermon_cut.auto.render_robust.render_sermon", c["render_sermon"])
    monkeypatch.setattr("sermon_cut.auto.render_robust.repin_chapters", c["repin"])
    orchestrator.discover_decide(
        "https://youtu.be/vid", root=tmp_path, conn=c["conn"], now="t0",
        runner=c["runner"], judge=c["judge"], resolve_id=lambda u: "vid",
        thumb_setter=c["thumb_setter"],
        title_reader=lambda s, wd, **kw: SlideRead("The God Who Knows Every Name", "high"))
    plan = json.loads((tmp_path / "runs" / "vid" / "plan.json").read_text())
    assert plan["title"] == "The God Who Knows Every Name"


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


def test_render_publish_error_sets_error_status(tmp_path):
    conn = status.connect(":memory:")
    status.upsert_run(conn, "vid", now="t0", status="discovering")
    _write(tmp_path / "runs" / "vid" / "plan_draft.json", _draft())
    def boom(video_id, wd):
        raise RuntimeError("thumbnail service down")
    import pytest
    with pytest.raises(RuntimeError):
        orchestrator.render_publish(
            "vid", root=tmp_path, conn=conn, now="t1",
            thumb_setter=boom)
    run = status.get_run(conn, "vid")
    assert run.status == "error" and "thumbnail service down" in run.error


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
