from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SelfAssessment:
    in_point_ok: bool        # opens on the preacher's greeting, not a Scripture reading
    out_point_ok: bool       # closes on the prayer / "Amen"
    no_reading_bleed: bool   # the pre-sermon reading does not bleed into the clip
    note: str                # Claude's one-line rationale


def evaluate(*, band: str, assessment: SelfAssessment) -> tuple[bool, str]:
    reasons: list[str] = []
    if band != "IN_BAND":
        reasons.append(f"length {band}")
    if not assessment.in_point_ok:
        reasons.append("in-point uncertain")
    if not assessment.no_reading_bleed:
        reasons.append("scripture reading may bleed in")
    if not assessment.out_point_ok:
        reasons.append("out-point uncertain")
    if reasons:
        return False, "held: " + "; ".join(reasons)
    return True, assessment.note
