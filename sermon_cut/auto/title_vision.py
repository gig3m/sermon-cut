from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Where in the sermon clip to look for the on-screen title slide. The screen is
# sometimes still off during the greeting (2026-09-06: blank until ~205s), so a
# single early frame is not enough. Offsets past the end of the clip yield no
# frame and are simply dropped.
DEFAULT_OFFSETS = (3.0, 60.0, 120.0, 180.0, 240.0, 360.0, 480.0, 600.0)


@dataclass(frozen=True)
class SlideRead:
    title: str        # what the slide says (heading + scripture line), "" if none
    confidence: str   # "high" = the slide clearly names the same sermon as the draft


def _default_judge(workdir):
    """Headless Claude reads the frames + draft in `title_context.json` and
    writes `slide_title.json`. Returns its parsed content, or None."""
    wd = Path(workdir)
    subprocess.run(["claude", "-p", "/sermon-read-title"], cwd=str(wd), check=True)
    out = wd / "slide_title.json"
    if not out.exists():
        return None
    try:
        return json.loads(out.read_text())
    except ValueError:
        return None


def read_slide_title(sermon_path, workdir, *, draft_title: str = "",
                     offsets=DEFAULT_OFFSETS, runner=subprocess.run,
                     judge=None) -> SlideRead:
    workdir = Path(workdir)
    frames_dir = workdir / "title_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    frames: list[str] = []
    for at in offsets:
        frame = frames_dir / f"frame_{int(at):04d}.png"
        runner(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-ss", str(at), "-i", str(sermon_path),
                "-frames:v", "1", str(frame)], check=False)
        if frame.exists():
            frames.append(str(frame))
    (workdir / "title_context.json").write_text(json.dumps(
        {"draft_title": draft_title, "frames": frames}, indent=2))

    judge = judge or _default_judge
    verdict = judge(workdir)
    if not isinstance(verdict, dict):
        return SlideRead("", "low")
    title = str(verdict.get("slide_title") or "").strip()
    confidence = "high" if verdict.get("confidence") == "high" else "low"
    return SlideRead(title, confidence)


def resolve_title(draft_title: str, slide: SlideRead) -> str:
    """The slide wins only when the judge is confident it names the same sermon
    as the transcript draft; otherwise the draft. A bare slide read is the
    fallback when there is no draft at all."""
    draft_title = (draft_title or "").strip()
    if slide.confidence == "high" and slide.title:
        return slide.title
    return draft_title or slide.title
