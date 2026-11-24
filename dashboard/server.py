from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from sermon_cut.auto import schedule, status, worker
from sermon_cut.cli import _ls_enumerate

from .app import create_app


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build(*, db_path, root, channel_id):
    app = create_app(db_path=db_path, root=root,
                     enumerate_streams=lambda: _ls_enumerate(channel_id))

    def start_worker():
        def _run():
            # Open the sqlite connection inside the worker thread itself —
            # sqlite3 connections can't be safely shared across threads.
            conn = status.connect(db_path)
            worker.serve_forever(conn, root=root, now_fn=_now)

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        return t

    def start_scheduler():
        def _run():
            # Twice-daily channel scan (local noon & midnight — the container's
            # TZ makes datetime.now() church-local) that populates the queue.
            schedule.run_scheduler(
                enumerate_streams=lambda: _ls_enumerate(channel_id),
                db_path=db_path,
                now_fn=lambda: datetime.now(),
                sleep=time.sleep,
            )

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        return t

    return app, start_worker, start_scheduler


def main():  # container entrypoint
    import uvicorn
    from sermon_cut.config import load_config

    cfg = load_config(Path("config.toml"))
    app, start_worker, start_scheduler = build(
        db_path=Path("auto.db"), root=Path("."),
        channel_id=cfg.youtube_channel_id)
    start_worker()
    start_scheduler()
    uvicorn.run(app, host="0.0.0.0", port=8890)


if __name__ == "__main__":
    main()
