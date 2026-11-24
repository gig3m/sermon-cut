from pathlib import Path
from types import SimpleNamespace
import pytest
from sermon_cut.title_card import format_date, fit_and_wrap, build_title_card

FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"


def _bg(path, color=(19, 105, 74)):
    from PIL import Image
    Image.new("RGB", (1920, 1080), color).save(path)
    return path


def _plan(title="Remember the Sabbath Day"):
    return SimpleNamespace(title=title, speaker="Clay Thornton", date="2026-07-05")


# Synthetic text measurer: width grows with char count and font size,
# height equals the font size. Deterministic, no real fonts needed.
def measure(text, size):
    return (len(text) * size * 0.5, size)


def test_fit_short_text_stays_one_line_at_max_font():
    lines, size = fit_and_wrap("A B", max_width=1000, max_lines=3, max_height=1000,
                               max_font=100, min_font=10, measure=measure)
    assert lines == ["A B"]
    assert size == 100


def test_fit_wraps_to_multiple_lines_at_max_font():
    lines, size = fit_and_wrap("one two three four", max_width=500, max_lines=5,
                               max_height=1000, max_font=100, min_font=10,
                               measure=measure)
    assert size == 100
    assert len(lines) == 2
    for ln in lines:
        assert measure(ln, size)[0] <= 500


def test_fit_balances_line_breaks_no_orphan():
    # greedy would pack "Remember the Sabbath" then orphan "Day";
    # balanced should split evenly into two lines of similar length.
    lines, size = fit_and_wrap("Remember the Sabbath Day", max_width=1100,
                               max_lines=3, max_height=1000, max_font=100,
                               min_font=10, measure=measure)
    assert lines == ["Remember the", "Sabbath Day"]


def test_fit_shrinks_font_to_respect_max_lines():
    # at max_font this needs 3 lines within width 500; must shrink to fit 2
    lines, size = fit_and_wrap("one two three four five six", max_width=500,
                               max_lines=2, max_height=100000, max_font=100,
                               min_font=10, measure=measure)
    assert len(lines) <= 2
    assert size < 100


def test_fit_shrinks_font_to_respect_max_height():
    # two lines at font 100 = height 200 > 150; must shrink so 2 lines fit
    lines, size = fit_and_wrap("one two three four", max_width=500, max_lines=9,
                               max_height=150, max_font=100, min_font=10,
                               measure=measure)
    assert size < 100
    assert len(lines) * size <= 150


def test_fit_never_exceeds_width_for_wrappable_text():
    text = "the quick brown fox jumps over the lazy dog again"
    lines, size = fit_and_wrap(text, max_width=400, max_lines=10, max_height=100000,
                               max_font=90, min_font=10, measure=measure)
    for ln in lines:
        # every multi-word line must fit; a lone over-long word is the only allowed overflow
        if " " in ln:
            assert measure(ln, size)[0] <= 400


def test_build_title_card_draws_onto_background(tmp_path):
    from PIL import Image
    bg = _bg(tmp_path / "intro_bg.png")
    out = build_title_card(_plan(), background_path=bg, font_dir=FONT_DIR,
                           workdir=tmp_path)
    assert out.exists()
    img = Image.open(out).convert("RGB")
    assert img.size == (1920, 1080)
    # text introduced colors beyond the solid background
    colors = img.getcolors(maxcolors=1_000_000)
    assert colors is None or len(colors) > 1


def test_build_title_card_left_justifies_text(tmp_path):
    from PIL import Image
    bg_color = (19, 105, 74)
    bg = _bg(tmp_path / "intro_bg.png", color=bg_color)
    out = build_title_card(_plan(title="Faith"), background_path=bg,
                           font_dir=FONT_DIR, workdir=tmp_path)
    img = Image.open(out).convert("RGB")
    px = img.load()
    # leftmost non-background pixel within the text zone
    min_x = img.width
    for y in range(520, 980, 4):
        for x in range(img.width):
            if px[x, y] != bg_color:
                min_x = min(min_x, x)
                break
    # left-justified: text begins near the 240px margin, NOT centered
    # (a centered short word "Faith" would begin near x~800)
    assert 200 <= min_x <= 340


def test_build_title_card_has_vertical_accent_rule(tmp_path):
    from PIL import Image
    bg_color = (19, 105, 74)
    bg = _bg(tmp_path / "intro_bg.png", color=bg_color)
    out = build_title_card(_plan(), background_path=bg, font_dir=FONT_DIR,
                           workdir=tmp_path)
    px = Image.open(out).convert("RGB").load()
    gold = (199, 166, 107)
    # a tall solid gold vertical rule sits just left of the text
    best = 0
    for x in range(170, 250):
        run = sum(1 for y in range(500, 1010) if px[x, y] == gold)
        best = max(best, run)
    assert best > 150


def test_build_title_card_is_idempotent(tmp_path):
    bg = _bg(tmp_path / "intro_bg.png")
    (tmp_path / "title_card.png").write_bytes(b"sentinel")
    out = build_title_card(_plan(), background_path=bg, font_dir=FONT_DIR,
                           workdir=tmp_path)
    assert out.read_bytes() == b"sentinel"  # not overwritten


def test_build_title_card_raises_when_title_too_long(tmp_path):
    from sermon_cut.title_card import TitleTooLongError
    bg = _bg(tmp_path / "intro_bg.png")
    huge = " ".join(["Everlasting"] * 20)
    with pytest.raises(TitleTooLongError):
        build_title_card(_plan(title=huge), background_path=bg,
                         font_dir=FONT_DIR, workdir=tmp_path)


def test_build_title_card_errors_on_missing_background(tmp_path):
    with pytest.raises(FileNotFoundError):
        build_title_card(_plan(), background_path=tmp_path / "nope.png",
                         font_dir=FONT_DIR, workdir=tmp_path)


def test_format_date_iso_to_long():
    assert format_date("2026-07-05") == "July 5, 2026"


def test_format_date_no_zero_padded_day():
    assert format_date("2026-01-09") == "January 9, 2026"


def test_format_date_rejects_malformed():
    with pytest.raises(ValueError):
        format_date("07/05/2026")


def test_build_livestream_card_writes(tmp_path):
    from sermon_cut.title_card import build_livestream_card
    out = build_livestream_card(
        "July 5th, 2026 Livestream",
        background_path="assets/intro_bg_ink.png", font_dir="assets/fonts",
        out_path=tmp_path / "card.png")
    assert out.exists() and out.stat().st_size > 1000


def test_build_livestream_card_too_long_raises(tmp_path):
    import pytest
    from sermon_cut.title_card import build_livestream_card, TitleTooLongError
    with pytest.raises(TitleTooLongError):
        build_livestream_card(
            "X" * 200, background_path="assets/intro_bg_ink.png",
            font_dir="assets/fonts", out_path=tmp_path / "c.png")
