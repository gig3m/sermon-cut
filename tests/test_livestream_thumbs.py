from sermon_cut.livestream_thumbs import (
    ordinal, parse_service_date, livestream_label, is_sunday_service,
)


def test_ordinal():
    assert [ordinal(n) for n in (1, 2, 3, 4, 11, 21, 22)] == \
        ["1st", "2nd", "3rd", "4th", "11th", "21st", "22nd"]


def test_parse_service_date_variants():
    assert parse_service_date("Sunday Morning Class & Worship - July 5, 2026") == (2026, 7, 5)
    assert parse_service_date("Sunday Morning Class & Worship -April 20, 2025") == (2025, 4, 20)
    assert parse_service_date("Sunday Bible Class and Worship - Jan 28, 2024") == (2024, 1, 28)
    assert parse_service_date("... - February 14th, 2024") == (2024, 2, 14)
    assert parse_service_date("... - Match 31, 2024") == (2024, 3, 31)
    assert parse_service_date("no date") is None


def test_livestream_label():
    assert livestream_label("Sunday Morning Class & Worship - July 5, 2026") == "July 5th, 2026 Livestream"
    assert livestream_label("nope") is None


def test_is_sunday_service():
    assert is_sunday_service("Sunday Morning Class & Worship - July 5, 2026")
    assert is_sunday_service("Sunday Bible Class and Worship - May 18, 2025")
    assert not is_sunday_service("Wednesday Bible Class - June 4, 2025")
    assert not is_sunday_service("Friday Marriage Class - April 25, 2025")
    assert not is_sunday_service("Wednesday Class - May 7, 2025")


def test_card_distance_tells_our_card_from_a_reencode_and_from_other_dark_cards(tmp_path):
    # The scan used to ask "is the thumbnail dark + grayscale?" -- which the
    # generic 'Livestream' placeholder set at stream time also is, so a stream
    # wearing the placeholder was skipped as already carded (2026-09-06). Now we
    # ask "is it the card WE would render?" by image distance.
    # Measured on real images (64x36 gray, mean abs diff): same card after
    # YouTube's JPEG re-encode 0.08; a card with a different date 2.8; the
    # placeholder 8.3; an auto video frame 74.7.
    import io
    from PIL import Image, ImageDraw
    from sermon_cut.livestream_thumbs import card_distance, is_our_card

    def card(text):
        im = Image.new("RGB", (640, 360), (28, 28, 28))
        d = ImageDraw.Draw(im)
        d.rectangle((250, 40, 390, 100), outline=(200, 170, 110), width=6)   # "logo"
        d.rectangle((60, 220, 60 + 9 * len(text), 280), fill=(245, 245, 240))  # "text"
        return im

    ours = tmp_path / "ours.png"; card("September 6th, 2026 Livestream").save(ours)
    same_jpeg = io.BytesIO(); card("September 6th, 2026 Livestream").save(same_jpeg, "JPEG", quality=75)
    placeholder = io.BytesIO(); card("Livestream").save(placeholder, "PNG")
    other_date = io.BytesIO(); card("August 23rd, 2026 Livestream").save(other_date, "PNG")

    assert card_distance(same_jpeg.getvalue(), ours) < 1.0
    assert card_distance(placeholder.getvalue(), ours) > 1.0
    assert card_distance(other_date.getvalue(), ours) > 1.0
    assert is_our_card(same_jpeg.getvalue(), ours)
    assert not is_our_card(placeholder.getvalue(), ours)


def test_find_candidates_keeps_streams_not_wearing_our_card():
    from sermon_cut.livestream_thumbs import find_candidates
    streams = [
        ("v_placeholder", "Sunday Morning Class & Worship - July 5, 2026"),
        ("v_ours",        "Sunday Morning Class & Worship - June 28, 2026"),
        ("v_nothumb",     "Sunday Morning Class & Worship - June 21, 2026"),
        ("v_norender",    "Sunday Morning Class & Worship - June 14, 2026"),
        ("v_wed",         "Wednesday Bible Class - June 4, 2025"),
        ("v_nodate",      "Sunday Morning Class & Worship"),
    ]
    asked = []
    def card_for(video_id, label):
        asked.append((video_id, label))
        return None if video_id == "v_norender" else f"/cards/{video_id}.png"
    def fetch(vid):
        return None if vid == "v_nothumb" else f"THUMB-{vid}".encode()
    def is_card(thumb, png):
        return thumb == b"THUMB-v_ours" and png == "/cards/v_ours.png"

    cands = find_candidates(enumerate_streams=lambda: iter(streams), fetch_thumbnail=fetch,
                            card_for=card_for, is_card=is_card)
    assert [c.video_id for c in cands] == ["v_placeholder", "v_nothumb", "v_norender"]
    assert cands[0].label == "July 5th, 2026 Livestream"
    # every Sunday stream with a date gets its expected card rendered for comparison
    assert [a[0] for a in asked] == ["v_placeholder", "v_ours", "v_nothumb", "v_norender"]


def test_run_renders_and_sets(tmp_path):
    from pathlib import Path
    from sermon_cut.livestream_thumbs import run
    streams = [("v1", "Sunday Morning Class & Worship - July 5, 2026"),
               ("v2", "Sunday Morning Class & Worship - June 28, 2026"),
               ("v3", "Sunday Morning Class & Worship - June 21, 2026")]
    rendered, sets = [], []

    def render(label, out_path):
        Path(out_path).write_text("png")
        rendered.append((label, str(out_path)))
        return Path(out_path)

    def setter(vid, png):
        sets.append((vid, str(png)))

    # v3 already wears our card -> compared and left alone; v1/v2 get set
    res = run(enumerate_streams=lambda: iter(streams),
              fetch_thumbnail=lambda v: f"THUMB-{v}".encode(),
              render=render, set_thumbnail=setter, out_dir=tmp_path,
              is_card=lambda thumb, png: thumb == b"THUMB-v3")
    assert [r.video_id for r in res] == ["v1", "v2"]
    assert all(r.rendered and r.set_ok for r in res)
    assert len(rendered) == 3            # v3's card was rendered to compare against
    assert [s[0] for s in sets] == ["v1", "v2"]


def test_run_dry_run_skips_set(tmp_path):
    from pathlib import Path
    from sermon_cut.livestream_thumbs import run
    sets = []
    res = run(enumerate_streams=lambda: iter([("v1", "Sunday Morning Class & Worship - July 5, 2026")]),
              fetch_thumbnail=lambda v: b"AUTO",
              render=lambda l, p: (Path(p).write_text("x") or Path(p)),
              set_thumbnail=lambda v, p: sets.append(v),
              out_dir=tmp_path, dry_run=True, is_card=lambda t, p: False)
    assert res[0].rendered and not res[0].set_ok and sets == []


def test_run_records_set_error_and_continues(tmp_path):
    from pathlib import Path
    from sermon_cut.livestream_thumbs import run
    streams = [("v1", "Sunday Morning Class & Worship - July 5, 2026"),
               ("v2", "Sunday Morning Class & Worship - June 28, 2026")]

    def setter(vid, png):
        raise RuntimeError("boom-429")

    res = run(enumerate_streams=lambda: iter(streams),
              fetch_thumbnail=lambda v: b"AUTO",
              render=lambda l, p: (Path(p).write_text("x") or Path(p)),
              set_thumbnail=setter, out_dir=tmp_path, is_card=lambda t, p: False)
    assert len(res) == 2                       # keeps going past the first failure
    assert all(not r.set_ok for r in res)
    assert all("boom-429" in r.error for r in res)   # the reason is captured


def test_run_records_render_error_and_continues(tmp_path):
    from pathlib import Path
    from sermon_cut.livestream_thumbs import run
    streams = [("v1", "Sunday Morning Class & Worship - July 5, 2026"),
               ("v2", "Sunday Morning Class & Worship - June 28, 2026")]
    def render(label, p):
        if "July" in label:
            raise ValueError("too long")
        return Path(p).write_text("x") or Path(p)
    res = run(enumerate_streams=lambda: iter(streams), fetch_thumbnail=lambda v: b"AUTO",
              render=render, set_thumbnail=lambda v, p: None, out_dir=tmp_path,
              is_card=lambda t, p: False)
    assert [(r.video_id, r.rendered, r.set_ok) for r in res] == [("v1", False, False), ("v2", True, True)]
    assert "render: too long" in res[0].error


def test_run_idempotent_skips_existing_render(tmp_path):
    from pathlib import Path
    from sermon_cut.livestream_thumbs import run
    (tmp_path / "v1.png").write_text("already")   # pre-existing render
    calls = []
    run(enumerate_streams=lambda: iter([("v1", "Sunday Morning Class & Worship - July 5, 2026")]),
        fetch_thumbnail=lambda v: b"AUTO",
        render=lambda l, p: calls.append(p) or Path(p),
        set_thumbnail=lambda v, p: None, out_dir=tmp_path, is_card=lambda t, p: False)
    assert calls == []   # render skipped because PNG existed


def test_run_with_ids_only_touches_the_requested_streams(tmp_path):
    """--ids must filter BEFORE any thumbnail fetch: on 2026-11-15 a read timeout
    on an unrelated old stream's thumbnail killed that week's run."""
    from pathlib import Path
    from sermon_cut.livestream_thumbs import run
    streams = [("v1", "Sunday Morning Class & Worship - July 5, 2026"),
               ("v2", "Sunday Morning Class & Worship - June 28, 2026")]
    fetched, rendered = [], []

    def fetch(v):
        fetched.append(v)
        if v == "v2":
            raise AssertionError("fetched a stream outside --ids")
        return b"AUTO"

    def render(label, out_path):
        Path(out_path).write_text("png")
        rendered.append(str(out_path))
        return Path(out_path)

    res = run(enumerate_streams=lambda: iter(streams), fetch_thumbnail=fetch,
              render=render, set_thumbnail=lambda v, p: None, out_dir=tmp_path,
              ids=["v1"], is_card=lambda t, p: False)
    assert [r.video_id for r in res] == ["v1"]
    assert fetched == ["v1"]
    assert len(rendered) == 1


def test_fetch_thumbnail_treats_a_read_timeout_as_no_thumbnail(monkeypatch):
    """A socket timeout during read() raises a bare TimeoutError, not URLError."""
    import urllib.request
    from sermon_cut import cli

    class Slow:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self):
            raise TimeoutError("The read operation timed out")

    calls = []
    def fake_urlopen(req, timeout):
        calls.append(req.full_url)
        return Slow()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert cli._ls_fetch_thumbnail("abc") is None
    assert len(calls) == 2   # maxres, then hq fallback


def test_fetch_thumbnail_treats_a_truncated_body_as_no_thumbnail(monkeypatch):
    import http.client
    import urllib.request
    from sermon_cut import cli

    class Cut:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self):
            raise http.client.IncompleteRead(b"partial", 100)

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: Cut())
    assert cli._ls_fetch_thumbnail("abc") is None
