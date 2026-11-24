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


def test_enqueue_clears_stale_error():
    conn = status.connect(":memory:")
    status.upsert_run(conn, "v", now="t0", status="error", error="boom")
    status.enqueue(conn, "v", now="t1")
    r = status.get_run(conn, "v")
    assert not r.error
    assert r.status == "queued"


def test_connect_sets_busy_timeout():
    conn = status.connect(":memory:")
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
