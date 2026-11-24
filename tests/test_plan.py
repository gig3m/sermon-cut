import json
from pathlib import Path
import pytest
from sermon_cut.plan import load_plan, validate_plan, segment_within_band, SermonPlan


def _good_plan_dict():
    return {
        "start": 3000.0,
        "end": 4500.0,
        "title": "Walking by Faith",
        "speaker": "Pastor Dan",
        "date": "2026-06-28",
        "description": "A sermon on Hebrews 11.",
        "chapters": [
            {"time": 3000.0, "label": "Introduction"},
            {"time": 3600.0, "label": "Main point"},
        ],
    }


def test_load_and_validate_good_plan(tmp_path: Path):
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(_good_plan_dict()))
    plan = load_plan(p)
    assert isinstance(plan, SermonPlan)
    assert plan.title == "Walking by Faith"
    assert plan.chapters[1].label == "Main point"
    validate_plan(plan)  # should not raise


def test_validate_rejects_end_before_start(tmp_path: Path):
    d = _good_plan_dict()
    d["end"] = d["start"] - 1
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(d))
    with pytest.raises(ValueError):
        validate_plan(load_plan(p))


def test_validate_rejects_chapter_out_of_range(tmp_path: Path):
    d = _good_plan_dict()
    d["chapters"].append({"time": 99999.0, "label": "Bad"})
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(d))
    with pytest.raises(ValueError):
        validate_plan(load_plan(p))


def test_validate_rejects_empty_speaker(tmp_path: Path):
    d = _good_plan_dict()
    d["speaker"] = ""
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(d))
    with pytest.raises(ValueError):
        validate_plan(load_plan(p))


def test_segment_within_band():
    d = _good_plan_dict()  # 1500s = 25 min
    plan = SermonPlan(**{**d, "chapters": []})
    assert segment_within_band(plan) is True
    short = SermonPlan(**{**d, "start": 0.0, "end": 300.0, "chapters": []})  # 5 min
    assert segment_within_band(short) is False
    long = SermonPlan(**{**d, "start": 0.0, "end": 3600.0, "chapters": []})  # 60 min
    assert segment_within_band(long) is False
