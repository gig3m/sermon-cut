from __future__ import annotations

from datetime import datetime, timedelta

from . import status, watcher


def seconds_until_next_fire(now: datetime, fire_hours=(0, 12)) -> float:
    """Seconds from `now` to the next wall-clock hour in `fire_hours`.

    Always strictly positive — landing exactly on a fire hour advances to the
    next one, so a zero-length sleep can never spin the loop.
    """
    hours = sorted(fire_hours)
    for h in hours:
        candidate = now.replace(hour=h, minute=0, second=0, microsecond=0)
        if candidate > now:
            return (candidate - now).total_seconds()
    # past the last fire hour today -> first fire hour tomorrow
    first = now.replace(hour=hours[0], minute=0, second=0, microsecond=0)
    return (first + timedelta(days=1) - now).total_seconds()


def run_scheduler(*, enumerate_streams, db_path, now_fn, sleep,
                  stop=None, fire_hours=(0, 12)) -> None:
    """Sleep until the next fire hour, scan the channel, repeat.

    `sleep`/`now_fn`/`stop` are injected so the loop is testable offline.
    `now_fn` returns a `datetime` (for the sleep math); it is stringified to
    ISO for `scan_channel`, which records it via `status.upsert_run(now: str)`.
    Each cycle opens its own sqlite connection (thread-local safety).
    """
    while stop is None or not stop.is_set():
        sleep(seconds_until_next_fire(now_fn(), fire_hours))
        if stop is not None and stop.is_set():
            break
        conn = status.connect(db_path)
        try:
            watcher.scan_channel(
                enumerate_streams=enumerate_streams,
                conn=conn,
                now=now_fn().isoformat(),
            )
        finally:
            conn.close()
