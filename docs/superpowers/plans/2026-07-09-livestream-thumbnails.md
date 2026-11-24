# Livestream Thumbnails (one-off) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. This plan ends with live operations on public YouTube videos with a human pilot gate, so it is intended for **inline execution in this session**, not subagent dispatch.

**Goal:** Set a branded ink title-card thumbnail (`"<date> Livestream"`) on all ~22 of the church channel's 2026 Sunday livestreams, reusing the sermon card's drawing format.

**Architecture:** A self-contained script under `experiments/livestream-thumbs/` (untracked, throwaway — like `experiments/shorts/`). It imports the sermon card's drawing helpers from `sermon_cut.title_card` so the format matches by construction, enumerates the `/streams` tab via yt-dlp (metadata only), renders one PNG per video onto `assets/intro_bg_ink.png`, and sets each as a custom thumbnail via the existing `youtube.upload` OAuth token. Nothing in the tested `sermon_cut/` package changes.

**Tech Stack:** Python 3.13, Pillow (via title_card, lazy-imported), yt-dlp (subprocess), google-api-python-client (via `sermon_cut.youtube_auth`), pytest for the pure helpers.

## Global Constraints

- **No new code in `sermon_cut/`** — the package and its 86-test suite stay untouched. All new code lives in `experiments/livestream-thumbs/`.
- **Reuse, don't copy** — card drawing imports helpers/constants from `sermon_cut.title_card`; do not duplicate the layout math.
- **No re-auth, no new scope** — use `get_authenticated_service(Path("client_secrets.json"), Path("token.json"))`; the existing `youtube.upload` scope authorizes `thumbnails.set`.
- **No full-video download** — enumeration is `yt-dlp --flat-playlist` (metadata only). Never fetch the 2hr streams.
- **Background:** `assets/intro_bg_ink.png` (already saved; gitignored). 1920×1080.
- **Fonts:** `assets/fonts/` (`Lora.ttf`, `Nunito.ttf`).
- **Channel:** `UCxxxxxxxxxxxxxxxxxxxxxx`. Streams URL: `https://www.youtube.com/channel/UCxxxxxxxxxxxxxxxxxxxxxx/streams`.
- **Card text format:** `"<Month> <D><ordinal>, <YYYY> Livestream"` (e.g. `July 5th, 2026 Livestream`). Date parsed from the video **title**.
- **Scope:** only titles whose parsed year is **2026**. Skip + report anything that doesn't parse.
- **Pilot gate:** July 5 (`emJCaRPvxYw`) and June 28 (`QpRYHCnzar4`) first; batch the rest only after Kyle approves the live result.

---

### Task 1: Date helpers (`dates.py`)

Pure string/number functions — the only unit-tested part. No heavy deps.

**Files:**
- Create: `experiments/livestream-thumbs/dates.py`
- Test: `experiments/livestream-thumbs/test_dates.py`

**Interfaces:**
- Produces:
  - `ordinal(n: int) -> str` — `1`→`"1st"`, `2`→`"2nd"`, `3`→`"3rd"`, `4`→`"4th"`, `11`→`"11th"`, `21`→`"21st"`.
  - `parse_service_date(title: str) -> tuple[int,int,int] | None` — returns `(year, month, day)` or `None` if no valid date.
  - `livestream_label(title: str) -> str | None` — the full card string, or `None` if unparseable.

- [ ] **Step 1: Write the failing tests**

```python
# experiments/livestream-thumbs/test_dates.py
from dates import ordinal, parse_service_date, livestream_label

def test_ordinal_common():
    assert ordinal(1) == "1st"
    assert ordinal(2) == "2nd"
    assert ordinal(3) == "3rd"
    assert ordinal(4) == "4th"

def test_ordinal_teens_and_twenties():
    assert ordinal(11) == "11th"
    assert ordinal(12) == "12th"
    assert ordinal(13) == "13th"
    assert ordinal(21) == "21st"
    assert ordinal(22) == "22nd"

def test_parse_clean():
    assert parse_service_date("Sunday Morning Class & Worship - July 5, 2026") == (2026, 7, 5)

def test_parse_no_space_after_dash():
    assert parse_service_date("Sunday Morning Class & Worship -April 20, 2026") == (2026, 4, 20)

def test_parse_double_space():
    assert parse_service_date("Sunday Morning Class & Worship - April 4,  2026") == (2026, 4, 4)

def test_parse_abbrev_and_ordinal_in_title():
    assert parse_service_date("Sunday Bible Class and Worship - Jan 28, 2026") == (2026, 1, 28)
    assert parse_service_date("Wednesday Evening Class - February 14th, 2026") == (2026, 2, 14)

def test_parse_match_typo_normalized():
    assert parse_service_date("Sunday Morning Class & Worship - Match 31, 2026") == (2026, 3, 31)

def test_parse_unparseable_returns_none():
    assert parse_service_date("Sunday Morning Class & Worship") is None
    assert parse_service_date("Random stream title") is None

def test_label():
    assert livestream_label("Sunday Morning Class & Worship - July 5, 2026") == "July 5th, 2026 Livestream"
    assert livestream_label("Sunday Morning Class & Worship - June 21, 2026") == "June 21st, 2026 Livestream"
    assert livestream_label("no date here") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd experiments/livestream-thumbs && ../../.venv/bin/python -m pytest test_dates.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'dates'` / import error.

- [ ] **Step 3: Write the implementation**

```python
# experiments/livestream-thumbs/dates.py
"""Parse a service date out of a livestream title and format the card label.
Pure functions only — no PIL/yt-dlp/google imports. One-off helper."""
from __future__ import annotations
import re

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

# "<Month> <Day>[st/nd/rd/th], <Year>" — tolerant of extra spaces and a
# trailing ordinal already in the title.
_DATE_RE = re.compile(
    r"([A-Za-z]+)\.?\s+(\d{1,2})(?:st|nd|rd|th)?\s*,\s*(\d{4})", re.IGNORECASE
)


def ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def parse_service_date(title: str) -> tuple[int, int, int] | None:
    m = _DATE_RE.search(title)
    if not m:
        return None
    month = _MONTHS.get(m.group(1).lower())
    if month is None:
        return None
    day, year = int(m.group(2)), int(m.group(3))
    if not (1 <= day <= 31):
        return None
    return (year, month, day)


def livestream_label(title: str) -> str | None:
    parsed = parse_service_date(title)
    if parsed is None:
        return None
    year, month, day = parsed
    return f"{_MONTH_NAMES[month]} {ordinal(day)}, {year} Livestream"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd experiments/livestream-thumbs && ../../.venv/bin/python -m pytest test_dates.py -q`
Expected: PASS (10 tests).

- [ ] **Step 5: Confirm the package suite is unaffected**

Run: `.venv/bin/python -m pytest -q` (from repo root)
Expected: the experiment test files are outside package collection; the 86 package tests still pass. (If root collection picks up `test_dates.py`, that's fine — it's pure and green.)

---

### Task 2: Card renderer (`card.py`)

Reuses the sermon card's helpers to draw one left-aligned line on the ink background at the sermon-title baseline. Verified visually (render a sample and look at it), not by unit test.

**Files:**
- Create: `experiments/livestream-thumbs/card.py`

**Interfaces:**
- Consumes: `sermon_cut.title_card` helpers/constants (`_CANVAS`, `_SIDE_MARGIN`, `_USABLE_W`, `_WHITE`, `_GOLD`, `_TITLE_MAX`, `_TITLE_MIN`, `_TITLE_MAX_LINES`, `_TITLE_LEADING`, `_EYEBROW_SIZE`, `_BLOCK_TOP`, `_META_GAP`, `_BOTTOM_MARGIN`, `_RULE_OFFSET`, `_RULE_W`, `_load_font`, `_measurer`, `fit_and_wrap`, `TitleTooLongError`).
- Produces: `render_card(text: str, *, background_path, font_dir, out_path) -> Path`.

- [ ] **Step 1: Write the renderer**

```python
# experiments/livestream-thumbs/card.py
"""Render a single-line livestream title card on the ink background, reusing
the sermon card's drawing helpers so the format matches exactly."""
from __future__ import annotations
from pathlib import Path

from sermon_cut.title_card import (
    _CANVAS, _SIDE_MARGIN, _USABLE_W, _WHITE, _GOLD,
    _TITLE_MAX, _TITLE_MIN, _TITLE_MAX_LINES, _TITLE_LEADING,
    _EYEBROW_SIZE, _BLOCK_TOP, _META_GAP, _BOTTOM_MARGIN,
    _RULE_OFFSET, _RULE_W, _load_font, _measurer, fit_and_wrap,
    TitleTooLongError,
)


def render_card(text: str, *, background_path, font_dir, out_path) -> Path:
    from PIL import Image, ImageDraw

    background_path = Path(background_path)
    if not background_path.exists():
        raise FileNotFoundError(f"ink background not found: {background_path}")
    lora = Path(font_dir) / "Lora.ttf"

    img = Image.open(background_path).convert("RGB").resize(_CANVAS)
    draw = ImageDraw.Draw(img)
    left = _SIDE_MARGIN

    # Anchor the single line at the same y a sermon *title* starts: block top
    # + the (unshrunk) eyebrow height + the eyebrow->title gap. Keeps livestream
    # and sermon cards sharing one baseline.
    eyebrow_h = int(_EYEBROW_SIZE * 1.4)
    top = _BLOCK_TOP + eyebrow_h + _META_GAP

    measure_title = _measurer(lora, "Bold")
    avail_h = (_CANVAS[1] - _BOTTOM_MARGIN) - top
    max_em = avail_h / _TITLE_LEADING
    lines, size = fit_and_wrap(
        text, _USABLE_W, _TITLE_MAX_LINES, max_em, _TITLE_MAX, _TITLE_MIN,
        measure_title,
    )
    widest = max((measure_title(ln, size)[0] for ln in lines), default=0)
    if (len(lines) > _TITLE_MAX_LINES or widest > _USABLE_W
            or len(lines) * size > max_em):
        raise TitleTooLongError(f"Livestream label {text!r} does not fit the card.")

    font = _load_font(lora, size, "Bold")
    lh = int(size * _TITLE_LEADING)
    block_h = len(lines) * lh

    draw.rectangle(
        [left - _RULE_OFFSET, top, left - _RULE_OFFSET + _RULE_W, top + block_h],
        fill=_GOLD,
    )
    y = top
    for line in lines:
        draw.text((left, y + lh / 2), line, font=font, fill=_WHITE, anchor="lm")
        y += lh

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    return out_path
```

- [ ] **Step 2: Render a sample and inspect it**

Run:
```bash
cd experiments/livestream-thumbs && ../../.venv/bin/python -c "
from card import render_card
render_card('July 5th, 2026 Livestream',
            background_path='../../assets/intro_bg_ink.png',
            font_dir='../../assets/fonts', out_path='out/_sample.png')
print('rendered out/_sample.png')
"
```
Expected: writes `out/_sample.png`. Open it (Read tool) and confirm: ink background, gold logo/wordmark intact, one white Lora-Bold line reading "July 5th, 2026 Livestream", gold vertical rule to its left, line sitting at the sermon-title height. File < 2 MB.

- [ ] **Step 3: Commit Tasks 1–2**

```bash
git add experiments/livestream-thumbs/dates.py experiments/livestream-thumbs/test_dates.py experiments/livestream-thumbs/card.py
git commit -m "feat(livestream-thumbs): date helpers + ink card renderer"
```
(Note: `out/` and `assets/*.png` are gitignored; only source is committed. If `experiments/` is fully untracked by preference, this commit is optional — decide at execution time.)

---

### Task 3: Enumerate + orchestrate (`run.py`)

Ties it together with a CLI. No unit test — it's I/O glue exercised by the pilot.

**Files:**
- Create: `experiments/livestream-thumbs/run.py`

**Interfaces:**
- Consumes: `dates.livestream_label`, `card.render_card`, `sermon_cut.youtube_auth.get_authenticated_service`.
- Produces: CLI `python run.py [--render-only] [--ids A,B] [--limit N]`.

- [ ] **Step 1: Write the orchestrator**

```python
# experiments/livestream-thumbs/run.py
"""One-off: set ink '<date> Livestream' thumbnails on the channel's 2026
Sunday livestreams. Enumerate via yt-dlp, render via card.py, set via the
existing youtube.upload OAuth token. Run from experiments/livestream-thumbs/."""
from __future__ import annotations
import argparse, json, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root on path
from dates import livestream_label, parse_service_date
from card import render_card

CHANNEL = "UCxxxxxxxxxxxxxxxxxxxxxx"
STREAMS_URL = f"https://www.youtube.com/channel/{CHANNEL}/streams"
REPO = Path(__file__).resolve().parents[2]
BG = REPO / "assets" / "intro_bg_ink.png"
FONTS = REPO / "assets" / "fonts"
OUT = Path(__file__).resolve().parent / "out"


def enumerate_2026():
    """Return [(video_id, title, label)] for 2026 streams, newest first."""
    proc = subprocess.run(
        ["yt-dlp", "--flat-playlist", "--dump-json", STREAMS_URL],
        capture_output=True, text=True, check=True,
    )
    rows, skipped = [], []
    for line in proc.stdout.splitlines():
        v = json.loads(line)
        vid, title = v.get("id"), v.get("title", "")
        parsed = parse_service_date(title)
        if parsed is None:
            skipped.append((vid, title, "unparseable"))
            continue
        if parsed[0] != 2026:
            continue
        rows.append((vid, title, livestream_label(title)))
    return rows, skipped


def set_thumbnail(video_id: str, png: Path):
    from googleapiclient.http import MediaFileUpload
    from sermon_cut.youtube_auth import get_authenticated_service
    yt = get_authenticated_service(REPO / "client_secrets.json", REPO / "token.json")
    yt.thumbnails().set(
        videoId=video_id,
        media_body=MediaFileUpload(str(png), mimetype="image/png"),
    ).execute()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--render-only", action="store_true", help="render PNGs, don't upload")
    ap.add_argument("--ids", help="comma-separated video ids to restrict to")
    ap.add_argument("--limit", type=int, help="cap number of videos")
    args = ap.parse_args()

    rows, skipped = enumerate_2026()
    if args.ids:
        want = set(args.ids.split(","))
        rows = [r for r in rows if r[0] in want]
    if args.limit:
        rows = rows[: args.limit]

    print(f"{len(rows)} target videos; {len(skipped)} skipped (non-2026 not shown)")
    for vid, title, _ in skipped:
        if vid:  # only report genuinely unparseable ones
            print(f"  SKIP {vid}  {title!r}")

    results = []
    for vid, title, label in rows:
        png = OUT / f"{vid}.png"
        try:
            render_card(label, background_path=BG, font_dir=FONTS, out_path=png)
            rendered = True
        except Exception as e:
            print(f"  RENDER FAIL {vid} {label!r}: {e}")
            results.append((vid, label, False, False))
            continue
        set_ok = False
        if not args.render_only:
            try:
                set_thumbnail(vid, png)
                set_ok = True
            except Exception as e:
                print(f"  SET FAIL {vid}: {e}")
        results.append((vid, label, rendered, set_ok))
        print(f"  {vid}  {label:<28}  render={'ok' if rendered else 'X'}  "
              f"set={'ok' if set_ok else ('-' if args.render_only else 'X')}")

    print("\nDONE. rendered "
          f"{sum(1 for r in results if r[2])}/{len(results)}, "
          f"set {sum(1 for r in results if r[3])}/{len(results)}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Dry-run enumeration (render-only, no upload)**

Run: `cd experiments/livestream-thumbs && ../../.venv/bin/python run.py --render-only`
Expected: prints ~22 target rows, each `render=ok set=-`; writes `out/<id>.png` for each; lists any genuinely unparseable titles. Spot-check 2–3 PNGs (Read tool) for correct dates.

- [ ] **Step 3: Commit**

```bash
git add experiments/livestream-thumbs/run.py
git commit -m "feat(livestream-thumbs): yt-dlp enumeration + thumbnail-set orchestrator"
```

---

### Task 4: Pilot (July 5 + June 28) — live gate

**Files:** none (execution).

- [ ] **Step 1: Set the two pilot thumbnails**

Run: `cd experiments/livestream-thumbs && ../../.venv/bin/python run.py --ids emJCaRPvxYw,QpRYHCnzar4`
Expected: both print `render=ok set=ok`.

- [ ] **Step 2: Verify live**

Open `https://youtu.be/emJCaRPvxYw` and `https://youtu.be/QpRYHCnzar4` (or the channel's Studio content list). Confirm the new ink cards show as the thumbnails (allow 1–2 min for CDN propagation). Confirm the dates read "July 5th, 2026 Livestream" and "June 28th, 2026 Livestream".

- [ ] **Step 3: HUMAN GATE — get Kyle's approval**

Stop and ask Kyle to confirm the live pilot thumbnails look right before batching. Do not proceed without explicit approval.

---

### Task 5: Batch the rest

**Files:** none (execution).

- [ ] **Step 1: Set all remaining 2026 thumbnails**

Run: `cd experiments/livestream-thumbs && ../../.venv/bin/python run.py`
Expected: every 2026 video prints `render=ok set=ok` (the two pilots re-set harmlessly). Final line reports `set N/N`.

- [ ] **Step 2: Report**

Print the final table (id, label, render, set) and an explicit list of any skipped/failed videos. If any `set=X`, note the error (commonly a transient quota/HTTP error — re-run `run.py`, which re-renders idempotently and re-sets).

---

## Self-Review

**Spec coverage:**
- Ink card, same format, single line, no eyebrow → Task 2. ✓
- Ordinal date from title, skip-and-report unparseable → Task 1 + Task 3. ✓
- 2026 scope, yt-dlp enumeration, metadata-only → Task 3. ✓
- Existing token / `thumbnails.set`, no re-auth → Task 3 (`set_thumbnail`). ✓
- Pilot-first rollout with human gate → Task 4, then Task 5. ✓
- No package changes, throwaway location → all tasks under `experiments/livestream-thumbs/`. ✓
- Reversibility / idempotent render → `out/<id>.png` reused; re-set is safe. ✓

**Placeholder scan:** none — all code is complete.

**Type consistency:** `livestream_label`/`parse_service_date`/`ordinal` signatures match between `dates.py`, its tests, and `run.py`; `render_card(text, *, background_path, font_dir, out_path)` matches between `card.py` and `run.py`. ✓
