import subprocess
from pathlib import Path
from sermon_cut.assemble import assemble


def _setup(tmp_path, with_outro):
    intro = tmp_path / "intro.mp4"; intro.write_bytes(b"i")
    sermon = tmp_path / "sermon.mp4"; sermon.write_bytes(b"s")
    outro = tmp_path / "outro.mp4"
    if with_outro:
        outro.write_bytes(b"o")
    durs = {str(intro): 5.0, str(sermon): 100.0, str(outro): 5.0}
    captured = {}

    def runner(cmd, **kwargs):
        if "print_format=json" in " ".join(cmd):      # loudnorm measurement pass
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr=(
                '{ "input_i" : "-20.0", "input_tp" : "-3.0", "input_lra" : "5.0", '
                '"input_thresh" : "-30.0", "target_offset" : "0.0" }'))
        captured["cmd"] = cmd
        (tmp_path / "final.mp4").write_bytes(b"final")
        return subprocess.CompletedProcess(cmd, 0)

    def probe(path):
        return durs[str(path)]

    return intro, sermon, outro, captured, runner, probe


def test_assemble_three_clip_weave_with_transitions(tmp_path: Path):
    intro, sermon, outro, captured, runner, probe = _setup(tmp_path, True)
    out = assemble(intro, sermon, tmp_path, outro_path=outro, dissolve=0.8,
                   fade=0.6, runner=runner, duration_probe=probe)
    assert out == tmp_path / "final.mp4"
    j = " ".join(captured["cmd"])
    # two cross-dissolves at computed offsets (intro=5, sermon=100, D=0.8)
    assert "xfade=transition=fade:duration=0.800:offset=4.200" in j
    assert "offset=103.400" in j
    assert j.count("xfade") == 2
    assert j.count("acrossfade=d=0.800") == 2
    # fade up from black, fade out at total-F  (total = 5+100+5-1.6 = 108.4)
    assert "fade=t=in:st=0:d=0.600" in j
    assert "fade=t=out:st=107.800:d=0.600" in j
    assert "loudnorm" in j
    assert str(outro) in j
    assert "concat=" not in j
    assert "[v]" in j and "[ao]" in j


def test_assemble_two_clip_weave_without_outro(tmp_path: Path):
    intro, sermon, outro, captured, runner, probe = _setup(tmp_path, False)
    out = assemble(intro, sermon, tmp_path, dissolve=0.8, fade=0.6,
                   runner=runner, duration_probe=probe)
    j = " ".join(captured["cmd"])
    assert j.count("xfade") == 1
    assert "offset=4.200" in j
    # total = 5 + 100 - 0.8 = 104.2; fade-out at 103.600
    assert "fade=t=out:st=103.600:d=0.600" in j
    assert "loudnorm" in j
    assert str(outro) not in j


def test_assemble_idempotent(tmp_path: Path):
    intro = tmp_path / "intro.mp4"; intro.write_bytes(b"i")
    sermon = tmp_path / "sermon.mp4"; sermon.write_bytes(b"s")
    (tmp_path / "final.mp4").write_bytes(b"exists")

    def explode(cmd, **kwargs):
        raise AssertionError("should not render when final.mp4 exists")

    out = assemble(intro, sermon, tmp_path, runner=explode)
    assert out == tmp_path / "final.mp4"


# --- two-pass loudnorm -------------------------------------------------------
# Single-pass `loudnorm` (no measured_* values) runs in "dynamic" mode: a
# real-time normaliser whose gain ramps up over the first ~60s. On the very
# quiet 2026-09-06 source (-50.75 LUFS) the sermon opened ~30 dB under level
# and climbed for a minute. Measuring first and applying with linear=true gives
# one constant gain from the first word.

def test_assemble_applies_measured_loudnorm_linearly(tmp_path: Path):
    intro, sermon, outro, captured, runner, probe = _setup(tmp_path, True)
    asked = []
    def loudness(path):
        asked.append(Path(path))
        return {"input_i": -50.75, "input_tp": -30.15, "input_lra": 6.30,
                "input_thresh": -61.14, "target_offset": -0.16}
    assemble(intro, sermon, tmp_path, outro_path=outro, runner=runner,
             duration_probe=probe, loudness_probe=loudness)
    assert asked == [sermon]            # measured on the sermon, the material that matters
    j = " ".join(captured["cmd"])
    assert ("loudnorm=I=-24:TP=-2:LRA=7:measured_I=-50.75:measured_TP=-30.15:"
            "measured_LRA=6.3:measured_thresh=-61.14:offset=-0.16:linear=true") in j
    assert j.count("loudnorm") == 1


def test_loudness_probe_parses_ffmpeg_json_from_stderr(tmp_path: Path):
    from sermon_cut.assemble import measure_loudness
    stderr = ('[Parsed_loudnorm_0 @ 0x1] \n{\n\t"input_i" : "-50.75",\n'
              '\t"input_tp" : "-30.15",\n\t"input_lra" : "6.30",\n'
              '\t"input_thresh" : "-61.14",\n\t"output_i" : "-23.84",\n'
              '\t"target_offset" : "-0.16"\n}\n')
    seen = {}
    def runner(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr=stderr)
    stats = measure_loudness(tmp_path / "sermon.mp4", runner=runner)
    assert stats == {"input_i": -50.75, "input_tp": -30.15, "input_lra": 6.30,
                     "input_thresh": -61.14, "target_offset": -0.16}
    cmd = seen["cmd"]
    assert cmd[0] == "ffmpeg" and "-vn" in cmd and "print_format=json" in " ".join(cmd)
    assert cmd[-2:] == ["-f", "null"] or cmd[-1] == "-"
