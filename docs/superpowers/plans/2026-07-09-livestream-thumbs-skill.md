# `/livestream-thumbs` Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans (inline, this session — ends in a live channel scan). Steps use `- [ ]`. TDD throughout.

**Goal:** A `/livestream-thumbs` skill that scans the channel's Sunday-service livestreams for ones still on an auto-frame, renders the ink `"<date> Livestream"` card, and sets it — unattended.

**Architecture:** Pure decision logic + injectable I/O in `sermon_cut/livestream_thumbs.py`; the ink card added to `title_card.py`; a `livestream-thumbs` CLI subcommand wires real yt-dlp/HTTP/YouTube deps; a `SKILL.md` drives it.

**Tech Stack:** Python 3.13, Pillow (lazy), yt-dlp (subprocess), urllib (stdlib), google-api-python-client (existing auth), pytest.

## Global Constraints

- No new deps; no re-auth (existing `youtube.upload` token covers `thumbnails.set`); no full-video download.
- Heavy deps (PIL, google, yt-dlp/HTTP) never imported at module top level — lazy-import or inject.
- Detection thresholds: `_CARD_MAX_BRIGHTNESS = 70`, `_CARD_MAX_SATURATION = 12`; card iff `brightness < 70 and saturation < 12`.
- Scope: Sunday worship services only (title contains "sunday" and "worship", case-insensitive).
- Card text: `"<Month> <D><ordinal>, <YYYY> Livestream"`, date parsed from the video title.
- Channel: `youtube_channel_id` from config. Background `assets/intro_bg_ink.png`, fonts `assets/fonts/`, output `runs/livestream-thumbs/<id>.png`.
- Run CLI as `.venv/bin/python -m sermon_cut ...`.

---

### Task 1: Pure decision logic (`livestream_thumbs.py`)

**Files:**
- Create: `sermon_cut/livestream_thumbs.py`
- Test: `tests/test_livestream_thumbs.py`

**Interfaces — Produces:**
- `ordinal(n:int)->str`, `parse_service_date(title)->tuple[int,int,int]|None`, `livestream_label(title)->str|None`, `is_sunday_service(title)->bool`, `has_branded_card(brightness:float, saturation:float)->bool`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_livestream_thumbs.py
from sermon_cut.livestream_thumbs import (
    ordinal, parse_service_date, livestream_label, is_sunday_service,
    has_branded_card,
)

def test_ordinal():
    assert [ordinal(n) for n in (1,2,3,4,11,21,22)] == ["1st","2nd","3rd","4th","11th","21st","22nd"]

def test_parse_service_date_variants():
    assert parse_service_date("Sunday Morning Class & Worship - July 5, 2026") == (2026,7,5)
    assert parse_service_date("Sunday Morning Class & Worship -April 20, 2025") == (2025,4,20)
    assert parse_service_date("Sunday Bible Class and Worship - Jan 28, 2024") == (2024,1,28)
    assert parse_service_date("... - February 14th, 2024") == (2024,2,14)
    assert parse_service_date("... - Match 31, 2024") == (2024,3,31)
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

def test_has_branded_card():
    assert has_branded_card(35.0, 0.8)      # our card
    assert has_branded_card(34.6, 0.8)
    assert not has_branded_card(120.0, 51.0)  # auto frame
    assert not has_branded_card(65.0, 20.0)   # dark but colorful
    assert not has_branded_card(90.0, 5.0)    # gray but bright
```

- [ ] **Step 2: Run — expect fail** (`ModuleNotFoundError`).
Run: `.venv/bin/python -m pytest tests/test_livestream_thumbs.py -q`

- [ ] **Step 3: Implement**

```python
# sermon_cut/livestream_thumbs.py
"""Scan the channel's Sunday livestreams for ones lacking a branded thumbnail,
render the ink '<date> Livestream' card, and set it. Pure logic + injectable I/O
— no PIL/google/yt-dlp at module top level."""
from __future__ import annotations
import re
from dataclasses import dataclass
from pathlib import Path

_CARD_MAX_BRIGHTNESS = 70
_CARD_MAX_SATURATION = 12

_MONTHS = {
    "january":1,"february":2,"march":3,"april":4,"may":5,"june":6,"july":7,
    "august":8,"september":9,"october":10,"november":11,"december":12,
    "jan":1,"feb":2,"mar":3,"apr":4,"jun":6,"jul":7,"aug":8,"sep":9,"sept":9,
    "oct":10,"nov":11,"dec":12,"match":3,
}
_MONTH_NAMES = ["","January","February","March","April","May","June","July",
                "August","September","October","November","December"]
_DATE_RE = re.compile(r"([A-Za-z]+)\.?\s+(\d{1,2})(?:st|nd|rd|th)?\s*,\s*(\d{4})", re.I)


@dataclass
class Candidate:
    video_id: str
    title: str
    label: str


@dataclass
class Result:
    video_id: str
    label: str
    detected: str      # "auto" (needs card) | "carded" (skipped) | "unparsed"
    rendered: bool
    set_ok: bool


def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1:"st",2:"nd",3:"rd"}.get(n % 10, "th")
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
    y, mo, d = parsed
    return f"{_MONTH_NAMES[mo]} {ordinal(d)}, {y} Livestream"


def is_sunday_service(title: str) -> bool:
    t = title.lower()
    return "sunday" in t and "worship" in t


def has_branded_card(brightness: float, saturation: float) -> bool:
    return brightness < _CARD_MAX_BRIGHTNESS and saturation < _CARD_MAX_SATURATION
```

- [ ] **Step 4: Run — expect pass.** `.venv/bin/python -m pytest tests/test_livestream_thumbs.py -q`

- [ ] **Step 5: Commit** — `git add sermon_cut/livestream_thumbs.py tests/test_livestream_thumbs.py && git commit -m "feat(livestream-thumbs): pure scope/date/detection logic"`

---

### Task 2: Image stats + candidate finding

**Files:** Modify `sermon_cut/livestream_thumbs.py`; Test append `tests/test_livestream_thumbs.py`.

**Interfaces:**
- Consumes: PIL (lazy). Produces: `thumbnail_stats(image_bytes:bytes)->tuple[float,float]`; `find_candidates(*, enumerate_streams, fetch_thumbnail)->list[Candidate]`.
- `enumerate_streams()` → iterable of `(video_id, title)`. `fetch_thumbnail(video_id)->bytes|None` (None = unreachable → treat as needing a card).

- [ ] **Step 1: Write failing tests**

```python
def test_thumbnail_stats_dark_vs_bright():
    from PIL import Image
    import io
    def png(color):
        b = io.BytesIO(); Image.new("RGB",(160,90),color).save(b,"PNG"); return b.getvalue()
    from sermon_cut.livestream_thumbs import thumbnail_stats
    db, ds = thumbnail_stats(png((20,20,22)))     # dark gray
    bb, bs = thumbnail_stats(png((200,40,40)))    # bright red
    assert db < 70 and ds < 12
    assert bb > 70 and bs > 12

def test_find_candidates_filters():
    from sermon_cut.livestream_thumbs import find_candidates
    streams = [
        ("v_sun_auto",   "Sunday Morning Class & Worship - July 5, 2026"),
        ("v_sun_carded", "Sunday Morning Class & Worship - June 28, 2026"),
        ("v_wed",        "Wednesday Bible Class - June 4, 2025"),
        ("v_sun_nodate", "Sunday Morning Class & Worship"),
    ]
    STATS = {"v_sun_auto": b"AUTO", "v_sun_carded": b"CARD"}
    def enum(): return iter(streams)
    def fetch(vid): return STATS.get(vid, b"AUTO")
    # monkeypatch stats via a mapping keyed on bytes
    import sermon_cut.livestream_thumbs as M
    orig = M.thumbnail_stats
    M.thumbnail_stats = lambda b: (35.0,0.8) if b==b"CARD" else (120.0,51.0)
    try:
        cands = find_candidates(enumerate_streams=enum, fetch_thumbnail=fetch)
    finally:
        M.thumbnail_stats = orig
    ids = [c.video_id for c in cands]
    assert ids == ["v_sun_auto"]              # only Sunday + auto-frame + parseable
    assert cands[0].label == "July 5th, 2026 Livestream"
```

- [ ] **Step 2: Run — expect fail.**

- [ ] **Step 3: Implement (append)**

```python
def thumbnail_stats(image_bytes: bytes) -> tuple[float, float]:
    from PIL import Image
    import io
    im = Image.open(io.BytesIO(image_bytes)).convert("RGB").resize((160, 90))
    px = list(im.getdata())
    n = len(px)
    brightness = sum((r + g + b) / 3 for r, g, b in px) / n
    saturation = sum(max(r, g, b) - min(r, g, b) for r, g, b in px) / n
    return (brightness, saturation)


def find_candidates(*, enumerate_streams, fetch_thumbnail) -> list[Candidate]:
    out = []
    for video_id, title in enumerate_streams():
        if not is_sunday_service(title):
            continue
        label = livestream_label(title)
        if label is None:
            continue
        data = fetch_thumbnail(video_id)
        if data is not None:
            b, s = thumbnail_stats(data)
            if has_branded_card(b, s):
                continue  # already carded
        out.append(Candidate(video_id=video_id, title=title, label=label))
    return out
```

- [ ] **Step 4: Run — expect pass.**

- [ ] **Step 5: Commit** — `feat(livestream-thumbs): thumbnail stats + candidate detection`

---

### Task 3: `run()` orchestration

**Files:** Modify `sermon_cut/livestream_thumbs.py`; Test append.

**Interfaces — Produces:** `run(*, enumerate_streams, fetch_thumbnail, render, set_thumbnail, out_dir, dry_run=False, ids=None, limit=None)->list[Result]`. `render(label, out_path)->Path`; `set_thumbnail(video_id, png_path)->None`.

- [ ] **Step 1: Write failing test**

```python
def test_run_renders_and_sets(tmp_path):
    from sermon_cut.livestream_thumbs import run
    import sermon_cut.livestream_thumbs as M
    streams = [("v1","Sunday Morning Class & Worship - July 5, 2026"),
               ("v2","Sunday Morning Class & Worship - June 28, 2026")]
    M_orig = M.thumbnail_stats
    M.thumbnail_stats = lambda b: (120.0,51.0)  # all auto → all candidates
    rendered, sets = [], []
    def render(label, out_path):
        Path(out_path).write_text("png"); rendered.append((label,str(out_path))); return Path(out_path)
    def setter(vid, png): sets.append((vid,str(png)))
    try:
        res = run(enumerate_streams=lambda: iter(streams),
                  fetch_thumbnail=lambda v: b"AUTO",
                  render=render, set_thumbnail=setter, out_dir=tmp_path)
    finally:
        M.thumbnail_stats = M_orig
    assert [r.video_id for r in res] == ["v1","v2"]
    assert all(r.rendered and r.set_ok for r in res)
    assert len(rendered) == 2 and len(sets) == 2

def test_run_dry_run_skips_set(tmp_path):
    from sermon_cut.livestream_thumbs import run
    import sermon_cut.livestream_thumbs as M
    M_orig = M.thumbnail_stats; M.thumbnail_stats = lambda b:(120.0,51.0)
    sets = []
    try:
        res = run(enumerate_streams=lambda: iter([("v1","Sunday Morning Class & Worship - July 5, 2026")]),
                  fetch_thumbnail=lambda v: b"AUTO",
                  render=lambda l,p:(Path(p).write_text("x") or Path(p)),
                  set_thumbnail=lambda v,p: sets.append(v),
                  out_dir=tmp_path, dry_run=True)
    finally:
        M.thumbnail_stats = M_orig
    assert res[0].rendered and not res[0].set_ok and sets == []
```

- [ ] **Step 2: Run — expect fail.**

- [ ] **Step 3: Implement (append)**

```python
def run(*, enumerate_streams, fetch_thumbnail, render, set_thumbnail, out_dir,
        dry_run=False, ids=None, limit=None) -> list[Result]:
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    cands = find_candidates(enumerate_streams=enumerate_streams,
                            fetch_thumbnail=fetch_thumbnail)
    if ids:
        want = set(ids)
        cands = [c for c in cands if c.video_id in want]
    if limit:
        cands = cands[:limit]
    results = []
    for c in cands:
        png = out_dir / f"{c.video_id}.png"
        try:
            if not png.exists():
                render(c.label, png)
            rendered = True
        except Exception:
            results.append(Result(c.video_id, c.label, "auto", False, False)); continue
        set_ok = False
        if not dry_run:
            try:
                set_thumbnail(c.video_id, png); set_ok = True
            except Exception:
                set_ok = False
        results.append(Result(c.video_id, c.label, "auto", rendered, set_ok))
    return results
```

- [ ] **Step 4: Run — expect pass.**

- [ ] **Step 5: Commit** — `feat(livestream-thumbs): run() orchestration (render + set, dry-run)`

---

### Task 4: Ink card in `title_card.py`

**Files:** Modify `sermon_cut/title_card.py`; Test `tests/test_title_card.py` (append).

**Interfaces — Produces:** `build_livestream_card(label:str, *, background_path, font_dir, out_path)->Path`.

- [ ] **Step 1: Write failing tests** (append to `tests/test_title_card.py`)

```python
def test_build_livestream_card_writes(tmp_path):
    from sermon_cut.title_card import build_livestream_card
    out = build_livestream_card("July 5th, 2026 Livestream",
            background_path="assets/intro_bg_ink.png", font_dir="assets/fonts",
            out_path=tmp_path / "card.png")
    assert out.exists() and out.stat().st_size > 1000

def test_build_livestream_card_too_long_raises(tmp_path):
    from sermon_cut.title_card import build_livestream_card, TitleTooLongError
    import pytest
    with pytest.raises(TitleTooLongError):
        build_livestream_card("X"*200, background_path="assets/intro_bg_ink.png",
            font_dir="assets/fonts", out_path=tmp_path / "c.png")
```

- [ ] **Step 2: Run — expect fail.** (Requires `assets/intro_bg_ink.png` present — it is.)

- [ ] **Step 3: Implement** — add to `title_card.py` (single-line card at the sermon-title baseline, reusing helpers):

```python
def build_livestream_card(label, *, background_path, font_dir, out_path) -> Path:
    """One-line ink title card ('<date> Livestream') in the sermon-title format
    — no eyebrow. Reuses the same fit/measure/draw helpers. Idempotent."""
    from PIL import Image, ImageDraw
    out_path = Path(out_path)
    background_path = Path(background_path)
    if not background_path.exists():
        raise FileNotFoundError(f"ink background not found: {background_path}")
    lora = Path(font_dir) / "Lora.ttf"

    img = Image.open(background_path).convert("RGB").resize(_CANVAS)
    draw = ImageDraw.Draw(img)
    left = _SIDE_MARGIN
    eyebrow_h = int(_EYEBROW_SIZE * 1.4)
    top = _BLOCK_TOP + eyebrow_h + _META_GAP

    measure_title = _measurer(lora, "Bold")
    avail_h = (_CANVAS[1] - _BOTTOM_MARGIN) - top
    max_em = avail_h / _TITLE_LEADING
    lines, size = fit_and_wrap(label, _USABLE_W, _TITLE_MAX_LINES, max_em,
                               _TITLE_MAX, _TITLE_MIN, measure_title)
    widest = max((measure_title(ln, size)[0] for ln in lines), default=0)
    if (len(lines) > _TITLE_MAX_LINES or widest > _USABLE_W
            or len(lines) * size > max_em):
        raise TitleTooLongError(f"Livestream label {label!r} does not fit the card.")
    font = _load_font(lora, size, "Bold")
    lh = int(size * _TITLE_LEADING)
    block_h = len(lines) * lh
    draw.rectangle([left - _RULE_OFFSET, top, left - _RULE_OFFSET + _RULE_W,
                    top + block_h], fill=_GOLD)
    y = top
    for line in lines:
        draw.text((left, y + lh / 2), line, font=font, fill=_WHITE, anchor="lm")
        y += lh
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    return out_path
```

- [ ] **Step 4: Run — expect pass.** `.venv/bin/python -m pytest tests/test_title_card.py -q`

- [ ] **Step 5: Commit** — `feat(title-card): build_livestream_card (single-line ink card)`

---

### Task 5: CLI subcommand `livestream-thumbs`

**Files:** Modify `sermon_cut/cli.py`; Test `tests/test_cli.py` (append, if a light test fits the existing style).

**Interfaces — Produces:** `python -m sermon_cut livestream-thumbs [--dry-run] [--ids A,B] [--limit N] [--config ...] [--client-secrets ...] [--token ...]`.

- [ ] **Step 1: Add real-dep helpers + subcommand to `cli.py`**

Add near the other imports:
```python
from .livestream_thumbs import run as livestream_run
```

Add helper functions (module level in `cli.py`):
```python
def _ls_enumerate(channel_id, *, runner=subprocess.run):
    url = f"https://www.youtube.com/channel/{channel_id}/streams"
    proc = runner(["yt-dlp", "--flat-playlist", "--dump-json", url],
                  check=True, capture_output=True, text=True)
    for line in proc.stdout.splitlines():
        v = json.loads(line)
        yield v.get("id"), v.get("title", "")

def _ls_fetch_thumbnail(video_id):
    from urllib.request import urlopen, Request
    from urllib.error import HTTPError, URLError
    for name in ("maxresdefault.jpg", "hqdefault.jpg"):
        url = f"https://i.ytimg.com/vi/{video_id}/{name}"
        try:
            with urlopen(Request(url), timeout=20) as r:
                return r.read()
        except (HTTPError, URLError):
            continue
    return None
```

Register the subcommand in `build_parser()`:
```python
    s = sub.add_parser("livestream-thumbs", parents=[common])
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--ids"); s.add_argument("--limit", type=int)
    s.add_argument("--config", default="config.toml")
    s.add_argument("--background", default="assets/intro_bg_ink.png")
    s.add_argument("--font-dir", default="assets/fonts")
    s.add_argument("--client-secrets", default="client_secrets.json")
    s.add_argument("--token", default="token.json")
```

Handle it in `main()`:
```python
    elif args.cmd == "livestream-thumbs":
        from .title_card import build_livestream_card
        cfg = load_config(Path(args.config))
        out_dir = Path("runs/livestream-thumbs")
        def render(label, out_path):
            return build_livestream_card(label, background_path=Path(args.background),
                                         font_dir=Path(args.font_dir), out_path=out_path)
        setter = None
        if not args.dry_run:
            from .youtube_auth import get_authenticated_service
            from googleapiclient.http import MediaFileUpload
            yt = get_authenticated_service(Path(args.client_secrets), Path(args.token))
            def setter(video_id, png):
                yt.thumbnails().set(videoId=video_id,
                    media_body=MediaFileUpload(str(png), mimetype="image/png")).execute()
        results = livestream_run(
            enumerate_streams=lambda: _ls_enumerate(cfg.youtube_channel_id),
            fetch_thumbnail=_ls_fetch_thumbnail,
            render=render, set_thumbnail=setter or (lambda v, p: None),
            out_dir=out_dir, dry_run=args.dry_run,
            ids=args.ids.split(",") if args.ids else None, limit=args.limit)
        for r in results:
            print(f"  {r.video_id}  {r.label:<30}  render={'ok' if r.rendered else 'X'}  "
                  f"set={'ok' if r.set_ok else ('-' if args.dry_run else 'X')}")
        print(f"DONE. {sum(1 for r in results if r.set_ok)}/{len(results)} set"
              f"{' (dry-run)' if args.dry_run else ''}")
```

- [ ] **Step 2: Add a light CLI parser test** (matches existing `test_cli.py` style — parser accepts the subcommand):

```python
def test_livestream_thumbs_parses():
    from sermon_cut.cli import build_parser
    args = build_parser().parse_args(["livestream-thumbs", "--dry-run", "--limit", "2"])
    assert args.cmd == "livestream-thumbs" and args.dry_run and args.limit == 2
```

- [ ] **Step 3: Run — expect pass.** `.venv/bin/python -m pytest tests/test_cli.py -q`

- [ ] **Step 4: Full suite green.** `.venv/bin/python -m pytest -q`

- [ ] **Step 5: Commit** — `feat(cli): livestream-thumbs subcommand (yt-dlp enum + thumbnail set)`

---

### Task 6: `SKILL.md` (use superpowers:writing-skills)

**Files:** Create `.claude/skills/livestream-thumbs/SKILL.md`.

- [ ] **Step 1: Write the skill** per the spec's SKILL.md section — front-matter (name `livestream-thumbs`, trigger description), setup check, `--dry-run` first + eyeball the count, then live run, report. Invoke via `~/projects/sermon-cut/.venv/bin/python -m sermon_cut livestream-thumbs`.
- [ ] **Step 2: Verify** the skill file loads (front-matter valid) and reads clearly.
- [ ] **Step 3: Commit** — `feat(skill): /livestream-thumbs`

---

### Task 7: Live verification + docs

- [ ] **Step 1: Dry-run on the real channel**
Run: `.venv/bin/python -m sermon_cut livestream-thumbs --dry-run`
Expected: candidate list = Sunday streams still on auto frames; the 22 already-carded 2026 Sundays do NOT appear. Spot-check a rendered `runs/livestream-thumbs/<id>.png`.

- [ ] **Step 2: Present the dry-run result to Kyle** (count + a sample card). This is the natural checkpoint before a live back-catalog set; proceed to set per his go-ahead (he runs it and is present).

- [ ] **Step 3: Update `CLAUDE.md`** — add `/livestream-thumbs` to the skill list + module map (`livestream_thumbs.py`, `build_livestream_card`). Commit.

## Self-Review

- Detection/scope/date/label → Task 1; stats + candidate filter → Task 2; render+set+dry-run → Task 3; ink card → Task 4; CLI wiring → Task 5; skill → Task 6; live verify + docs → Task 7. All spec sections covered.
- No placeholders; all code complete.
- Signatures consistent across tasks: `find_candidates(enumerate_streams, fetch_thumbnail)`, `run(..., render, set_thumbnail, out_dir, dry_run, ids, limit)`, `build_livestream_card(label, *, background_path, font_dir, out_path)`, `has_branded_card(brightness, saturation)`.
