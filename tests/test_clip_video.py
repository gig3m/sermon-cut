import subprocess
from pathlib import Path
from sermon_cut.clip_video import clip_video


def test_clip_video_uses_asymmetric_padding(tmp_path: Path):
    captured = {}

    def runner(cmd, **kwargs):
        captured["cmd"] = cmd
        (tmp_path / "sermon.mp4").write_bytes(b"video")
        return subprocess.CompletedProcess(cmd, 0)

    out = clip_video("https://youtu.be/v", 3000.0, 4500.0, tmp_path,
                     front_padding=1, back_padding=4, runner=runner)
    assert out == tmp_path / "sermon.mp4"
    cmd = captured["cmd"]
    assert "yt-dlp" in cmd[0]
    joined = " ".join(cmd)
    # 3000-1 = 2999 -> 00:49:59 ; 4500+4 = 4504 -> 01:15:04
    assert "--download-sections" in cmd
    assert "*00:49:59-01:15:04" in joined


def test_clip_video_clamps_front_padding_at_zero(tmp_path: Path):
    captured = {}

    def runner(cmd, **kwargs):
        captured["cmd"] = cmd
        (tmp_path / "sermon.mp4").write_bytes(b"video")
        return subprocess.CompletedProcess(cmd, 0)

    clip_video("https://youtu.be/v", 3.0, 100.0, tmp_path,
               front_padding=8, back_padding=8, runner=runner)
    assert "*00:00:00-00:01:48" in " ".join(captured["cmd"])  # start clamps to 0


def test_clip_video_idempotent(tmp_path: Path):
    (tmp_path / "sermon.mp4").write_bytes(b"exists")

    def explode(cmd, **kwargs):
        raise AssertionError("should not download when sermon.mp4 exists")

    out = clip_video("https://youtu.be/v", 0.0, 10.0, tmp_path, runner=explode)
    assert out == tmp_path / "sermon.mp4"
