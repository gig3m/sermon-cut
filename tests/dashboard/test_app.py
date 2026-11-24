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


def test_build_wires_app_worker_and_scheduler(tmp_path):
    from dashboard import server
    app, start_worker, start_scheduler = server.build(
        db_path=tmp_path / "auto.db", root=tmp_path, channel_id="TESTCH")
    # app has a real enumerate bound; the starters are callable but we don't run
    # the loops here
    assert callable(start_worker)
    assert callable(start_scheduler)
    assert app.state.enumerate_streams is not None


def test_ignore_route_tombstones_and_hides_from_queue(tmp_path):
    c = _client(tmp_path)
    r = c.post("/run/vid1/ignore", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    # vid1 no longer in the default queue; vid2 still there
    page = c.get("/").text
    assert "vid1" not in page and "vid2" in page
    assert "1 ignored" in page


def test_ignored_shown_and_restorable(tmp_path):
    c = _client(tmp_path)
    c.post("/run/vid1/ignore", follow_redirects=False)
    shown = c.get("/?show=ignored").text
    assert "vid1" in shown and "Restore" in shown
    r = c.post("/run/vid1/restore", follow_redirects=False)
    assert r.status_code == 303
    # back to discovered, visible in the default queue again
    assert "vid1" in c.get("/").text


def test_ignore_unknown_404(tmp_path):
    assert _client(tmp_path).post("/run/nope/ignore", follow_redirects=False).status_code == 404


def test_ignore_all_discovered_route(tmp_path):
    c = _client(tmp_path)
    r = c.post("/ignore-discovered", follow_redirects=False)
    assert r.status_code == 303
    page = c.get("/").text
    assert "vid1" not in page          # the only discovered row, now ignored
    assert "vid2" in page              # ready_for_review untouched
    assert "1 ignored" in page


def test_dismiss_button_only_on_discovered_rows(tmp_path):
    # vid1 discovered -> has a Dismiss form; vid2 ready -> no Dismiss
    page = _client(tmp_path).get("/").text
    assert "/run/vid1/ignore" in page
    assert "/run/vid2/ignore" not in page
