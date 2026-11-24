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


def test_ignore_all_discovered_only_touches_discovered():
    conn = status.connect(":memory:")
    status.upsert_run(conn, "d1", now="t0", status="discovered")
    status.upsert_run(conn, "d2", now="t0", status="discovered")
    status.upsert_run(conn, "done1", now="t0", status="done")
    status.upsert_run(conn, "err1", now="t0", status="error")
    n = status.ignore_all_discovered(conn, now="t1")
    assert n == 2
    assert status.get_run(conn, "d1").status == "ignored"
    assert status.get_run(conn, "d2").status == "ignored"
    assert status.get_run(conn, "done1").status == "done"
    assert status.get_run(conn, "err1").status == "error"


def test_ignored_is_a_valid_status():
    conn = status.connect(":memory:")
    status.upsert_run(conn, "v", now="t0", status="ignored")
    assert status.get_run(conn, "v").status == "ignored"
