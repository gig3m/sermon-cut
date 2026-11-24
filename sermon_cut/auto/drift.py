from __future__ import annotations

import re


def normalize(text: str) -> list[str]:
    return re.sub(r"[^a-z0-9 ]", " ", text.lower()).split()


def find_word_time(words: list[dict], phrase: str, near: float, edge: str) -> float | None:
    """Find the contiguous run of `words` whose normalized tokens equal the
    normalized `phrase`, choosing the occurrence whose first word starts nearest
    `near`. Return the first word's start (edge='start') or the last word's end
    (edge='end'); None if no occurrence matches."""
    # Build a flat token list and track which word each token belongs to
    tokens = []
    word_indices = []
    for i, w in enumerate(words):
        normalized = normalize(w["word"])
        tokens.extend(normalized)
        word_indices.extend([i] * len(normalized))

    target = normalize(phrase)
    n = len(target)
    if n == 0:
        return None
    best_i, best_dist = None, None
    for i in range(0, len(tokens) - n + 1):
        if tokens[i:i + n] == target:
            first_word_idx = word_indices[i]
            dist = abs(words[first_word_idx]["start"] - near)
            if best_dist is None or dist < best_dist:
                best_i, best_dist = i, dist
    if best_i is None:
        return None
    first_word_idx = word_indices[best_i]
    last_word_idx = word_indices[best_i + n - 1]
    return words[first_word_idx]["start"] if edge == "start" else words[last_word_idx]["end"]


def pin_boundaries(start_words: list[dict], end_words: list[dict], *,
                   in_phrase: str, out_phrase: str,
                   rough_start: float, rough_end: float) -> tuple[float, float]:
    start = find_word_time(start_words, in_phrase, rough_start, "start")
    end = find_word_time(end_words, out_phrase, rough_end, "end")
    return (start if start is not None else rough_start,
            end if end is not None else rough_end)


def pin_chapters(segments: list[dict], topics: list[dict], phys_start: float) -> list[dict]:
    """Map each topic phrase to the earliest segment whose normalized text contains
    it; chapter time = round(phys_start + segment.start). Ordered by time, unmatched
    topics dropped, duplicate times removed (first label wins)."""
    out = []
    for topic in topics:
        want = normalize(topic["phrase"])
        for seg in segments:
            toks = normalize(seg["text"])
            if _contains_subseq(toks, want):
                out.append({"time": round(phys_start + seg["start"]), "label": topic["label"]})
                break
    out.sort(key=lambda c: c["time"])
    deduped, seen = [], set()
    for c in out:
        if c["time"] in seen:
            continue
        seen.add(c["time"]); deduped.append(c)
    return deduped


def _contains_subseq(tokens: list[str], want: list[str]) -> bool:
    if not want:
        return False
    for i in range(0, len(tokens) - len(want) + 1):
        if tokens[i:i + len(want)] == want:
            return True
    return False
