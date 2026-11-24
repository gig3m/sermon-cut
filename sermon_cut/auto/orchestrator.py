from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from . import render_robust, status
from .confidence import SelfAssessment, evaluate
from .title_vision import read_slide_title, resolve_title
from ..config import load_config
from ..shorts_select import select_short_moments


def _default_runner(args):
    return subprocess.run([sys.executable, "-m", "sermon_cut", *map(str, args)],
                          check=True, capture_output=True, text=True)


def _default_judge(workdir):
    subprocess.run(["claude", "-p", "/sermon-auto"], cwd=str(workdir), check=True)


def _default_thumb_setter(video_id, workdir):
    subprocess.run([sys.executable, "-m", "sermon_cut", "livestream-thumbs",
                    "--ids", str(video_id)], check=True)


def _read_receipt_url(workdir: Path) -> str | None:
    p = workdir / "upload.json"
    return json.loads(p.read_text())["url"] if p.exists() else None


def discover_decide(video_url, *, root, conn, now, speaker=None, title=None,
                    options=None, runner=_default_runner, judge=_default_judge,
                    resolve_id, thumb_setter=None, transcriber=None,
                    title_reader=None) -> str:
    root = Path(root)
    vid = resolve_id(video_url)
    try:
        fetch_url = (video_url if ("://" in video_url or "youtu" in video_url)
                     else f"https://www.youtube.com/watch?v={vid}")
        wd = root / "runs" / vid
        wd.mkdir(parents=True, exist_ok=True)
        status.upsert_run(conn, vid, now=now, status="discovering", stage="fetch_audio",
                          speaker=speaker, title_override=title, options=options)

        runner(["fetch-audio", fetch_url, "--workdir", wd])
        status.upsert_run(conn, vid, now=now, stage="transcribe")
        runner(["transcribe", "--audio", wd / "audio.m4a", "--workdir", wd])

        status.upsert_run(conn, vid, now=now, stage="decide")
        judge(wd)
        verdict = json.loads((wd / "auto_verdict.json").read_text())
        if verdict.get("skip"):
            status.upsert_run(conn, vid, now=now, status="skipped",
                              confidence_note=verdict.get("note", "skipped"))
            return "skipped"

        draft_path = wd / "plan_draft.json"
        draft = json.loads(draft_path.read_text())
        dur = draft["rough_end"] - draft["rough_start"]
        band = "IN_BAND" if 600 <= dur <= 2700 else "OUT_OF_BAND"
        assessment = SelfAssessment(
            in_point_ok=bool(verdict["in_point_ok"]),
            out_point_ok=bool(verdict["out_point_ok"]),
            no_reading_bleed=bool(verdict["no_reading_bleed"]),
            note=verdict["note"],
        )
        _confident, note = evaluate(band=band, assessment=assessment)

        status.upsert_run(conn, vid, now=now, status="rendering", stage="render",
                          plan_path=str(draft_path), confidence_note=note)
        return render_publish(vid, root=root, conn=conn, now=now, speaker=speaker,
                              title=title, options=options, runner=runner,
                              source_url=fetch_url, transcriber=transcriber,
                              title_reader=title_reader, thumb_setter=thumb_setter)
    except Exception as e:
        status.upsert_run(conn, vid, now=now, status="error", error=str(e))
        raise


def render_publish(video_id, *, root, conn, now, speaker=None, title=None,
                   options=None, runner=_default_runner, source_url=None,
                   transcriber=None, title_reader=None, thumb_setter=None) -> str:
    root = Path(root)
    wd = root / "runs" / video_id
    options = options or {}
    url = source_url or f"https://www.youtube.com/watch?v={video_id}"

    try:
        if not options.get("skip_thumbnail"):
            status.upsert_run(conn, video_id, now=now, stage="thumbnail")
            setter = thumb_setter or _default_thumb_setter
            setter(video_id, wd)

        draft = json.loads((wd / "plan_draft.json").read_text())

        status.upsert_run(conn, video_id, now=now, status="rendering", stage="render")
        # render_sermon runs raw yt-dlp/ffmpeg — it needs a subprocess.run-style
        # runner (raw commands + check=), NOT the `python -m sermon_cut <args>`
        # CLI dispatcher `runner` threaded through this function for the intro/
        # outro/assemble/upload subcommands. Let it keep its subprocess.run
        # default; only inject a transcriber for tests.
        kwargs = {}
        if transcriber is not None:
            kwargs["word_transcriber"] = transcriber
        result = render_robust.render_sermon(
            url, wd / "audio.m4a", wd,
            in_phrase=draft["in_phrase"], out_phrase=draft["out_phrase"],
            rough_start=draft["rough_start"], rough_end=draft["rough_end"],
            **kwargs)

        # Title policy: the transcript draft is always present; the on-screen
        # slide replaces it only when the judge is confident the two name the
        # same sermon. A form override beats both.
        draft_title = draft.get("title") or ""
        if title:
            resolved_title = title
        else:
            reader = title_reader or read_slide_title
            slide = reader(result.sermon_path, wd, draft_title=draft_title)
            resolved_title = resolve_title(draft_title, slide)

        chapters = render_robust.repin_chapters(
            result.sermon_path, draft["chapter_topics"], result.phys_start, wd)

        plan = {
            "start": result.sermon_start,
            "end": result.sermon_end,
            "title": resolved_title,
            "speaker": speaker or draft["speaker"],
            "date": draft["date"],
            "description": draft["description"],
            "chapters": chapters,
        }
        plan_path = wd / "plan.json"
        plan_path.write_text(json.dumps(plan))

        status.upsert_run(conn, video_id, now=now, stage="intro", plan_path=str(plan_path))
        runner(["intro", "--plan", plan_path, "--background", "assets/intro_bg.png", "--workdir", wd])
        runner(["outro", "--image", "assets/outro_card.png", "--workdir", wd])
        status.upsert_run(conn, video_id, now=now, stage="assemble")
        runner(["assemble", "--intro", wd / "intro.mp4", "--sermon", wd / "sermon.mp4",
                "--outro", wd / "outro.mp4", "--workdir", wd])
        status.upsert_run(conn, video_id, now=now, stage="upload")
        runner(["upload", "--video", wd / "final.mp4", "--plan", plan_path, "--workdir", wd])
        sermon_url = _read_receipt_url(wd)
        status.upsert_run(conn, video_id, now=now, status="ready_for_review",
                          stage="done", sermon_url=sermon_url)
        return "ready_for_review"
    except Exception as e:
        status.upsert_run(conn, video_id, now=now, status="error", error=str(e))
        raise


def make_shorts(video_id, *, root, conn, now, options=None,
                runner=_default_runner, shorts_judge=None) -> str:
    root = Path(root)
    wd = root / "runs" / video_id
    options = options or {}
    cand_path = wd / "shorts" / "candidates.json"
    plan_path = wd / "plan.json"
    sermon_path = wd / "sermon.mp4"
    transcript_path = wd / "transcript.json"
    status.upsert_run(conn, video_id, now=now, stage="shorts")

    try:
        urls: list[str] = []
        if not options.get("skip_shorts"):
            cfg = load_config("config.toml")
            plan = json.loads(plan_path.read_text())
            judge_kwargs = {}
            if shorts_judge is not None:
                judge_kwargs["judge"] = shorts_judge
            select_short_moments(transcript_path, plan, cand_path, cfg, **judge_kwargs)
            cands = json.loads(cand_path.read_text()).get("candidates", []) if cand_path.exists() else []
            for c in cands:
                if not c.get("chosen"):
                    continue
                slug = c["slug"]
                sdir = wd / "shorts" / slug
                runner(["shorts-clip", "--sermon", sermon_path, "--plan", plan_path,
                        "--candidate", slug, "--candidates", cand_path,
                        "--config", "config.toml", "--workdir", sdir])
                runner(["shorts-render", "--plan", plan_path, "--candidate", slug,
                        "--candidates", cand_path, "--config", "config.toml",
                        "--workdir", sdir])
                runner(["shorts-upload", "--video", sdir / "short.mp4", "--candidate", slug,
                        "--candidates", cand_path, "--plan", plan_path,
                        "--sermon-run", wd, "--config", "config.toml", "--workdir", sdir])
                url = _read_receipt_url(sdir)
                if url:
                    urls.append(url)

        status.upsert_run(conn, video_id, now=now, status="done", stage="done", shorts_urls=urls)
        return "done"
    except Exception as e:
        status.upsert_run(conn, video_id, now=now, status="error", error=str(e))
        raise
