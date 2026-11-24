import json
from pathlib import Path
import sermon_cut.cli as cli
import sermon_cut.youtube_auth as youtube_auth
from sermon_cut.config import Config


def test_validate_plan_subcommand_reports_band(tmp_path, capsys, monkeypatch):
    import json
    plan = {
        "start": 3000.0, "end": 4500.0, "title": "T", "speaker": "S",
        "date": "2026-06-28", "description": "d", "chapters": [],
    }
    p = tmp_path / "plan.json"; p.write_text(json.dumps(plan))
    rc = cli.main(["validate-plan", "--plan", str(p)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "OK" in out and "IN_BAND" in out


def _write_plan(tmp_path):
    import json
    plan = {
        "start": 4015.0, "end": 5690.0, "title": "Remember the Sabbath Day",
        "speaker": "Clay Thornton", "date": "2026-07-05", "description": "d",
        "chapters": [],
    }
    p = tmp_path / "plan.json"; p.write_text(json.dumps(plan))
    return p


def test_intro_subcommand_builds_title_card_from_plan(tmp_path, monkeypatch):
    planpath = _write_plan(tmp_path)
    calls = {}

    def fake_build(plan_obj, *, background_path, font_dir, workdir):
        calls["build"] = (plan_obj.title, Path(background_path), Path(font_dir))
        return Path(workdir) / "title_card.png"

    def fake_make_bumper(image, workdir, *, seconds=5, runner=None):
        calls["intro"] = (Path(image), int(seconds))
        return Path(workdir) / "intro.mp4"

    monkeypatch.setattr(cli, "build_title_card", fake_build)
    monkeypatch.setattr(cli, "make_bumper", fake_make_bumper)
    rc = cli.main(["intro", "--plan", str(planpath), "--background", "bg.png",
                   "--font-dir", "fonts", "--seconds", "5",
                   "--workdir", str(tmp_path)])
    assert rc == 0
    assert calls["build"][0] == "Remember the Sabbath Day"
    assert calls["build"][1] == Path("bg.png")
    # make_bumper loops the freshly-built card, not a static image
    assert calls["intro"] == (tmp_path / "title_card.png", 5)


def test_intro_subcommand_still_accepts_static_image(tmp_path, monkeypatch):
    calls = {}

    def fake_make_bumper(image, workdir, *, seconds=5, runner=None):
        calls["intro"] = (Path(image), int(seconds))
        return Path(workdir) / "intro.mp4"

    monkeypatch.setattr(cli, "make_bumper", fake_make_bumper)
    rc = cli.main(["intro", "--image", "assets/intro_card.png", "--seconds", "5",
                   "--workdir", str(tmp_path)])
    assert rc == 0
    assert calls["intro"] == (Path("assets/intro_card.png"), 5)


def test_outro_subcommand_builds_outro_bumper(tmp_path, monkeypatch):
    calls = {}

    def fake_make_bumper(image, workdir, *, seconds=5, out_name="intro.mp4", runner=None):
        calls["args"] = (Path(image), int(seconds), out_name)
        return Path(workdir) / out_name

    monkeypatch.setattr(cli, "make_bumper", fake_make_bumper)
    rc = cli.main(["outro", "--image", "assets/outro_card.png", "--seconds", "5",
                   "--workdir", str(tmp_path)])
    assert rc == 0
    assert calls["args"] == (Path("assets/outro_card.png"), 5, "outro.mp4")


def test_assemble_subcommand_passes_outro_and_transitions(tmp_path, monkeypatch):
    calls = {}

    def fake_assemble(intro, sermon, workdir, *, outro_path=None, dissolve=0.8,
                      fade=0.6, runner=None):
        calls["kw"] = (Path(intro), Path(sermon),
                       Path(outro_path) if outro_path else None, dissolve, fade)
        return Path(workdir) / "final.mp4"

    monkeypatch.setattr(cli, "assemble", fake_assemble)
    rc = cli.main(["assemble", "--intro", "i.mp4", "--sermon", "s.mp4",
                   "--outro", "o.mp4", "--dissolve", "0.8", "--fade", "0.6",
                   "--workdir", str(tmp_path)])
    assert rc == 0
    intro, sermon, outro, dissolve, fade = calls["kw"]
    assert outro == Path("o.mp4")
    assert dissolve == 0.8 and fade == 0.6


def test_clip_subcommand_dispatches(tmp_path, capsys, monkeypatch):
    called = {}

    def fake_clip(url, start, end, workdir, *, front_padding=1, back_padding=4,
                  runner=None):
        called["args"] = (url, start, end, Path(workdir), front_padding, back_padding)
        return Path(workdir) / "sermon.mp4"

    monkeypatch.setattr(cli, "clip_video", fake_clip)
    rc = cli.main(["clip", "https://youtu.be/v", "--start", "3000", "--end",
                   "4500", "--front-padding", "0", "--back-padding", "4",
                   "--workdir", str(tmp_path)])
    assert rc == 0
    assert called["args"][0] == "https://youtu.be/v"
    assert called["args"][1] == 3000.0
    assert called["args"][4] == 0 and called["args"][5] == 4
    assert str(tmp_path / "sermon.mp4") in capsys.readouterr().out


def test_upload_subcommand_writes_receipt_and_prints_url(tmp_path, capsys, monkeypatch):
    planpath = _write_plan(tmp_path)
    video = tmp_path / "final.mp4"; video.write_bytes(b"v")

    fake_cfg = Config(church_name="Test Church", youtube_channel_id="UC123",
                      default_speaker="Speaker", canva_template_id="x")
    monkeypatch.setattr(cli, "load_config", lambda path: fake_cfg)
    monkeypatch.setattr(youtube_auth, "get_authenticated_service",
                        lambda client_secrets, token: object())

    calls = {}

    def fake_upload_video(video_path, meta, *, youtube_client, thumbnail_path=None):
        calls["video_path"] = Path(video_path)
        return "https://youtu.be/newvid42"

    monkeypatch.setattr(cli, "upload_video", fake_upload_video)

    rc = cli.main(["upload", "--video", str(video), "--plan", str(planpath),
                   "--workdir", str(tmp_path)])
    out = capsys.readouterr().out

    assert rc == 0
    assert "https://youtu.be/newvid42" in out
    assert calls["video_path"] == video

    receipt = json.loads((tmp_path / "upload.json").read_text())
    assert receipt == {"video_id": "newvid42", "url": "https://youtu.be/newvid42"}


def test_cli_auto_discover_dispatches(tmp_path, monkeypatch):
    fake_cfg = Config(church_name="Test Church", youtube_channel_id="UC123",
                      default_speaker="Speaker", canva_template_id="x")
    monkeypatch.setattr(cli, "load_config", lambda path: fake_cfg)

    from sermon_cut.auto import orchestrator
    captured = {}

    def fake_discover_decide(video_url, **kwargs):
        captured["video_url"] = video_url
        captured["kwargs"] = kwargs
        return "ready_for_review"

    monkeypatch.setattr(orchestrator, "discover_decide", fake_discover_decide)

    rc = cli.main(["auto", "discover", "https://youtu.be/x",
                   "--db", str(tmp_path / "auto.db"), "--root", str(tmp_path)])

    assert rc == 0
    assert captured["video_url"] == "https://youtu.be/x"
    assert "notifier" not in captured["kwargs"]
    assert "resolve_id" in captured["kwargs"]


def _cands(tmp_path):
    p = tmp_path / "candidates.json"
    p.write_text(json.dumps({"candidates": [{
        "slug": "hook", "title": "Hook", "source_start": 3865.0,
        "source_end": 3925.0, "rationale": "r",
        "standalone_confidence": "high", "context_integrity": "ok",
        "chosen": True}]}))
    return p


def test_shorts_validate_reports_band(tmp_path, capsys):
    cands = _cands(tmp_path)
    cfg = tmp_path / "config.toml"
    cfg.write_text('church_name="C"\nyoutube_channel_id="Y"\n'
                   'default_speaker="S"\ncanva_template_id="T"\n')
    rc = cli.main(["shorts-validate", "--candidates", str(cands),
                   "--config", str(cfg)])
    out = capsys.readouterr().out
    assert rc == 0 and "hook" in out and "IN_BAND" in out


def test_shorts_clip_dispatches_extract(tmp_path, monkeypatch):
    cands = _cands(tmp_path)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"start": 3850.0, "end": 5610.0, "title": "Cities",
        "speaker": "Clay", "date": "2026-05-17", "description": "d", "chapters": []}))
    sermon = tmp_path / "sermon.mp4"; sermon.write_bytes(b"v")
    cfg = tmp_path / "config.toml"
    cfg.write_text('church_name="C"\nyoutube_channel_id="Y"\n'
                   'default_speaker="S"\ncanva_template_id="T"\n')
    calls = {}
    def fake_extract(sermon_path, ws, we, workdir, **kw):
        calls["bounds"] = (round(ws, 1), round(we, 1)); return workdir / "window.mp4"
    monkeypatch.setattr(cli, "extract_window", fake_extract)
    rc = cli.main(["shorts-clip", "--sermon", str(sermon), "--plan", str(plan),
                   "--candidate", "hook", "--candidates", str(cands),
                   "--config", str(cfg), "--workdir", str(tmp_path)])
    # clip time = 3865 - (3850 - 1) = 16.0 ; window pad 5 -> 11.0 .. (3925-3849)+5=81.0
    assert rc == 0 and calls["bounds"] == (11.0, 81.0)


def test_shorts_render_is_idempotent_when_short_already_exists(tmp_path, capsys, monkeypatch):
    cands = _cands(tmp_path)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"start": 3850.0, "end": 5610.0, "title": "Cities",
        "speaker": "Clay", "date": "2026-05-17", "description": "d", "chapters": []}))
    cfg = tmp_path / "config.toml"
    cfg.write_text('church_name="C"\nyoutube_channel_id="Y"\n'
                   'default_speaker="S"\ncanva_template_id="T"\n')

    short_path = tmp_path / "short.mp4"
    short_path.write_bytes(b"already-rendered")

    def boom(*args, **kwargs):
        raise AssertionError("heavy shorts-render work should be skipped when short.mp4 exists")

    monkeypatch.setattr(cli, "transcribe_words", boom)
    monkeypatch.setattr(cli, "render_overlay", boom)
    monkeypatch.setattr(cli, "measure_centers", boom)
    monkeypatch.setattr(cli, "render_tracked_body", boom)
    monkeypatch.setattr(cli, "assemble_short", boom)

    rc = cli.main(["shorts-render", "--plan", str(plan), "--candidate", "hook",
                   "--candidates", str(cands), "--config", str(cfg),
                   "--workdir", str(tmp_path)])
    out = capsys.readouterr().out

    assert rc == 0
    assert str(short_path) in out


def test_livestream_thumbs_parses():
    args = cli.build_parser().parse_args(
        ["livestream-thumbs", "--dry-run", "--limit", "2", "--ids", "a,b"])
    assert args.cmd == "livestream-thumbs"
    assert args.dry_run and args.limit == 2 and args.ids == "a,b"


def test_ls_enumerate_parses_ytdlp_lines():
    import json as _json

    def fake_runner(cmd, check, capture_output, text):
        class P:
            stdout = "\n".join(_json.dumps(d) for d in [
                {"id": "v1", "title": "Sunday Morning Class & Worship - July 5, 2026"},
                {"id": "v2", "title": "Wednesday Bible Class - June 4, 2025"},
            ])
        return P()

    rows = list(cli._ls_enumerate("UCxxxx", runner=fake_runner))
    assert rows == [("v1", "Sunday Morning Class & Worship - July 5, 2026"),
                    ("v2", "Wednesday Bible Class - June 4, 2025")]


def _minimal_cfg(tmp_path, extra=""):
    cfg = tmp_path / "config.toml"
    cfg.write_text('church_name="C"\nyoutube_channel_id="Y"\n'
                   'default_speaker="S"\ncanva_template_id="T"\n' + extra)
    return cfg


def test_transcribe_model_defaults_to_config(tmp_path, monkeypatch, capsys):
    cfg = _minimal_cfg(tmp_path, 'whisper_model="large-v3"\n')
    audio = tmp_path / "audio.m4a"; audio.write_bytes(b"a")
    seen = {}

    def fake_transcribe(path, workdir, *, model_name=None, **kw):
        seen["model"] = model_name
        return type("R", (), {"json_path": workdir / "transcript.json"})()

    monkeypatch.setattr(cli, "transcribe", fake_transcribe)
    rc = cli.main(["transcribe", "--audio", str(audio), "--workdir", str(tmp_path),
                   "--config", str(cfg)])
    assert rc == 0 and seen["model"] == "large-v3"


def test_transcribe_model_flag_overrides_config(tmp_path, monkeypatch):
    cfg = _minimal_cfg(tmp_path, 'whisper_model="large-v3"\n')
    audio = tmp_path / "audio.m4a"; audio.write_bytes(b"a")
    seen = {}

    def fake_transcribe(path, workdir, *, model_name=None, **kw):
        seen["model"] = model_name
        return type("R", (), {"json_path": workdir / "transcript.json"})()

    monkeypatch.setattr(cli, "transcribe", fake_transcribe)
    cli.main(["transcribe", "--audio", str(audio), "--workdir", str(tmp_path),
              "--config", str(cfg), "--model", "small"])
    assert seen["model"] == "small"


def test_transcribe_model_falls_back_when_config_missing(tmp_path, monkeypatch):
    audio = tmp_path / "audio.m4a"; audio.write_bytes(b"a")
    seen = {}

    def fake_transcribe(path, workdir, *, model_name=None, **kw):
        seen["model"] = model_name
        return type("R", (), {"json_path": workdir / "transcript.json"})()

    monkeypatch.setattr(cli, "transcribe", fake_transcribe)
    cli.main(["transcribe", "--audio", str(audio), "--workdir", str(tmp_path),
              "--config", str(tmp_path / "nope.toml")])
    assert seen["model"] == "medium"
