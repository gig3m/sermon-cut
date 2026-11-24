from __future__ import annotations
import argparse
import json
import subprocess
import sys
from pathlib import Path

from .fetch_audio import fetch_audio, resolve_latest
from .transcribe import transcribe
from .plan import load_plan, validate_plan, segment_within_band
from .clip_video import clip_video
from .intro import make_bumper
from .title_card import build_title_card
from .assemble import assemble
from .upload import (
    build_description, upload_video, VideoMetadata, format_video_title,
    write_upload_receipt, load_upload_receipt, format_shorts_title,
    build_shorts_description,
)
from .config import load_config
from .shorts_select import (
    load_candidates, candidate_band, validate_candidate, source_to_clip_time,
)
from .shorts_render import (
    window_bounds, extract_window, build_vertical_outro_card, assemble_short,
)
from .shorts_track import measure_centers, smooth_camera, crop_x_from_center, render_tracked_body
from .shorts_captions import render_overlay
from .transcribe import transcribe_words
from .livestream_thumbs import run as livestream_run


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sermon_cut")
    # Shared parent so every subcommand accepts --workdir AFTER the subcommand name.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workdir", default=".")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("fetch-audio", parents=[common]); s.add_argument("url")
    s = sub.add_parser("resolve-latest", parents=[common]); s.add_argument("channel_id")
    s = sub.add_parser("transcribe", parents=[common]); s.add_argument("--audio", required=True); s.add_argument("--model"); s.add_argument("--config", default="config.toml")
    s = sub.add_parser("validate-plan", parents=[common]); s.add_argument("--plan", required=True)
    s = sub.add_parser("clip", parents=[common]); s.add_argument("url"); s.add_argument("--start", type=float, required=True); s.add_argument("--end", type=float, required=True); s.add_argument("--front-padding", type=int, default=1); s.add_argument("--back-padding", type=int, default=4)
    s = sub.add_parser("intro", parents=[common]); s.add_argument("--image"); s.add_argument("--plan"); s.add_argument("--background", default="assets/intro_bg.png"); s.add_argument("--font-dir", default="assets/fonts"); s.add_argument("--seconds", type=int, default=5)
    s = sub.add_parser("outro", parents=[common]); s.add_argument("--image", required=True); s.add_argument("--seconds", type=int, default=5)
    s = sub.add_parser("assemble", parents=[common]); s.add_argument("--intro", required=True); s.add_argument("--sermon", required=True); s.add_argument("--outro"); s.add_argument("--dissolve", type=float, default=0.8); s.add_argument("--fade", type=float, default=0.6)
    s = sub.add_parser("upload", parents=[common]); s.add_argument("--video", required=True); s.add_argument("--plan", required=True); s.add_argument("--config", default="config.toml"); s.add_argument("--client-secrets", default="client_secrets.json"); s.add_argument("--token", default="token.json"); s.add_argument("--thumbnail")

    s = sub.add_parser("shorts-validate", parents=[common])
    s.add_argument("--candidates", required=True); s.add_argument("--config", default="config.toml")

    s = sub.add_parser("shorts-clip", parents=[common])
    s.add_argument("--sermon", required=True); s.add_argument("--plan", required=True)
    s.add_argument("--candidate", required=True); s.add_argument("--candidates", required=True)
    s.add_argument("--config", default="config.toml")

    s = sub.add_parser("shorts-render", parents=[common])
    s.add_argument("--plan", required=True); s.add_argument("--candidate", required=True)
    s.add_argument("--candidates", required=True); s.add_argument("--config", default="config.toml")
    s.add_argument("--outro-card", default="assets/outro_card.png")
    s.add_argument("--font-dir", default="assets/fonts")

    s = sub.add_parser("shorts-upload", parents=[common])
    s.add_argument("--video", required=True); s.add_argument("--candidate", required=True)
    s.add_argument("--candidates", required=True); s.add_argument("--plan", required=True)
    s.add_argument("--sermon-run", required=True); s.add_argument("--config", default="config.toml")
    s.add_argument("--client-secrets", default="client_secrets.json"); s.add_argument("--token", default="token.json")

    s = sub.add_parser("livestream-thumbs", parents=[common])
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--ids"); s.add_argument("--limit", type=int)
    s.add_argument("--config", default="config.toml")
    s.add_argument("--background", default="assets/intro_bg_ink.png")
    s.add_argument("--font-dir", default="assets/fonts")
    s.add_argument("--client-secrets", default="client_secrets.json")
    s.add_argument("--token", default="token.json")

    a = sub.add_parser("auto")
    asub = a.add_subparsers(dest="auto_cmd", required=True)
    for name, idarg in (("discover", "url"), ("render", "video_id"), ("shorts", "video_id")):
        sp = asub.add_parser(name, parents=[common])
        sp.add_argument(idarg)
        sp.add_argument("--db", default="auto.db")
        sp.add_argument("--root", default=".")
        sp.add_argument("--config", default="config.toml")
    return p


def _ls_enumerate(channel_id, *, runner=subprocess.run):
    """Yield (video_id, title) for every stream on the channel's /streams tab."""
    url = f"https://www.youtube.com/channel/{channel_id}/streams"
    proc = runner(["yt-dlp", "--flat-playlist", "--dump-json", url],
                  check=True, capture_output=True, text=True)
    for line in proc.stdout.splitlines():
        v = json.loads(line)
        yield v.get("id"), v.get("title", "")


def _ls_fetch_thumbnail(video_id):
    """The video's primary public thumbnail bytes (maxres, hq fallback), or None."""
    import http.client
    import urllib.request
    for name in ("maxresdefault.jpg", "hqdefault.jpg"):
        try:
            with urllib.request.urlopen(
                    urllib.request.Request(f"https://i.ytimg.com/vi/{video_id}/{name}"),
                    timeout=20) as r:
                return r.read()
        except (OSError, http.client.HTTPException):
            # HTTPError/URLError are OSErrors, and so is the bare TimeoutError a
            # stalled read() raises (2026-11-15: it escaped and failed the run).
            # IncompleteRead/BadStatusLine are HTTPExceptions, not OSErrors.
            continue
    return None


def _find_candidate(candidates_path, slug):
    for c in load_candidates(candidates_path):
        if c.slug == slug:
            return c
    raise KeyError(f"no candidate with slug '{slug}' in {candidates_path}")


def _default_runner_capture(args, runner=subprocess.run) -> str:
    """Run `python -m sermon_cut <args>` and return captured stdout (mirrors
    orchestrator._default_runner, but for callers that just want the printed value)."""
    proc = runner([sys.executable, "-m", "sermon_cut", *map(str, args)],
                  check=True, capture_output=True, text=True)
    return proc.stdout


def _probe_fps(path, runner=subprocess.run) -> float:
    proc = runner(["ffprobe", "-v", "error", "-select_streams", "v:0",
                   "-show_entries", "stream=r_frame_rate", "-of",
                   "default=noprint_wrappers=1:nokey=1", str(path)],
                  check=True, capture_output=True, text=True)
    num, _, den = proc.stdout.strip().partition("/")
    return float(num) / float(den) if den else float(num)


def _configured_whisper_model(config_path: Path, default: str = "medium") -> str:
    if not config_path.exists():
        return default
    return load_config(config_path).whisper_model


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    workdir = Path(args.workdir)

    if args.cmd == "fetch-audio":
        print(fetch_audio(args.url, workdir).audio_path)
    elif args.cmd == "resolve-latest":
        print(resolve_latest(args.channel_id))
    elif args.cmd == "transcribe":
        # config.toml's whisper_model is the default so one setting governs both
        # this stage and the shorts word-transcribe; --model still wins, and a
        # missing config file falls back rather than failing the run.
        model = args.model or _configured_whisper_model(Path(args.config))
        print(transcribe(Path(args.audio), workdir, model_name=model).json_path)
    elif args.cmd == "validate-plan":
        plan = load_plan(Path(args.plan))
        validate_plan(plan)
        band = "IN_BAND" if segment_within_band(plan) else "OUT_OF_BAND"
        print(f"OK {band}")
    elif args.cmd == "clip":
        print(clip_video(args.url, args.start, args.end, workdir,
                         front_padding=args.front_padding,
                         back_padding=args.back_padding))
    elif args.cmd == "intro":
        if args.plan:
            plan = load_plan(Path(args.plan))
            image = build_title_card(plan, background_path=Path(args.background),
                                     font_dir=Path(args.font_dir), workdir=workdir)
        elif args.image:
            image = Path(args.image)
        else:
            build_parser().error("intro requires --plan or --image")
        print(make_bumper(image, workdir, seconds=args.seconds))
    elif args.cmd == "outro":
        print(make_bumper(Path(args.image), workdir, seconds=args.seconds,
                          out_name="outro.mp4"))
    elif args.cmd == "assemble":
        outro = Path(args.outro) if args.outro else None
        print(assemble(Path(args.intro), Path(args.sermon), workdir,
                       outro_path=outro, dissolve=args.dissolve, fade=args.fade))
    elif args.cmd == "upload":
        from .youtube_auth import get_authenticated_service
        cfg = load_config(Path(args.config))
        plan = load_plan(Path(args.plan))
        description = build_description(plan, cfg.description_footer,
                                       intro_seconds=cfg.intro_seconds,
                                       front_padding_seconds=cfg.front_padding_seconds,
                                       dissolve_seconds=cfg.dissolve_seconds)
        meta = VideoMetadata(title=format_video_title(plan), description=description,
                             tags=[cfg.church_name, "sermon", plan.speaker])
        yt = get_authenticated_service(Path(args.client_secrets), Path(args.token))
        thumbnail = Path(args.thumbnail) if args.thumbnail else workdir / "title_card.png"
        url = upload_video(Path(args.video), meta, youtube_client=yt,
                           thumbnail_path=thumbnail)
        write_upload_receipt(workdir, url.rsplit("/", 1)[-1], url)
        print(url)
    elif args.cmd == "shorts-validate":
        cfg = load_config(Path(args.config))
        for c in load_candidates(Path(args.candidates)):
            print(f"{c.slug} {candidate_band(c, cfg.shorts)}")
    elif args.cmd == "shorts-clip":
        cfg = load_config(Path(args.config))
        plan = load_plan(Path(args.plan))
        cand = _find_candidate(Path(args.candidates), args.candidate)
        validate_candidate(cand, cfg.shorts)
        clip_start = source_to_clip_time(cand.source_start, plan.start,
                                         cfg.front_padding_seconds)
        clip_end = source_to_clip_time(cand.source_end, plan.start,
                                       cfg.front_padding_seconds)
        ws, we = window_bounds(clip_start, clip_end)
        print(extract_window(Path(args.sermon), ws, we, workdir))
    elif args.cmd == "shorts-render":
        short_path = workdir / "short.mp4"
        if short_path.exists():
            print(short_path)
            return 0
        cfg = load_config(Path(args.config))
        plan = load_plan(Path(args.plan))
        cand = _find_candidate(Path(args.candidates), args.candidate)
        clip_start_sermon = source_to_clip_time(cand.source_start, plan.start,
                                                cfg.front_padding_seconds)
        clip_end_sermon = source_to_clip_time(cand.source_end, plan.start,
                                              cfg.front_padding_seconds)
        ws, _ = window_bounds(clip_start_sermon, clip_end_sermon)
        clip_start = clip_start_sermon - ws
        dur = clip_end_sermon - clip_start_sermon

        window_path = workdir / "window.mp4"
        words_path = transcribe_words(window_path, workdir, model_name=cfg.whisper_model)
        words = json.loads(words_path.read_text())
        overlay_path = render_overlay(words, workdir, cfg.shorts,
                                      clip_start=clip_start, dur=dur,
                                      font_dir=Path(args.font_dir))

        fps = _probe_fps(window_path)
        times, centers = measure_centers(window_path, clip_start, dur, cfg.shorts, fps=fps)
        smoothed = smooth_camera(centers, cfg.shorts)
        xmax = 1920 - cfg.shorts.crop_w
        crop_x = crop_x_from_center(smoothed, cfg.shorts.crop_w, xmax)

        body_path = workdir / "body.mp4"
        render_tracked_body(window_path, overlay_path, times, crop_x, cfg.shorts,
                            clip_start=clip_start, dur=dur, out_path=body_path, fps=fps)

        outro_png = build_vertical_outro_card(Path(args.outro_card), workdir)
        short_path = assemble_short(body_path, outro_png, workdir, cfg.shorts)
        print(short_path)
    elif args.cmd == "shorts-upload":
        from .youtube_auth import get_authenticated_service
        cfg = load_config(Path(args.config))
        plan = load_plan(Path(args.plan))
        cand = _find_candidate(Path(args.candidates), args.candidate)
        receipt = load_upload_receipt(Path(args.sermon_run)) or {}
        title = format_shorts_title(cand.title, plan.title)
        description = build_shorts_description(cand.title, cand.rationale,
                                               cfg.description_footer,
                                               sermon_url=receipt.get("url"))
        meta = VideoMetadata(title=title, description=description,
                             tags=[cfg.church_name, "Shorts", plan.speaker],
                             privacy="unlisted")
        yt = get_authenticated_service(Path(args.client_secrets), Path(args.token))
        url = upload_video(Path(args.video), meta, youtube_client=yt)
        write_upload_receipt(workdir, url.rsplit("/", 1)[-1], url)
        print(url)
    elif args.cmd == "livestream-thumbs":
        from .title_card import build_livestream_card
        cfg = load_config(Path(args.config))
        out_dir = Path("runs/livestream-thumbs")

        def render(label, out_path):
            return build_livestream_card(label, background_path=Path(args.background),
                                         font_dir=Path(args.font_dir), out_path=out_path)

        setter = lambda video_id, png: None
        if not args.dry_run:
            from .youtube_auth import get_authenticated_service
            from googleapiclient.http import MediaFileUpload
            from googleapiclient.errors import HttpError
            yt = get_authenticated_service(Path(args.client_secrets), Path(args.token))
            # YouTube throttles thumbnails.set after a burst (HTTP 429). Once we
            # hit it, every remaining set will 429 too — short-circuit so we don't
            # fire dozens of doomed calls; the run resumes cleanly later (already-
            # carded videos are detected and skipped).
            rate_limited = {"hit": False}

            def setter(video_id, png):
                if rate_limited["hit"]:
                    raise RuntimeError("skipped: thumbnail rate limit already hit this run")
                try:
                    yt.thumbnails().set(
                        videoId=video_id,
                        media_body=MediaFileUpload(str(png), mimetype="image/png"),
                    ).execute()
                except HttpError as e:
                    if getattr(e, "resp", None) is not None and e.resp.status == 429:
                        rate_limited["hit"] = True
                    raise

        results = livestream_run(
            enumerate_streams=lambda: _ls_enumerate(cfg.youtube_channel_id),
            fetch_thumbnail=_ls_fetch_thumbnail,
            render=render, set_thumbnail=setter, out_dir=out_dir,
            dry_run=args.dry_run,
            ids=args.ids.split(",") if args.ids else None, limit=args.limit)
        for r in results:
            line = (f"  {r.video_id}  {r.label:<30}  render={'ok' if r.rendered else 'X'}  "
                    f"set={'ok' if r.set_ok else ('-' if args.dry_run else 'X')}")
            if r.error and not args.dry_run:
                line += f"  ({r.error[:80]})"
            print(line)
        n_set = sum(1 for r in results if r.set_ok)
        print(f"DONE. {n_set}/{len(results)} set"
              f"{' (dry-run: nothing set)' if args.dry_run else ''}")
        if any("rate limit" in r.error.lower() or "too many thumbnails" in r.error.lower()
               for r in results):
            remaining = len(results) - n_set
            print(f"NOTE: hit YouTube's thumbnail rate limit — {remaining} still "
                  f"need a card. Re-run `livestream-thumbs` later to resume "
                  f"(already-set videos are skipped).")
    elif args.cmd == "auto":
        from .auto import status, orchestrator
        cfg = load_config(Path(args.config))
        conn = status.connect(args.db)
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        if args.auto_cmd == "discover":
            def resolve_id(url):
                if url == "latest":
                    out = _default_runner_capture(["resolve-latest", cfg.youtube_channel_id])
                    url = out.strip()
                return url.rsplit("v=", 1)[-1].rsplit("/", 1)[-1]
            orchestrator.discover_decide(args.url, root=args.root, conn=conn, now=now,
                                         resolve_id=resolve_id)
        elif args.auto_cmd == "render":
            orchestrator.render_publish(args.video_id, root=args.root, conn=conn,
                                        now=now,
                                        source_url=f"https://www.youtube.com/watch?v={args.video_id}")
        elif args.auto_cmd == "shorts":
            orchestrator.make_shorts(args.video_id, root=args.root, conn=conn,
                                     now=now)
    return 0
