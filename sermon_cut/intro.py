from __future__ import annotations
import subprocess
from pathlib import Path


def make_bumper(image_path: Path, workdir: Path, *, seconds: int = 5,
                out_name: str = "intro.mp4",
                width: int = 1920, height: int = 1080, fps: int = 30,
                runner=subprocess.run) -> Path:
    """Loop a still image into a short silent clip (intro or outro bumper)."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    out = workdir / out_name
    if out.exists():
        return out

    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1"
    )
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", str(image_path),
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-t", str(seconds),
        "-vf", vf,
        "-r", str(fps),
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest",
        str(out),
    ]
    runner(cmd, check=True)
    return out
