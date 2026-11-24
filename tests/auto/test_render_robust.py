from pathlib import Path
from sermon_cut.auto import render_robust as rr


class Rec:
    """Fake runner: records commands and creates the -o/output file so idempotency
    and downstream existence checks behave like the real thing."""
    def __init__(self): self.cmds = []
    def __call__(self, cmd, check=None, **kw):
        self.cmds.append([str(c) for c in cmd])
        out = None
        if "-o" in cmd:
            out = cmd[cmd.index("-o") + 1]
        else:
            out = cmd[-1]  # ffmpeg: output is last arg
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(b"x")
        return type("P", (), {"returncode": 0})()


def test_full_download_builds_ytdlp_command(tmp_path):
    r = Rec()
    out = rr.full_download("https://youtu.be/x", tmp_path / "full.mp4", runner=r)
    assert out == tmp_path / "full.mp4" and out.exists()
    assert r.cmds[0][0] == "yt-dlp"
    assert "-f" in r.cmds[0] and "https://youtu.be/x" in r.cmds[0]


def test_full_download_idempotent(tmp_path):
    (tmp_path / "full.mp4").write_bytes(b"already")
    r = Rec()
    rr.full_download("https://youtu.be/x", tmp_path / "full.mp4", runner=r)
    assert r.cmds == []  # skipped


def test_mux_av_maps_video_and_audio_copy(tmp_path):
    r = Rec()
    rr.mux_av(tmp_path / "v.mp4", tmp_path / "a.m4a", tmp_path / "av.mp4", runner=r)
    cmd = r.cmds[0]
    assert cmd[0] == "ffmpeg"
    assert "-map" in cmd and "0:v:0" in cmd and "1:a:0" in cmd
    assert "-c" in cmd and "copy" in cmd


def test_cut_window_frame_accurate_reencode(tmp_path):
    r = Rec()
    rr.cut_window(tmp_path / "av.mp4", 3816.0, 1800.0, tmp_path / "sermon.mp4",
                  fps=30, runner=r)
    cmd = r.cmds[0]
    assert cmd[0] == "ffmpeg"
    assert cmd[cmd.index("-ss") + 1] == "3816.0"
    assert cmd[cmd.index("-t") + 1] == "1800.0"
    assert "fps=30" in " ".join(cmd)
    assert "libx264" in cmd


def test_extract_probe_mono_16k_wav(tmp_path):
    r = Rec()
    rr.extract_probe(tmp_path / "a.m4a", 100.0, 90.0, tmp_path / "p.wav", runner=r)
    cmd = r.cmds[0]
    assert cmd[cmd.index("-ar") + 1] == "16000"
    assert cmd[cmd.index("-ac") + 1] == "1"


import json


def _make_word_transcriber(mapping):
    """mapping: out_name -> list[{"word","start","end"}] (probe-relative seconds)."""
    def fake(audio_path, workdir, *, model_name="small", out_name="words.json"):
        from pathlib import Path
        p = Path(workdir) / out_name
        p.write_text(json.dumps(mapping[out_name]))
        return p
    return fake


def test_render_sermon_pins_boundaries_and_cuts(tmp_path):
    r = Rec()
    # start probe (base = rough_start - probe_pad = 3800-25 = 3775): greeting at rel 20 -> 3795
    start_words = [{"word": "reading", "start": 5.0, "end": 5.4},
                   {"word": "good", "start": 20.0, "end": 20.4},
                   {"word": "morning", "start": 20.4, "end": 20.9}]
    # end probe (base = rough_end - probe_pad = 5600-25 = 5575): amen at rel 30 -> 5605
    end_words = [{"word": "name", "start": 29.0, "end": 29.5},
                 {"word": "amen", "start": 30.0, "end": 30.6}]
    wt = _make_word_transcriber({"sp_words.json": start_words, "ep_words.json": end_words})
    res = rr.render_sermon(
        "https://youtu.be/x", tmp_path / "audio.m4a", tmp_path,
        in_phrase="good morning", out_phrase="amen",
        rough_start=3800.0, rough_end=5600.0, front_lead=1.0,
        probe_pad=25.0, runner=r, word_transcriber=wt)
    assert abs(res.sermon_start - 3795.0) < 0.01   # 3775 + 20.0
    assert abs(res.sermon_end - 5605.6) < 0.01     # 5575 + 30.6 (amen end)
    assert abs(res.phys_start - 3794.0) < 0.01     # start - front_lead
    # the cut command used phys_start and dur = sermon_end - phys_start
    cut = [c for c in r.cmds if c[0] == "ffmpeg" and "-vf" in c][0]
    assert cut[cut.index("-ss") + 1] == "3794.0"
    assert abs(float(cut[cut.index("-t") + 1]) - (5605.6 - 3794.0)) < 0.01
    assert res.sermon_path == tmp_path / "sermon.mp4"


def test_render_sermon_idempotent_when_sermon_exists(tmp_path):
    (tmp_path / "sermon.mp4").write_bytes(b"done")
    (tmp_path / "_render.json").write_text(
        '{"phys_start": 1.0, "sermon_start": 2.0, "sermon_end": 3.0}')
    r = Rec()
    # even with a transcriber that would KeyError, no work happens
    res = rr.render_sermon(
        "https://youtu.be/x", tmp_path / "audio.m4a", tmp_path,
        in_phrase="a", out_phrase="b", rough_start=1.0, rough_end=2.0,
        runner=r, word_transcriber=lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    assert res.sermon_path.exists()
    assert not any("-vf" in c for c in r.cmds)  # no cut ran
    assert res.phys_start == 1.0
    assert res.sermon_start == 2.0
    assert res.sermon_end == 3.0


def test_repin_chapters_uses_segment_transcriber(tmp_path):
    import types
    (tmp_path / "sermon.mp4").write_bytes(b"x")

    def seg_fake(audio_path, workdir, *, model_name="medium"):
        segs = [{"text": "the first point today", "start": 40.0},
                {"text": "let us pray now", "start": 300.0}]
        return types.SimpleNamespace(segments=segs)

    topics = [{"phrase": "first point", "label": "First point"},
              {"phrase": "let us pray", "label": "Closing prayer"}]
    out = rr.repin_chapters(tmp_path / "sermon.mp4", topics, phys_start=1000.0,
                            workdir=tmp_path, segment_transcriber=seg_fake)
    assert out == [{"time": 1040, "label": "First point"},
                   {"time": 1300, "label": "Closing prayer"}]


def test_repin_chapters_real_transcribe_default_no_clobber(tmp_path, monkeypatch):
    """Drives the REAL sermon_cut.transcribe.transcribe as the default
    segment_transcriber (offline, via a monkeypatched model factory), and
    proves repin_chapters does not clobber the run's own transcript.json."""
    import types
    from sermon_cut import transcribe as transcribe_mod

    class FakeSegment:
        def __init__(self, start, end, text):
            self.start, self.end, self.text = start, end, text

    class FakeModel:
        def transcribe(self, path, **kwargs):
            segs = [FakeSegment(40.0, 41.0, "the first point today"),
                    FakeSegment(300.0, 301.0, "let us pray now")]
            return iter(segs), None

    monkeypatch.setattr(transcribe_mod, "_default_model_factory",
                        lambda model_name: FakeModel())

    sermon = tmp_path / "sermon.mp4"
    sermon.write_bytes(b"x")
    # Sentinel: the run's real transcript.json must survive repin_chapters.
    (tmp_path / "transcript.json").write_text('["MAIN"]')

    topics = [{"phrase": "first point", "label": "First point"},
              {"phrase": "let us pray", "label": "Closing prayer"}]
    out = rr.repin_chapters(sermon, topics, phys_start=1000.0, workdir=tmp_path)

    assert out == [{"time": 1040, "label": "First point"},
                   {"time": 1300, "label": "Closing prayer"}]
    assert (tmp_path / "transcript.json").read_text() == '["MAIN"]'
