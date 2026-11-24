from __future__ import annotations
from datetime import datetime
from pathlib import Path

# Brand constants (1920x1080). The background PNG supplies the logo + church
# name in its upper third; text is drawn into the clear zone below.
_CANVAS = (1920, 1080)
_SIDE_MARGIN = 240                           # ~12.5% each side
_USABLE_W = _CANVAS[0] - 2 * _SIDE_MARGIN    # 1440
_WHITE = (245, 245, 240)
_GOLD = (199, 166, 107)
_TITLE_MAX, _TITLE_MIN = 150, 100            # big, heavy; below min -> too long
_TITLE_MAX_LINES = 3
_TITLE_LEADING = 1.05
_EYEBROW_SIZE, _EYEBROW_TRACK = 30, 4        # "SPEAKER · DATE" caps line
_BLOCK_TOP = 560                             # top of the text block (under wordmark)
_META_GAP = 46                               # eyebrow -> title
_BOTTOM_MARGIN = 70                           # keep the title clear of the frame edge
_RULE_OFFSET, _RULE_W = 40, 6                # vertical accent rule, left of text


class TitleTooLongError(ValueError):
    """The sermon title cannot fit the card at the minimum font size — the
    caller should stop and ask for a shorter title rather than shrink further."""


def format_date(iso: str) -> str:
    """Format an ISO date (YYYY-MM-DD) as 'Month D, YYYY' (no zero-padded day)."""
    dt = datetime.strptime(iso, "%Y-%m-%d")
    return f"{dt.strftime('%B')} {dt.day}, {dt.year}"


def _pack(words, limit, size, measure):
    """Greedily pack words into lines no wider than limit (a word too wide for
    the limit takes its own line). Returns the list of lines."""
    lines, current = [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if not current or measure(candidate, size)[0] <= limit:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _wrap(text, max_width, size, measure):
    """Greedily wrap words so each line fits max_width at the given font size."""
    return _pack(text.split(), max_width, size, measure)


def _wrap_balanced(text, max_width, size, measure):
    """Wrap into the fewest lines that fit max_width, then even out the line
    widths — binary-search the smallest width limit that still needs the same
    number of lines, so words spread evenly instead of orphaning a short last
    line ("Remember the / Sabbath Day", not "Remember the Sabbath / Day")."""
    words = text.split()
    if len(words) <= 1:
        return words or [""]
    target = len(_pack(words, max_width, size, measure))
    lo = max(int(measure(w, size)[0]) for w in words)   # can't beat widest word
    hi = int(measure(text, size)[0])                     # everything on one line
    while lo < hi:
        mid = (lo + hi) // 2
        if len(_pack(words, mid, size, measure)) <= target:
            hi = mid
        else:
            lo = mid + 1
    return _pack(words, lo, size, measure)


def fit_and_wrap(text, max_width, max_lines, max_height, max_font, min_font, measure):
    """Wrap and shrink text to fit a box.

    Try font sizes from max_font down to min_font; return the first (largest)
    size whose balanced wrap fits within max_width, at most max_lines lines, and
    max_height total. If none fits (title too long), fall back to the min_font
    wrap — the caller checks the returned line count / width / height and decides
    whether to reject it. ``measure(text, size) -> (w, h)``. Returns
    ``(lines, font_size)``.
    """
    for size in range(int(max_font), int(min_font) - 1, -1):
        lines = _wrap_balanced(text, max_width, size, measure)
        widest = max((measure(ln, size)[0] for ln in lines), default=0)
        height = sum(measure(ln, size)[1] for ln in lines)
        if widest <= max_width and len(lines) <= max_lines and height <= max_height:
            return lines, size
    size = int(min_font)
    return _wrap_balanced(text, max_width, size, measure), size


def _load_font(path: Path, size: int, variation: str | None = None):
    from PIL import ImageFont
    if not Path(path).exists():
        raise FileNotFoundError(f"font not found: {path}")
    font = ImageFont.truetype(str(path), size)
    if variation:
        try:
            font.set_variation_by_name(variation)
        except Exception:
            pass
    return font


def _measurer(path: Path, variation: str, base: int = 100):
    """A measure(text, size) -> (w, h) backed by a real font, for fit_and_wrap.
    The TTF is loaded once at a base size and measurements are scaled linearly
    (glyph metrics scale with font size) — avoids reloading the font for every
    step of the size sweep and width binary-search."""
    font = _load_font(path, base, variation)
    cache = {}
    def measure(text, size):
        w = cache.get(text)
        if w is None:
            left, _t, right, _b = font.getbbox(text)
            w = cache[text] = right - left
        # height is the em (font size); block height is measured in ems and the
        # caller budgets against em-space so it stays consistent with leading.
        return (w * size / base, size)
    return measure


def _draw_tracked(draw, left_x, cy, text, font, fill, tracking):
    """Draw letter-spaced text starting at left_x, vertically centered on cy."""
    x = left_x
    for ch in text:
        draw.text((x, cy), ch, font=font, fill=fill, anchor="lm")
        x += draw.textlength(ch, font=font) + tracking


def _tracked_width(draw, text, font, tracking):
    return (sum(draw.textlength(ch, font=font) for ch in text)
            + tracking * max(0, len(text) - 1))


def build_livestream_card(label, *, background_path, font_dir, out_path) -> Path:
    """One-line ink title card ('<date> Livestream') in the sermon-title format —
    no eyebrow. The line sits at the same baseline a sermon title starts at, so
    the two card types line up. Reuses the same fit/measure/draw helpers. Raises
    TitleTooLongError if the label can't fit at the minimum font size."""
    from PIL import Image, ImageDraw

    out_path = Path(out_path)
    background_path = Path(background_path)
    if not background_path.exists():
        raise FileNotFoundError(f"ink background not found: {background_path}")
    lora = Path(font_dir) / "Lora.ttf"

    img = Image.open(background_path).convert("RGB").resize(_CANVAS)
    draw = ImageDraw.Draw(img)
    left = _SIDE_MARGIN

    # Anchor the single line where a sermon *title*'s first line sits: block top
    # + the (unshrunk) eyebrow height + the eyebrow->title gap.
    eyebrow_h = int(_EYEBROW_SIZE * 1.4)
    top = _BLOCK_TOP + eyebrow_h + _META_GAP

    measure_title = _measurer(lora, "Bold")
    avail_h = (_CANVAS[1] - _BOTTOM_MARGIN) - top
    max_em = avail_h / _TITLE_LEADING
    lines, size = fit_and_wrap(
        label, _USABLE_W, _TITLE_MAX_LINES, max_em, _TITLE_MAX, _TITLE_MIN,
        measure_title,
    )
    widest = max((measure_title(ln, size)[0] for ln in lines), default=0)
    if (len(lines) > _TITLE_MAX_LINES or widest > _USABLE_W
            or len(lines) * size > max_em):
        raise TitleTooLongError(f"Livestream label {label!r} does not fit the card.")

    title_font = _load_font(lora, size, "Bold")
    title_lh = int(size * _TITLE_LEADING)
    block_h = len(lines) * title_lh

    draw.rectangle(
        [left - _RULE_OFFSET, top, left - _RULE_OFFSET + _RULE_W, top + block_h],
        fill=_GOLD,
    )
    y = top
    for line in lines:
        draw.text((left, y + title_lh / 2), line, font=title_font,
                  fill=_WHITE, anchor="lm")
        y += title_lh

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    return out_path


def build_title_card(plan, *, background_path, font_dir, workdir) -> Path:
    """Composite this sermon's title/speaker/date onto the branded background.

    Reads plan.title/speaker/date, draws into the clear zone using vendored Lora
    (serif) and Nunito (sans), and writes workdir/title_card.png. Idempotent.
    """
    from PIL import Image, ImageDraw

    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    out = workdir / "title_card.png"
    if out.exists():
        return out

    background_path = Path(background_path)
    if not background_path.exists():
        raise FileNotFoundError(f"intro background not found: {background_path}")
    font_dir = Path(font_dir)
    lora, nunito = font_dir / "Lora.ttf", font_dir / "Nunito.ttf"

    img = Image.open(background_path).convert("RGB").resize(_CANVAS)
    draw = ImageDraw.Draw(img)
    left, top = _SIDE_MARGIN, _BLOCK_TOP

    # Eyebrow: "SPEAKER  ·  DATE" in gold caps, shrunk to fit the usable width.
    eyebrow = f"{plan.speaker.upper()}   ·   {format_date(plan.date).upper()}"
    eb_size = _EYEBROW_SIZE
    eb_font = _load_font(nunito, eb_size, "SemiBold")
    while eb_size > 20 and _tracked_width(draw, eyebrow, eb_font, _EYEBROW_TRACK) > _USABLE_W:
        eb_size -= 1
        eb_font = _load_font(nunito, eb_size, "SemiBold")
    eyebrow_h = int(eb_size * 1.4)

    # Title: bold serif, as large as possible within _TITLE_MAX_LINES and the
    # vertical space beneath the eyebrow. If it would need to shrink below
    # _TITLE_MIN (too many lines, a word overflows, or it runs off the frame),
    # it's too long — reject rather than accommodate an unreadable tiny title.
    measure_title = _measurer(lora, "Bold")
    avail_h = (_CANVAS[1] - _BOTTOM_MARGIN) - (top + eyebrow_h + _META_GAP)
    max_em = avail_h / _TITLE_LEADING  # block height budget, in em units
    lines, title_size = fit_and_wrap(
        plan.title, _USABLE_W, _TITLE_MAX_LINES, max_em, _TITLE_MAX, _TITLE_MIN,
        measure_title,
    )
    widest = max((measure_title(ln, title_size)[0] for ln in lines), default=0)
    if (len(lines) > _TITLE_MAX_LINES or widest > _USABLE_W
            or len(lines) * title_size > max_em):
        raise TitleTooLongError(
            f"Title {plan.title!r} does not fit the card within "
            f"{_TITLE_MAX_LINES} lines at the {_TITLE_MIN}px minimum font. "
            f"Shorten plan.title and retry."
        )
    title_font = _load_font(lora, title_size, "Bold")
    title_lh = int(title_size * _TITLE_LEADING)

    block_h = eyebrow_h + _META_GAP + len(lines) * title_lh

    # Vertical gold accent rule anchoring the left-aligned block.
    draw.rectangle(
        [left - _RULE_OFFSET, top, left - _RULE_OFFSET + _RULE_W, top + block_h],
        fill=_GOLD,
    )

    y = top
    _draw_tracked(draw, left, y + eyebrow_h / 2, eyebrow, eb_font, _GOLD,
                  _EYEBROW_TRACK)
    y += eyebrow_h + _META_GAP
    for line in lines:
        draw.text((left, y + title_lh / 2), line, font=title_font,
                  fill=_WHITE, anchor="lm")
        y += title_lh

    img.save(out)
    return out
