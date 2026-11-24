import threading
from datetime import datetime

from sermon_cut.auto import status
from sermon_cut.auto.schedule import run_scheduler, seconds_until_next_fire


def test_before_noon_fires_at_noon():
    now = datetime(2026, 8, 27, 9, 0, 0)  # 09:00
    assert seconds_until_next_fire(now) == 3 * 3600  # -> 12:00


def test_after_noon_fires_at_midnight():
    now = datetime(2026, 8, 27, 15, 0, 0)  # 15:00
    assert seconds_until_next_fire(now) == 9 * 3600  # -> next midnight


def test_exactly_on_a_fire_hour_goes_to_the_next_one():
    now = datetime(2026, 8, 27, 12, 0, 0)  # exactly noon
    assert seconds_until_next_fire(now) == 12 * 3600  # never 0 -> midnight


def test_just_before_midnight():
    now = datetime(2026, 8, 27, 23, 59, 0)
    assert seconds_until_next_fire(now) == 60


def test_run_scheduler_scans_each_cycle_until_stopped(tmp_path):
    db = tmp_path / "auto.db"
    status.connect(db).close()  # create schema

    streams = [("vidA", "Sunday Morning Worship 8-23-2026")]
    stop = threading.Event()
    calls = {"n": 0}

    def fake_sleep(_secs):
        calls["n"] += 1
        if calls["n"] >= 2:
            stop.set()

    run_scheduler(
        enumerate_streams=lambda: streams,
        db_path=db,
        now_fn=lambda: datetime(2026, 8, 27, 9, 0, 0),
        sleep=fake_sleep,
        stop=stop,
    )

    conn = status.connect(db)
    try:
        assert status.get_run(conn, "vidA") is not None
    finally:
        conn.close()
