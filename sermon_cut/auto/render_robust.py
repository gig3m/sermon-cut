from __future__ import annotations

import subprocess
from pathlib import Path

_FMT = "bestvideo[height<=1080][ext=mp4]/bestvideo[height<=1080]"


def full_download(url: str, out_path, *, fmt: str = _FMT, runner=subprocess.run) -> Path:
    out = Path(out_path)
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    runner(["yt-dlp", "-f", fmt, "-o", str(out), "--no-progress", url], check=True)
    return out


def mux_av(video_path, audio_path, out_path, *, runner=subprocess.run) -> Path:
    out = Path(out_path)
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    runner(["ffmpeg", "-y", "-i", str(video_path), "-i", str(audio_path),
            "-map", "0:v:0", "-map", "1:a:0", "-c", "copy", "-shortest", str(out)],
           check=True)
    return out


def cut_window(av_path, start: float, dur: float, out_path, *,
               fps: int = 30, crf: int = 18, runner=subprocess.run) -> Path:
    out = Path(out_path)
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    runner(["ffmpeg", "-y", "-ss", str(start), "-i", str(av_path), "-t", str(dur),
            "-vf", f"fps={fps}", "-c:v", "libx264", "-crf", str(crf),
            "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)],
           check=True)
    return out


def extract_probe(src, start: float, dur: float, out_wav, *, runner=subprocess.run) -> Path:
    out = Path(out_wav)
    out.parent.mkdir(parents=True, exist_ok=True)
    runner(["ffmpeg", "-y", "-ss", str(start), "-i", str(src), "-t", str(dur),
            "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(out)],
           check=True)
    return out


import json
from dataclasses import dataclass

from . import drift


@dataclass
class RenderResult:
    sermon_path: Path
    phys_start: float
    sermon_start: float
    sermon_end: float


def _load_words(path, base: float) -> list[dict]:
    raw = json.loads(Path(path).read_text())
    return [{"word": w["word"], "start": w["start"] + base, "end": w["end"] + base}
            for w in raw]


def render_sermon(url, audio_path, workdir, *, in_phrase, out_phrase,
                  rough_start, rough_end, front_lead: float = 1.0,
                  probe_pad: float = 25.0, probe_len: float = 110.0,
                  runner=subprocess.run, word_transcriber=None) -> RenderResult:
    workdir = Path(workdir)
    sermon = workdir / "sermon.mp4"
    if word_transcriber is None:
        from ..transcribe import transcribe_words as word_transcriber

    if not sermon.exists():
        full = full_download(url, workdir / "full.mp4", runner=runner)
        av = mux_av(full, audio_path, workdir / "full_av.mp4", runner=runner)
        # probe windows (clamped at 0) around the rough boundaries
        sbase = max(0.0, rough_start - probe_pad)
        ebase = max(0.0, rough_end - probe_pad)
        extract_probe(audio_path, sbase, probe_len, workdir / "sp.wav", runner=runner)
        extract_probe(audio_path, ebase, probe_len, workdir / "ep.wav", runner=runner)
        sw = _load_words(word_transcriber(workdir / "sp.wav", workdir, out_name="sp_words.json"), sbase)
        ew = _load_words(word_transcriber(workdir / "ep.wav", workdir, out_name="ep_words.json"), ebase)
        start, end = drift.pin_boundaries(sw, ew, in_phrase=in_phrase, out_phrase=out_phrase,
                                          rough_start=rough_start, rough_end=rough_end)
        phys_start = max(0.0, start - front_lead)
        cut_window(av, phys_start, end - phys_start, sermon, runner=runner)
        (workdir / "_render.json").write_text(json.dumps(
            {"phys_start": phys_start, "sermon_start": start, "sermon_end": end}))

    meta = json.loads((workdir / "_render.json").read_text())
    return RenderResult(sermon, meta["phys_start"], meta["sermon_start"], meta["sermon_end"])


def _segments_from(result) -> list[dict]:
    """Accept either a real TranscriptResult (.segments) or a back-compat fake
    that returns a path to a JSON file (either a bare list of segments or a
    dict with a "segments" key)."""
    if hasattr(result, "segments"):
        return result.segments
    data = json.loads(Path(result).read_text())
    return data["segments"] if isinstance(data, dict) else data


def repin_chapters(sermon_path, topics, phys_start, workdir, *, segment_transcriber=None) -> list[dict]:
    workdir = Path(workdir)
    if segment_transcriber is None:
        from ..transcribe import transcribe as segment_transcriber
    # Re-transcribe into a dedicated subdir so the real `transcribe`'s fixed
    # `transcript.json` output can't clobber the run's main transcript.
    chap_dir = workdir / "_chap"
    chap_dir.mkdir(parents=True, exist_ok=True)
    res = segment_transcriber(sermon_path, chap_dir)
    segments = _segments_from(res)
    return drift.pin_chapters(segments, topics, phys_start)
