"""Scan the channel's Sunday livestreams for ones lacking a branded thumbnail,
render the ink '<date> Livestream' card, and set it. Pure logic + injectable I/O
— no PIL/google/yt-dlp at module top level (PIL is lazy-imported inside
thumbnail_stats; enumeration/fetch/set are injected callables)."""
from __future__ import annotations
import re
from dataclasses import dataclass
from pathlib import Path

# "Already carded" means the live thumbnail IS the card we would render for this
# stream, judged by image distance (mean abs diff on a 64x36 grayscale copy).
# Measured on real images: the same card after YouTube's JPEG re-encode 0.08;
# a card with a DIFFERENT date 2.8; the generic "Livestream" placeholder the
# church sets at stream time 8.3; an auto video frame 74.7. The earlier
# "dark + near-grayscale == branded" heuristic could not tell the placeholder
# from our card and skipped 2026-09-06's stream.
CARD_MATCH_MAX_DISTANCE = 1.0

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
    "match": 3,  # recurring typo for "March" in the catalog
}
_MONTH_NAMES = ["", "January", "February", "March", "April", "May", "June",
                "July", "August", "September", "October", "November", "December"]
_DATE_RE = re.compile(
    r"([A-Za-z]+)\.?\s+(\d{1,2})(?:st|nd|rd|th)?\s*,\s*(\d{4})", re.IGNORECASE
)


@dataclass
class Candidate:
    video_id: str
    title: str
    label: str
    card: object = None      # Path of the rendered card, None if rendering failed


@dataclass
class Result:
    video_id: str
    label: str
    detected: str      # "auto" (needed a card) — the only kind that reaches here
    rendered: bool
    set_ok: bool
    error: str = ""    # reason set/render failed (e.g. a 429 rate-limit message)


def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def parse_service_date(title: str):
    m = _DATE_RE.search(title)
    if not m:
        return None
    month = _MONTHS.get(m.group(1).lower())
    if month is None:
        return None
    day, year = int(m.group(2)), int(m.group(3))
    return (year, month, day) if 1 <= day <= 31 else None


def livestream_label(title: str):
    parsed = parse_service_date(title)
    if parsed is None:
        return None
    year, month, day = parsed
    return f"{_MONTH_NAMES[month]} {ordinal(day)}, {year} Livestream"


def is_sunday_service(title: str) -> bool:
    t = title.lower()
    return "sunday" in t and "worship" in t


def card_distance(thumbnail_bytes: bytes, card_png) -> float:
    """Mean absolute pixel difference between the live thumbnail and our rendered
    card, both reduced to 64x36 grayscale (kills JPEG/scale noise, keeps text)."""
    from PIL import Image, ImageChops, ImageStat
    import io
    def small(im):
        return im.convert("L").resize((64, 36), Image.BILINEAR)
    a = small(Image.open(io.BytesIO(thumbnail_bytes)))
    b = small(Image.open(card_png))
    return ImageStat.Stat(ImageChops.difference(a, b)).mean[0]


def is_our_card(thumbnail_bytes: bytes, card_png, *,
                max_distance: float = CARD_MATCH_MAX_DISTANCE) -> bool:
    return card_distance(thumbnail_bytes, card_png) < max_distance


def thumbnail_stats(image_bytes: bytes) -> tuple[float, float]:
    """(brightness, saturation) of a downscaled RGB copy of the thumbnail.
    brightness = mean((r+g+b)/3); saturation = mean(max-min)."""
    from PIL import Image
    import io
    im = Image.open(io.BytesIO(image_bytes)).convert("RGB").resize((160, 90))
    data = im.tobytes()   # flat RGB bytes, length = w*h*3
    n = len(data) // 3
    bright_sum = sat_sum = 0
    for i in range(0, len(data), 3):
        r, g, b = data[i], data[i + 1], data[i + 2]
        bright_sum += r + g + b
        sat_sum += max(r, g, b) - min(r, g, b)
    return (bright_sum / 3 / n, sat_sum / n)


def find_candidates(*, enumerate_streams, fetch_thumbnail, card_for,
                    is_card=None) -> list[Candidate]:
    """Sunday-service streams with a parseable date whose current thumbnail is
    NOT the card we would render. `enumerate_streams()` -> iterable of
    (video_id, title); `fetch_thumbnail(video_id)` -> bytes|None (None => needs a
    card); `card_for(video_id, label)` -> Path of the rendered card, or None if
    it could not be rendered (then we cannot judge, and it stays a candidate so
    the failure is reported)."""
    is_card = is_card or is_our_card
    out = []
    for video_id, title in enumerate_streams():
        if not is_sunday_service(title):
            continue
        label = livestream_label(title)
        if label is None:
            continue
        card = card_for(video_id, label)
        data = fetch_thumbnail(video_id)
        if card is not None and data is not None and is_card(data, card):
            continue  # already wearing our card — leave it alone
        out.append(Candidate(video_id=video_id, title=title, label=label, card=card))
    return out


def run(*, enumerate_streams, fetch_thumbnail, render, set_thumbnail, out_dir,
        dry_run=False, ids=None, limit=None, is_card=None) -> list[Result]:
    """Render each Sunday stream's card (idempotent: reuse an existing PNG),
    compare it with the live thumbnail, and unless dry_run, set it where it is
    missing. `render(label, out_path)->Path`; `set_thumbnail(video_id, png_path)
    ->None`. All I/O injected."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    render_errors: dict[str, str] = {}

    def card_for(video_id, label):
        png = out_dir / f"{video_id}.png"
        try:
            if not png.exists():
                render(label, png)
            return png
        except Exception as e:
            render_errors[video_id] = f"render: {e}"
            return None

    cands = find_candidates(enumerate_streams=enumerate_streams,
                            fetch_thumbnail=fetch_thumbnail, card_for=card_for,
                            is_card=is_card)
    if ids:
        want = set(ids)
        cands = [c for c in cands if c.video_id in want]
    if limit:
        cands = cands[:limit]

    results = []
    for c in cands:
        if c.card is None:
            results.append(Result(c.video_id, c.label, "auto", False, False,
                                  error=render_errors.get(c.video_id, "render: failed")))
            continue
        set_ok, error = False, ""
        if not dry_run:
            try:
                set_thumbnail(c.video_id, c.card)
                set_ok = True
            except Exception as e:
                error = str(e)
        results.append(Result(c.video_id, c.label, "auto", True, set_ok, error=error))
    return results
