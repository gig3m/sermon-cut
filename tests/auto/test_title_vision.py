import json
from pathlib import Path
from sermon_cut.auto import title_vision
from sermon_cut.auto.title_vision import SlideRead, resolve_title


def _runner_writes_frames(cmds, *, fail_at=()):
    def runner(cmd, check=None, **kw):
        cmds.append([str(c) for c in cmd])
        at = float(cmd[cmd.index("-ss") + 1])
        if at not in fail_at:
            Path(cmd[-1]).write_bytes(b"png")   # ffmpeg writes the frame
        return type("P", (), {"returncode": 1 if at in fail_at else 0})()
    return runner


def test_read_slide_title_samples_several_offsets_and_hands_draft_to_judge(tmp_path):
    cmds = []
    seen = {}
    def judge(workdir):
        ctx = json.loads((Path(workdir) / "title_context.json").read_text())
        seen.update(ctx)
        for f in ctx["frames"]:
            assert Path(f).exists()
        return {"slide_title": "  Remain in Me ", "confidence": "high", "note": "matches"}
    read = title_vision.read_slide_title(tmp_path / "sermon.mp4", tmp_path,
                                         draft_title="Remain in Me",
                                         offsets=(3.0, 60.0, 120.0),
                                         runner=_runner_writes_frames(cmds), judge=judge)
    assert read == SlideRead("Remain in Me", "high")
    assert [c[c.index("-ss") + 1] for c in cmds] == ["3.0", "60.0", "120.0"]
    assert all("-frames:v" in c and c[c.index("-frames:v") + 1] == "1" for c in cmds)
    assert seen["draft_title"] == "Remain in Me" and len(seen["frames"]) == 3


def test_read_slide_title_skips_offsets_past_the_end(tmp_path):
    # ffmpeg produces no file when -ss is beyond the clip; that offset is dropped,
    # not fatal.
    cmds = []
    def judge(workdir):
        ctx = json.loads((Path(workdir) / "title_context.json").read_text())
        assert len(ctx["frames"]) == 1
        return {"slide_title": "", "confidence": "low", "note": "no slide"}
    read = title_vision.read_slide_title(tmp_path / "s.mp4", tmp_path, draft_title="D",
                                         offsets=(3.0, 600.0),
                                         runner=_runner_writes_frames(cmds, fail_at=(600.0,)),
                                         judge=judge)
    assert read == SlideRead("", "low")


def test_read_slide_title_treats_garbage_judge_output_as_low(tmp_path):
    cmds = []
    read = title_vision.read_slide_title(tmp_path / "s.mp4", tmp_path, draft_title="D",
                                         offsets=(3.0,), runner=_runner_writes_frames(cmds),
                                         judge=lambda wd: None)
    assert read == SlideRead("", "low")


def test_resolve_title_prefers_slide_only_on_high_confidence():
    assert resolve_title("Christ Is Your Life", SlideRead("Jesus", "low")) == "Christ Is Your Life"
    assert resolve_title("Christ Is Your Life", SlideRead("Jesus", "high")) == "Jesus"
    assert resolve_title("Christ Is Your Life", SlideRead("", "high")) == "Christ Is Your Life"


def test_resolve_title_falls_back_to_slide_when_no_draft():
    assert resolve_title("", SlideRead("The God Who Knows Every Name", "low")) == \
        "The God Who Knows Every Name"
    assert resolve_title("", SlideRead("", "low")) == ""
