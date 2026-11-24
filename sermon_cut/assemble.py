from __future__ import annotations
import json
import re
import subprocess
from pathlib import Path

# EBU R128 targets handed to loudnorm (its defaults, stated so the two passes
# cannot drift apart).
LOUDNORM_I, LOUDNORM_TP, LOUDNORM_LRA = -24.0, -2.0, 7.0
_LOUDNORM_KEYS = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")


def measure_loudness(path: Path, *, runner=subprocess.run) -> dict:
    """First pass of two-pass loudnorm: measure the clip's loudness stats.

    Audio-only (`-vn`) so it costs seconds, not an encode. loudnorm prints its
    JSON block on stderr at the end of the run.
    """
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn",
           "-af", f"loudnorm=I={LOUDNORM_I:g}:TP={LOUDNORM_TP:g}:LRA={LOUDNORM_LRA:g}:"
                  "print_format=json",
           "-f", "null", "-"]
    result = runner(cmd, capture_output=True, text=True, check=True)
    m = re.search(r"\{[^{}]*\}", result.stderr or "", re.S)
    if not m:
        raise RuntimeError(f"loudnorm printed no stats for {path}")
    raw = json.loads(m.group(0))
    return {k: float(raw[k]) for k in _LOUDNORM_KEYS}


def _loudnorm_filter(stats: dict) -> str:
    """Second pass: apply the measured stats with a constant (linear) gain.

    Without `measured_*` loudnorm runs in dynamic mode and ramps its gain up
    over the first ~60s -- on a -50 LUFS source the sermon opened ~30 dB quiet
    and climbed for a minute (2026-09-06). `linear=true` is honoured when the
    true peak stays under TP after the gain; otherwise loudnorm falls back to
    dynamic on its own.
    """
    return (f"loudnorm=I={LOUDNORM_I:g}:TP={LOUDNORM_TP:g}:LRA={LOUDNORM_LRA:g}:"
            f"measured_I={stats['input_i']:g}:measured_TP={stats['input_tp']:g}:"
            f"measured_LRA={stats['input_lra']:g}:measured_thresh={stats['input_thresh']:g}:"
            f"offset={stats['target_offset']:g}:linear=true")


def _default_probe(path: Path) -> float:
    """Return a media file's duration in seconds via ffprobe."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nokey=1:noprint_wrappers=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


def assemble(intro_path: Path, sermon_path: Path, workdir: Path, *,
             outro_path: Path | None = None, dissolve: float = 0.8,
             fade: float = 0.6, width: int = 1920, height: int = 1080,
             fps: int = 30, runner=subprocess.run, duration_probe=_default_probe,
             loudness_probe=None) -> Path:
    """Weave intro -> sermon [-> outro] into final.mp4 with cross-dissolves
    between clips and a fade up/down from/to black. Cross-dissolves overlap each
    join by ``dissolve`` seconds (eating into the sermon's throwaway padding), so
    audio and video stay in sync. ``outro_path=None`` produces a two-clip weave.
    """
    workdir = Path(workdir)
    out = workdir / "final.mp4"
    if out.exists():
        return out

    inputs = [intro_path, sermon_path]
    if outro_path is not None:
        inputs.append(outro_path)

    scale = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}"
    )
    parts = []
    for i in range(len(inputs)):
        parts.append(f"[{i}:v]{scale}[v{i}]")
        parts.append(f"[{i}:a]aresample=48000[a{i}]")

    # Chain cross-dissolves. offset for each join is the running length of the
    # material already placed, minus the dissolve (so the overlap lands on the
    # tail of what's there and the head of the next clip).
    durs = [duration_probe(p) for p in inputs]
    vcur, acur, running = "v0", "a0", durs[0]
    for i in range(1, len(inputs)):
        offset = running - dissolve
        vout, aout = f"vx{i}", f"ax{i}"
        parts.append(
            f"[{vcur}][v{i}]xfade=transition=fade:duration={dissolve:.3f}:"
            f"offset={offset:.3f}[{vout}]"
        )
        parts.append(f"[{acur}][a{i}]acrossfade=d={dissolve:.3f}[{aout}]")
        vcur, acur = vout, aout
        running = running + durs[i] - dissolve

    fade_out_start = running - fade
    parts.append(
        f"[{vcur}]fade=t=in:st=0:d={fade:.3f},"
        f"fade=t=out:st={fade_out_start:.3f}:d={fade:.3f}[v]"
    )
    # Two-pass loudnorm, measured on the sermon (the bumpers are near-silent
    # stills and would only drag the integrated reading down).
    stats = (loudness_probe or (lambda p: measure_loudness(p, runner=runner)))(sermon_path)
    parts.append(f"[{acur}]{_loudnorm_filter(stats)},aresample=48000[ao]")
    filtergraph = ";".join(parts)

    cmd = ["ffmpeg", "-y"]
    for p in inputs:
        cmd += ["-i", str(p)]
    cmd += [
        "-filter_complex", filtergraph,
        "-map", "[v]", "-map", "[ao]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        str(out),
    ]
    runner(cmd, check=True)
    return out
