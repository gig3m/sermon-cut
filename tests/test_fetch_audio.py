import json
import subprocess
from pathlib import Path
from sermon_cut.fetch_audio import fetch_audio, resolve_latest, AudioResult


class FakeRunner:
    def __init__(self, info: dict, workdir: Path):
        self.info = info
        self.workdir = workdir
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        # simulate yt-dlp writing the audio file and info json
        (self.workdir / "audio.m4a").write_bytes(b"fake-audio")
        (self.workdir / "source.info.json").write_text(json.dumps(self.info))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


def test_fetch_audio_downloads_and_parses(tmp_path: Path):
    info = {"id": "vid123", "title": "Sunday Service", "duration": 7200}
    runner = FakeRunner(info, tmp_path)
    result = fetch_audio("https://youtu.be/vid123", tmp_path, runner=runner)
    assert isinstance(result, AudioResult)
    assert result.video_id == "vid123"
    assert result.title == "Sunday Service"
    assert result.duration == 7200
    assert result.audio_path == tmp_path / "audio.m4a"
    assert runner.calls[0][0] == "yt-dlp"
    joined = " ".join(runner.calls[0])
    # audio-only: must request bestaudio, not full video
    assert "-f" in runner.calls[0] and "bestaudio" in joined
    # info json must be written to source.info.json (per-type output template)
    assert "infojson:" in joined and "source" in joined


def test_fetch_audio_idempotent_skips_download(tmp_path: Path):
    (tmp_path / "audio.m4a").write_bytes(b"already")
    (tmp_path / "source.info.json").write_text(
        json.dumps({"id": "v", "title": "t", "duration": 1})
    )

    def explode(cmd, **kwargs):
        raise AssertionError("runner should not be called when artifacts exist")

    result = fetch_audio("https://youtu.be/v", tmp_path, runner=explode)
    assert result.video_id == "v"


def test_resolve_latest_returns_watch_url(tmp_path: Path):
    def runner(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="abc987\n", stderr="")

    url = resolve_latest("UC123", runner=runner)
    assert url == "https://www.youtube.com/watch?v=abc987"
