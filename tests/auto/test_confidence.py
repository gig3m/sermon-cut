from sermon_cut.auto.confidence import SelfAssessment, evaluate


def _ok(note="clean open on greeting, closes on Amen"):
    return SelfAssessment(in_point_ok=True, out_point_ok=True, no_reading_bleed=True, note=note)


def test_confident_when_in_band_and_all_flags_ok():
    confident, note = evaluate(band="IN_BAND", assessment=_ok())
    assert confident is True
    assert note == "clean open on greeting, closes on Amen"


def test_held_when_out_of_band():
    confident, note = evaluate(band="OUT_OF_BAND", assessment=_ok())
    assert confident is False
    assert note.startswith("held:")
    assert "length OUT_OF_BAND" in note


def test_held_when_reading_bleed():
    a = SelfAssessment(in_point_ok=False, out_point_ok=True, no_reading_bleed=False, note="x")
    confident, note = evaluate(band="IN_BAND", assessment=a)
    assert confident is False
    assert "in-point uncertain" in note
    assert "scripture reading may bleed in" in note


def test_held_when_out_point_uncertain():
    a = SelfAssessment(in_point_ok=True, out_point_ok=False, no_reading_bleed=True, note="x")
    confident, note = evaluate(band="IN_BAND", assessment=a)
    assert confident is False
    assert "out-point uncertain" in note
