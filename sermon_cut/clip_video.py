from __future__ import annotations
import subprocess
from pathlib import Path
from .timefmt import seconds_to_timestamp, clamp_nonnegative


def clip_video(url: str, start: float, end: float, workdir: Path, *,
               front_padding: int = 1, back_padding: int = 4,
               runner=subprocess.run) -> Path:
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    out = workdir / "sermon.mp4"
    if out.exists():
        return out

    a = seconds_to_timestamp(clamp_nonnegative(start - front_padding))
    b = seconds_to_timestamp(end + back_padding)
    cmd = [
        "yt-dlp",
        "-f", "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
        "--download-sections", f"*{a}-{b}",
        "--force-keyframes-at-cuts",
        "--merge-output-format", "mp4",
        "-o", str(out),
        "--no-progress",
        url,
    ]
    runner(cmd, check=True)
    return out
