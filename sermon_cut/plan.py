from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Chapter:
    time: float
    label: str


@dataclass
class SermonPlan:
    start: float
    end: float
    title: str
    speaker: str
    date: str
    description: str
    chapters: list[Chapter] = field(default_factory=list)


def load_plan(path: Path) -> SermonPlan:
    data = json.loads(Path(path).read_text())
    chapters = [Chapter(time=float(c["time"]), label=c["label"])
                for c in data.get("chapters", [])]
    return SermonPlan(
        start=float(data["start"]),
        end=float(data["end"]),
        title=data["title"],
        speaker=data["speaker"],
        date=data["date"],
        description=data["description"],
        chapters=chapters,
    )


def validate_plan(plan: SermonPlan) -> None:
    if plan.end <= plan.start:
        raise ValueError(f"end ({plan.end}) must be greater than start ({plan.start})")
    for name in ("title", "speaker", "date"):
        if not str(getattr(plan, name)).strip():
            raise ValueError(f"{name} must not be empty")
    for ch in plan.chapters:
        if not (plan.start <= ch.time <= plan.end):
            raise ValueError(
                f"chapter '{ch.label}' time {ch.time} outside [{plan.start}, {plan.end}]"
            )


def segment_within_band(plan: SermonPlan, *, min_seconds: int = 600,
                        max_seconds: int = 2700) -> bool:
    length = plan.end - plan.start
    return min_seconds <= length <= max_seconds
