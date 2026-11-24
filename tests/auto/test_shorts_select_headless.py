import json
from types import SimpleNamespace
from pathlib import Path

from sermon_cut import shorts_select


def _cfg():
    # candidate_band(c, cfg.shorts) reads hard_min/hard_max/target_max directly
    # off the shorts config; these mirror the real ShortsConfig defaults.
    return SimpleNamespace(shorts=SimpleNamespace(
        target_min=20, target_max=60, hard_min=15, hard_max=180))


def _moment(slug, s, e, chosen=True, integrity="ok"):
    return {"slug": slug, "title": slug.title(), "source_start": s, "source_end": e,
            "rationale": "r", "standalone_confidence": "high",
            "context_integrity": integrity, "chosen": chosen}


def test_select_keeps_only_chosen_in_band_ok_and_caps(tmp_path):
    moments = [
        _moment("a", 100.0, 145.0),                       # 45s ok
        _moment("b", 200.0, 260.0),                       # 60s ok
        _moment("c", 300.0, 302.0),                       # 2s -> TOO_SHORT, dropped
        _moment("d", 400.0, 450.0, integrity="risky"),    # excluded by integrity
        _moment("e", 500.0, 540.0, chosen=False),         # not chosen
        _moment("f", 600.0, 640.0),                       # 40s ok -> 4th, capped out
    ]
    judge = lambda tp, plan: moments
    out = shorts_select.select_short_moments(
        tmp_path / "transcript.json", {"start": 0}, tmp_path / "candidates.json",
        _cfg(), judge=judge, max_keep=3)
    saved = json.loads(Path(out).read_text())
    slugs = [c["slug"] for c in saved["candidates"]]
    assert slugs == ["a", "b", "f"]
    assert all(c["chosen"] for c in saved["candidates"])


def test_select_can_return_zero(tmp_path):
    judge = lambda tp, plan: []
    out = shorts_select.select_short_moments(
        tmp_path / "t.json", {"start": 0}, tmp_path / "candidates.json",
        _cfg(), judge=judge)
    assert json.loads(Path(out).read_text())["candidates"] == []
