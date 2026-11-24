from __future__ import annotations
import subprocess
from pathlib import Path
from .timefmt import seconds_to_timestamp, clamp_nonnegative
from .assemble import _default_probe

def window_bounds(clip_start, clip_end, *, pad=5.0):
    return (clamp_nonnegative(clip_start - pad), clip_end + pad)

def extract_window(sermon_path, win_start, win_end, workdir, *,
                   out_name="window.mp4", runner=subprocess.run):
    workdir = Path(workdir); workdir.mkdir(parents=True, exist_ok=True)
    out = workdir / out_name
    if out.exists():
        return out
    runner(["ffmpeg", "-y", "-v", "error",
            "-ss", seconds_to_timestamp(win_start),
            "-to", seconds_to_timestamp(win_end),
            "-i", str(sermon_path), "-c", "copy", str(out)], check=True)
    return out


def build_short_filter(body_dur, cfg):
    """Build the normalized xfade filtergraph weaving the short's body into
    its branded outro card, with fade up/down. Returns (filtergraph, total_dur).
    """
    xf = body_dur - cfg.dissolve
    total = body_dur + cfg.outro_hold
    fo = total - cfg.fade_out
    norm = "fps=30,format=yuv420p,setsar=1,settb=AVTB"
    fc = (f"[0:v]scale=1080:1920,{norm},fade=t=in:st=0:d={cfg.fade_up}[bd];"
          f"[1:v]scale=1080:1920,{norm}[o];"
          f"[bd][o]xfade=transition=fade:duration={cfg.dissolve}:offset={xf:.3f}[xv];"
          f"[xv]fade=t=out:st={fo:.3f}:d={cfg.fade_out}[v];"
          f"[0:a]afade=t=out:st={xf:.3f}:d={cfg.dissolve},apad[a]")
    return fc, total


def assemble_short(body_path, outro_png, workdir, cfg, *,
                    out_name="short.mp4", runner=subprocess.run,
                    duration_probe=_default_probe) -> Path:
    """Weave the short's body clip into the branded outro card, producing
    short.mp4 (cold-open + outro hold + fades). Idempotent.
    """
    workdir = Path(workdir); workdir.mkdir(parents=True, exist_ok=True)
    out = workdir / out_name
    if out.exists():
        return out

    body_dur = duration_probe(body_path)
    filtergraph, total = build_short_filter(body_dur, cfg)
    outro_dur = cfg.outro_hold + cfg.dissolve

    cmd = ["ffmpeg", "-y", "-v", "error",
           "-i", str(body_path),
           "-loop", "1", "-t", str(outro_dur), "-i", str(outro_png),
           "-filter_complex", filtergraph,
           "-map", "[v]", "-map", "[a]",
           "-t", str(total),
           "-c:v", "libx264", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "192k",
           str(out)]
    runner(cmd, check=True)
    return out


def build_vertical_outro_card(src_card_path, workdir, *, out_name="outro_9x16.png",
                              width=1080, height=1920) -> Path:
    from PIL import Image
    import shutil
    workdir = Path(workdir); out = workdir / out_name
    if out.exists():
        return out

    override = workdir.parent / "outro_9x16_override.png"
    if override.exists():
        workdir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(override, out)
        return out

    workdir.mkdir(parents=True, exist_ok=True)
    src = Image.open(src_card_path).convert("RGB")
    bg = src.getpixel((5, 5))
    canvas = Image.new("RGB", (width, height), bg)
    scaled = src.resize((width, int(width * src.height / src.width)))
    canvas.paste(scaled, (0, (height - scaled.height) // 2))
    canvas.save(out)
    return out
