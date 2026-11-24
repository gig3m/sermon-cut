from sermon_cut.auto import drift


def _w(word, start, end): return {"word": word, "start": start, "end": end}


def _words(pairs, t0=0.0, step=0.4):
    out, t = [], t0
    for p in pairs:
        out.append(_w(p, round(t, 2), round(t + step, 2))); t += step
    return out


def test_normalize_strips_punctuation_and_lowercases():
    assert drift.normalize("Good Morning, church!") == ["good", "morning", "church"]


def test_find_word_time_start_edge_returns_first_word_start():
    words = _words(["please", "be", "seated", "good", "morning", "everyone"], t0=100.0)
    t = drift.find_word_time(words, "good morning", near=104.0, edge="start")
    assert t == words[3]["start"]  # "good"


def test_find_word_time_end_edge_returns_last_word_end():
    words = _words(["in", "christ's", "name", "amen", "well", "thank", "you"], t0=200.0)
    t = drift.find_word_time(words, "in christ's name amen", near=200.0, edge="end")
    assert t == words[3]["end"]  # "amen"


def test_find_word_time_picks_occurrence_nearest_near():
    words = (_words(["good", "morning"], t0=10.0)
             + _words(["filler"] * 5, t0=20.0)
             + _words(["good", "morning"], t0=50.0))
    assert drift.find_word_time(words, "good morning", near=49.0, edge="start") == 50.0
    assert drift.find_word_time(words, "good morning", near=9.0, edge="start") == 10.0


def test_find_word_time_returns_none_when_absent():
    words = _words(["a", "b", "c"], t0=0.0)
    assert drift.find_word_time(words, "not here", near=0.0, edge="start") is None


def test_pin_boundaries_uses_found_times():
    sw = _words(["reading", "ends", "good", "morning", "open"], t0=100.0)
    ew = _words(["name", "amen", "well", "thank"], t0=300.0)
    start, end = drift.pin_boundaries(
        sw, ew, in_phrase="good morning", out_phrase="amen",
        rough_start=101.0, rough_end=300.5)
    assert start == sw[2]["start"]
    assert end == ew[1]["end"]


def test_pin_boundaries_falls_back_to_rough_when_absent():
    sw = _words(["a", "b"], t0=0.0)
    start, end = drift.pin_boundaries(
        sw, sw, in_phrase="zzz", out_phrase="qqq",
        rough_start=5.0, rough_end=9.0)
    assert (start, end) == (5.0, 9.0)


def test_pin_chapters_maps_phrases_to_absolute_seconds():
    segments = [
        {"text": "welcome this morning to our study", "start": 10.0},
        {"text": "the first point is about grace", "start": 70.0},
        {"text": "and now let us pray together", "start": 300.0},
    ]
    topics = [
        {"phrase": "first point", "label": "First point"},
        {"phrase": "let us pray", "label": "Closing prayer"},
    ]
    out = drift.pin_chapters(segments, topics, phys_start=1000.0)
    assert out == [
        {"time": 1070, "label": "First point"},
        {"time": 1300, "label": "Closing prayer"},
    ]


def test_pin_chapters_skips_unmatched_topics():
    segments = [{"text": "only this text", "start": 5.0}]
    out = drift.pin_chapters(segments, [{"phrase": "missing", "label": "X"}], phys_start=0.0)
    assert out == []
