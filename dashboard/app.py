from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from sermon_cut.auto import status, watcher

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_app(*, db_path, root, enumerate_streams=None) -> FastAPI:
    app = FastAPI(title="Sermon Cut")
    app.state.db_path = db_path
    app.state.root = root
    app.state.enumerate_streams = enumerate_streams

    @app.get("/", response_class=HTMLResponse)
    def queue(request: Request, show: str = ""):
        conn = status.connect(app.state.db_path)
        all_runs = status.list_runs(conn); conn.close()
        active = [r for r in all_runs if r.status != "ignored"]
        ignored = [r for r in all_runs if r.status == "ignored"]
        return _TEMPLATES.TemplateResponse(request, "queue.html", {
            "runs": active,
            "ignored": ignored,
            "show_ignored": show == "ignored",
        })

    @app.get("/run/{video_id}", response_class=HTMLResponse)
    def detail(request: Request, video_id: str):
        conn = status.connect(app.state.db_path)
        run = status.get_run(conn, video_id); conn.close()
        if run is None:
            raise HTTPException(status_code=404)
        return _TEMPLATES.TemplateResponse(request, "detail.html", {"run": run})

    @app.post("/run/{video_id}/process")
    def process(video_id: str, speaker: str = Form(""), title: str = Form(""),
                skip_thumbnail: str = Form(None), skip_shorts: str = Form(None),
                force: str = Form(None)):
        options = {"skip_thumbnail": skip_thumbnail is not None,
                   "skip_shorts": skip_shorts is not None,
                   "force": force is not None}
        conn = status.connect(app.state.db_path)
        try:
            status.enqueue(conn, video_id, now=_now(), speaker=speaker or None,
                           title=title or None, options=options)
        finally:
            conn.close()
        return RedirectResponse(f"/run/{video_id}", status_code=303)

    @app.post("/run/{video_id}/retry")
    def retry(video_id: str):
        conn = status.connect(app.state.db_path)
        try:
            run = status.get_run(conn, video_id)
            if run is None:
                raise HTTPException(status_code=404)
            status.enqueue(conn, video_id, now=_now(), speaker=run.speaker,
                           title=run.title_override, options=run.options)
        finally:
            conn.close()
        return RedirectResponse(f"/run/{video_id}", status_code=303)

    @app.post("/run/{video_id}/ignore")
    def ignore(video_id: str):
        conn = status.connect(app.state.db_path)
        try:
            if status.get_run(conn, video_id) is None:
                raise HTTPException(status_code=404)
            status.upsert_run(conn, video_id, now=_now(), status="ignored")
        finally:
            conn.close()
        return RedirectResponse("/", status_code=303)

    @app.post("/run/{video_id}/restore")
    def restore(video_id: str):
        conn = status.connect(app.state.db_path)
        try:
            if status.get_run(conn, video_id) is None:
                raise HTTPException(status_code=404)
            status.upsert_run(conn, video_id, now=_now(), status="discovered")
        finally:
            conn.close()
        return RedirectResponse("/?show=ignored", status_code=303)

    @app.post("/ignore-discovered")
    def ignore_discovered():
        conn = status.connect(app.state.db_path)
        try:
            status.ignore_all_discovered(conn, now=_now())
        finally:
            conn.close()
        return RedirectResponse("/", status_code=303)

    @app.post("/rescan")
    def rescan():
        if app.state.enumerate_streams is not None:
            conn = status.connect(app.state.db_path)
            try:
                watcher.scan_channel(enumerate_streams=app.state.enumerate_streams,
                                     conn=conn, now=_now())
            finally:
                conn.close()
        return RedirectResponse("/", status_code=303)

    return app
