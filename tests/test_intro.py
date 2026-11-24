import subprocess
from pathlib import Path
from sermon_cut.intro import make_bumper


def test_make_bumper_builds_ffmpeg_command(tmp_path: Path):
    img = tmp_path / "card.png"
    img.write_bytes(b"png")
    captured = {}

    def runner(cmd, **kwargs):
        captured["cmd"] = cmd
        (tmp_path / "intro.mp4").write_bytes(b"intro")
        return subprocess.CompletedProcess(cmd, 0)

    out = make_bumper(img, tmp_path, seconds=5, width=1920, height=1080, fps=30,
                      runner=runner)
    assert out == tmp_path / "intro.mp4"
    cmd = captured["cmd"]
    joined = " ".join(cmd)
    assert cmd[0] == "ffmpeg"
    assert str(img) in joined
    assert "anullsrc" in joined            # silent audio track
    assert "-t" in cmd and "5" in cmd      # duration
    assert "1920" in joined and "1080" in joined
    assert "-r" in cmd and "30" in cmd     # fps


def test_make_bumper_out_name(tmp_path: Path):
    img = tmp_path / "card.png"
    img.write_bytes(b"png")

    def runner(cmd, **kwargs):
        (tmp_path / "outro.mp4").write_bytes(b"outro")
        return subprocess.CompletedProcess(cmd, 0)

    out = make_bumper(img, tmp_path, seconds=5, out_name="outro.mp4", runner=runner)
    assert out == tmp_path / "outro.mp4"


def test_make_bumper_idempotent(tmp_path: Path):
    img = tmp_path / "card.png"
    img.write_bytes(b"png")
    (tmp_path / "intro.mp4").write_bytes(b"exists")

    def explode(cmd, **kwargs):
        raise AssertionError("should not render when the output exists")

    out = make_bumper(img, tmp_path, runner=explode)
    assert out == tmp_path / "intro.mp4"
