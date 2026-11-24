from __future__ import annotations
import json
from dataclasses import dataclass, asdict
from pathlib import Path

@dataclass
class ShortCandidate:
    slug: str
    title: str
    source_start: float
    source_end: float
    rationale: str
    standalone_confidence: str
    context_integrity: str = "ok"
    chosen: bool = False

def _dur(c): return c.source_end - c.source_start

def candidate_band(c, cfg) -> str:
    d = _dur(c)
    if d < cfg.hard_min: return "TOO_SHORT"
    if d > cfg.hard_max: return "TOO_LONG"
    if d > cfg.target_max: return "WARN_LONG"
    return "IN_BAND"

def validate_candidate(c, cfg) -> None:
    band = candidate_band(c, cfg)
    if band in ("TOO_SHORT", "TOO_LONG"):
        raise ValueError(f"candidate '{c.slug}' {band}: {_dur(c):.1f}s")

def source_to_clip_time(source_t, plan_start, front_padding) -> float:
    return source_t - (plan_start - front_padding)

def load_candidates(path) -> list[ShortCandidate]:
    data = json.loads(Path(path).read_text())
    return [ShortCandidate(**c) for c in data["candidates"]]

def save_candidates(cands, path) -> None:
    Path(path).write_text(json.dumps({"candidates": [asdict(c) for c in cands]},
                                     indent=2))


import subprocess


def _default_judge(transcript_path, plan):
    """Headless Shorts selection: run `claude -p` in the transcript's dir with the
    sermon-shorts selection skill; it writes candidates.json which we read back.
    Replaced by a fake in tests."""
    wd = Path(transcript_path).parent
    subprocess.run(["claude", "-p", "/sermon-shorts-select"], cwd=str(wd), check=True)
    cand = wd / "shorts" / "candidates.json"
    return json.loads(cand.read_text()).get("candidates", []) if cand.exists() else []


def select_short_moments(transcript_path, plan, out_path, cfg, *,
                         judge=_default_judge, max_keep: int = 3):
    moments = judge(transcript_path, plan) or []
    kept = []
    for m in moments:
        if not m.get("chosen"):
            continue
        if m.get("context_integrity") != "ok":
            continue
        c = ShortCandidate(
            slug=m["slug"], title=m["title"],
            source_start=float(m["source_start"]), source_end=float(m["source_end"]),
            rationale=m.get("rationale", ""),
            standalone_confidence=m.get("standalone_confidence", ""),
            context_integrity=m.get("context_integrity", "ok"),
            chosen=True)
        if candidate_band(c, cfg.shorts) in ("TOO_SHORT", "TOO_LONG"):
            continue
        kept.append(c)
        if len(kept) >= max_keep:
            break
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    save_candidates(kept, out)
    return out
