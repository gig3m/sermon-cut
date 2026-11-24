from __future__ import annotations

from . import status


def _default_orchestrate(run, *, conn, root, now):
    from . import orchestrator
    final = orchestrator.discover_decide(
        f"https://www.youtube.com/watch?v={run.video_id}", root=root, conn=conn, now=now,
        speaker=run.speaker, title=run.title_override, options=run.options,
        resolve_id=lambda u: run.video_id)
    if final == "ready_for_review" and not (run.options or {}).get("skip_shorts"):
        orchestrator.make_shorts(run.video_id, root=root, conn=conn, now=now, options=run.options)


def process_one(conn, *, root, now, orchestrate=None) -> str | None:
    orchestrate = orchestrate or _default_orchestrate
    run = status.claim_next(conn, now=now)
    if run is None:
        return None
    try:
        orchestrate(run, conn=conn, root=root, now=now)
    except Exception as e:  # keep the worker alive; the run is marked failed
        status.upsert_run(conn, run.video_id, now=now, status="error", error=str(e))
    return run.video_id


def serve_forever(conn, *, root, now_fn, interval: float = 10.0, stop=None) -> None:
    import threading
    stop = stop or threading.Event()
    while not stop.is_set():
        did = process_one(conn, root=root, now=now_fn())   # real _default_orchestrate
        if did is None:                                    # empty queue → sleep, re-check stop
            stop.wait(interval)
