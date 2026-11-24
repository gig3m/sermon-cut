import json, pytest
from sermon_cut.config import ShortsConfig
from sermon_cut.shorts_select import (
    ShortCandidate, load_candidates, save_candidates,
    candidate_band, validate_candidate, source_to_clip_time)

def _c(start=0.0, end=60.0):
    return ShortCandidate(slug="hook", title="Hook", source_start=start,
                          source_end=end, rationale="stands alone",
                          standalone_confidence="high")

def test_band_in_and_warn_and_reject():
    cfg = ShortsConfig()
    assert candidate_band(_c(0, 60), cfg) == "IN_BAND"
    assert candidate_band(_c(0, 100), cfg) == "WARN_LONG"
    assert candidate_band(_c(0, 10), cfg) == "TOO_SHORT"
    assert candidate_band(_c(0, 200), cfg) == "TOO_LONG"

def test_validate_raises_out_of_band():
    cfg = ShortsConfig()
    with pytest.raises(ValueError):
        validate_candidate(_c(0, 10), cfg)
    validate_candidate(_c(0, 60), cfg)  # no raise

def test_source_to_clip_time_maps_into_sermon_mp4():
    # sermon.mp4 begins at plan.start - front_padding
    assert source_to_clip_time(3865.0, 3850.0, 1.0) == 3865.0 - (3850.0 - 1.0)

def test_candidates_round_trip(tmp_path):
    p = tmp_path / "candidates.json"
    save_candidates([_c()], p)
    got = load_candidates(p)
    assert got[0].slug == "hook" and got[0].chosen is False
