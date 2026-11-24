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


def test_default_orchestrate_calls_discover_decide_and_conditionally_shorts(monkeypatch):
    from sermon_cut.auto import orchestrator

    calls = {"discover": [], "shorts": []}

    def fake_discover_decide(video_url, *, root, conn, now, speaker, title, options,
                              resolve_id):
        calls["discover"].append({
            "video_url": video_url, "root": root, "speaker": speaker,
            "title": title, "options": options, "resolved_id": resolve_id(video_url),
        })
        return "ready_for_review"

    def fake_make_shorts(video_id, *, root, conn, now, options):
        calls["shorts"].append({"video_id": video_id, "options": options})

    monkeypatch.setattr(orchestrator, "discover_decide", fake_discover_decide)
    monkeypatch.setattr(orchestrator, "make_shorts", fake_make_shorts)

    # Case 1: ready_for_review, no skip_shorts -> make_shorts called
    conn = status.connect(":memory:")
    status.upsert_run(conn, "v1", now="t0", status="discovered", speaker="Bob",
                      title_override="T1", options={})
    status.enqueue(conn, "v1", now="t1", speaker="Bob", title="T1", options={})
    run1 = status.get_run(conn, "v1")
    worker._default_orchestrate(run1, conn=conn, root="/tmp/root", now="t2")

    assert len(calls["discover"]) == 1
    d = calls["discover"][0]
    assert d["speaker"] == "Bob" and d["title"] == "T1" and d["options"] == {}
    assert d["resolved_id"] == "v1"
    assert len(calls["shorts"]) == 1
    assert calls["shorts"][0]["video_id"] == "v1"

    # Case 2: ready_for_review but skip_shorts=True -> make_shorts NOT called
    calls["shorts"].clear()
    conn2 = status.connect(":memory:")
    status.upsert_run(conn2, "v2", now="t0", status="discovered", options={"skip_shorts": True})
    status.enqueue(conn2, "v2", now="t1", options={"skip_shorts": True})
    run2 = status.get_run(conn2, "v2")
    worker._default_orchestrate(run2, conn=conn2, root="/tmp/root", now="t2")
    assert calls["shorts"] == []

    # Case 3: discover_decide returns something other than ready_for_review -> not called
    calls["shorts"].clear()

    def fake_discover_decide_skipped(video_url, *, root, conn, now, speaker, title,
                                      options, resolve_id):
        return "skipped"

    monkeypatch.setattr(orchestrator, "discover_decide", fake_discover_decide_skipped)
    conn3 = status.connect(":memory:")
    status.upsert_run(conn3, "v3", now="t0", status="discovered", options={})
    status.enqueue(conn3, "v3", now="t1", options={})
    run3 = status.get_run(conn3, "v3")
    worker._default_orchestrate(run3, conn=conn3, root="/tmp/root", now="t2")
    assert calls["shorts"] == []


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
