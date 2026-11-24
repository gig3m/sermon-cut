from __future__ import annotations
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class AudioResult:
    audio_path: Path
    info_path: Path
    video_id: str
    title: str
    duration: float


def fetch_audio(url: str, workdir: Path, *, runner=subprocess.run) -> AudioResult:
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    audio_path = workdir / "audio.m4a"
    info_path = workdir / "source.info.json"

    if not (audio_path.exists() and info_path.exists()):
        cmd = [
            "yt-dlp",
            "-f", "bestaudio[ext=m4a]/bestaudio",
            "--extract-audio", "--audio-format", "m4a",
            "--write-info-json",
            "-o", f"infojson:{workdir / 'source'}",
            "-o", str(workdir / "audio.%(ext)s"),
            "--no-progress",
            url,
        ]
        runner(cmd, check=True)

    info = json.loads(info_path.read_text())
    return AudioResult(
        audio_path=audio_path,
        info_path=info_path,
        video_id=info["id"],
        title=info["title"],
        duration=float(info["duration"]),
    )


def resolve_latest(channel_id: str, *, runner=subprocess.run) -> str:
    cmd = [
        "yt-dlp",
        f"https://www.youtube.com/channel/{channel_id}/videos",
        "--flat-playlist",
        "--playlist-items", "1",
        "--print", "id",
        "--no-warnings",
    ]
    proc = runner(cmd, check=True, capture_output=True, text=True)
    video_id = proc.stdout.strip().splitlines()[0]
    return f"https://www.youtube.com/watch?v={video_id}"
