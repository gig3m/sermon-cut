# Sermon Shorts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an on-demand `/sermon-shorts <run-id>` feature that turns an already-processed sermon into human-picked, captioned, subject-tracked, branded 9:16 YouTube Shorts uploaded Unlisted.

**Architecture:** New `sermon_cut` modules (`shorts_select`, `shorts_captions`, `shorts_track`, `shorts_render`) plus small additions to `transcribe.py`/`upload.py`/`cli.py`, orchestrated by a new `sermon-shorts` skill. Shorts are sub-clips of the existing `runs/<id>/sermon.mp4` (no new download). Each stage is idempotent and uses the injectable-runner/`model_factory`/`duration_probe` pattern already used across the pipeline.

**Tech Stack:** Python 3.11+, ffmpeg/ffprobe (via injectable `runner`), faster-whisper (word timestamps), Pillow (captions/outro), OpenCV + numpy (tracking), YouTube Data API (existing auth).

## Global Constraints

- **Privacy is always `unlisted`** — no code path may upload Public. (Reuse `upload.py`, which hardcodes `unlisted`.)
- **No new full download** — Shorts are sub-clips of `runs/<id>/sermon.mp4` via `ffmpeg -c copy`.
- **No paid/recurring dependency.** New deps `opencv-python-headless` + `numpy` are free/local.
- **Heavy deps never imported at module top level** — `cv2`, `numpy`, `faster_whisper`, `PIL`, `googleapiclient` are imported lazily inside functions or injected. `pytest` must stay fully offline (no network, GPU, or real binaries).
- **Every stage is idempotent** — skips work when its output artifact already exists.
- **Tests run with only pytest**, using fakes/injected runners. Follow TDD.
- Invoke the CLI as `.venv/bin/python -m sermon_cut <subcommand> --workdir runs/<id>/shorts/<slug>`.

---

### Task 1: `[shorts]` config table

**Files:**
- Modify: `sermon_cut/config.py`
- Modify: `config.example.toml`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `ShortsConfig` dataclass with fields `caption_font_size:int=84`, `cap_y:int=1400`, `amber:tuple=(255,201,60)`, `deadzone:int=95`, `maxjump:int=200`, `max_vel:float=5.0`, `pre_alpha:float=0.20`, `post_alpha:float=0.08`, `min_fg:int=3060`, `crop_w:int=430`, `crop_h:int=764`, `crop_y:int=290`, `fade_up:float=0.4`, `dissolve:float=0.6`, `outro_hold:float=2.4`, `fade_out:float=0.5`, `target_min:int=20`, `target_max:int=90`, `hard_min:int=15`, `hard_max:int=180`. `Config` gains `shorts: ShortsConfig = field(default_factory=ShortsConfig)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py  (add)
from sermon_cut.config import load_config, ShortsConfig

def test_shorts_config_defaults_when_absent(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('church_name="C"\nyoutube_channel_id="Y"\n'
                 'default_speaker="S"\ncanva_template_id="T"\n')
    cfg = load_config(p)
    assert isinstance(cfg.shorts, ShortsConfig)
    assert cfg.shorts.deadzone == 95
    assert cfg.shorts.hard_max == 180

def test_shorts_config_overrides_from_table(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('church_name="C"\nyoutube_channel_id="Y"\n'
                 'default_speaker="S"\ncanva_template_id="T"\n'
                 '[shorts]\ndeadzone=120\nmax_vel=3.5\n')
    cfg = load_config(p)
    assert cfg.shorts.deadzone == 120
    assert cfg.shorts.max_vel == 3.5
    assert cfg.shorts.hard_max == 180  # untouched default
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_config.py -k shorts -v`
Expected: FAIL with `ImportError: cannot import name 'ShortsConfig'`

- [ ] **Step 3: Write minimal implementation**

```python
# sermon_cut/config.py  — add above Config, extend Config + load_config
@dataclass
class ShortsConfig:
    caption_font_size: int = 84
    cap_y: int = 1400
    amber: tuple = (255, 201, 60)
    deadzone: int = 95
    maxjump: int = 200
    max_vel: float = 5.0
    pre_alpha: float = 0.20
    post_alpha: float = 0.08
    min_fg: int = 3060
    crop_w: int = 430
    crop_h: int = 764
    crop_y: int = 290
    fade_up: float = 0.4
    dissolve: float = 0.6
    outro_hold: float = 2.4
    fade_out: float = 0.5
    target_min: int = 20
    target_max: int = 90
    hard_min: int = 15
    hard_max: int = 180
```

Add `shorts: ShortsConfig = field(default_factory=ShortsConfig)` to `Config` (import `field` from dataclasses). In `load_config`, before `Config(**data)`:

```python
    shorts_data = data.pop("shorts", {})
    data["shorts"] = ShortsConfig(**shorts_data)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_config.py -v`
Expected: PASS (all config tests)

- [ ] **Step 5: Add `[shorts]` to `config.example.toml`**

Append a commented `[shorts]` block documenting each field, noting `crop_w/crop_h/crop_y` are **tuned to a fixed camera**.

- [ ] **Step 6: Commit**

```bash
git add sermon_cut/config.py config.example.toml tests/test_config.py
git commit -m "feat: [shorts] config table"
```

---

### Task 2: Word-timestamp transcription

**Files:**
- Modify: `sermon_cut/transcribe.py`
- Test: `tests/test_transcribe.py`

**Interfaces:**
- Produces: `transcribe_words(audio_path: Path, workdir: Path, *, model_name: str = "small", model_factory=None, out_name: str = "words.json") -> Path`. Writes a JSON list of `{"start": float, "end": float, "word": str}`. Idempotent. Passes `word_timestamps=True, vad_filter=True, condition_on_previous_text=False`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_transcribe.py  (add)
import json
from sermon_cut.transcribe import transcribe_words

class FakeWord:
    def __init__(self, start, end, word):
        self.start, self.end, self.word = start, end, word

class FakeWordSegment:
    def __init__(self, words): self.words = words

class FakeWordModel:
    def __init__(self, name): self.name = name; self.kwargs = None
    def transcribe(self, path, **kwargs):
        self.kwargs = kwargs
        segs = [FakeWordSegment([FakeWord(0.0, 0.4, " Where"),
                                 FakeWord(0.4, 0.7, " do")])]
        return iter(segs), {"language": "en"}

def test_transcribe_words_writes_word_json(tmp_path):
    audio = tmp_path / "window.wav"; audio.write_bytes(b"x")
    captured = {}
    def factory(name):
        m = FakeWordModel(name); captured["m"] = m; return m
    out = transcribe_words(audio, tmp_path, model_factory=factory)
    data = json.loads(out.read_text())
    assert data[0] == {"start": 0.0, "end": 0.4, "word": "Where"}
    assert captured["m"].kwargs["word_timestamps"] is True
    assert captured["m"].kwargs["vad_filter"] is True

def test_transcribe_words_idempotent(tmp_path):
    audio = tmp_path / "window.wav"; audio.write_bytes(b"x")
    (tmp_path / "words.json").write_text('[{"start":0,"end":1,"word":"cached"}]')
    def explode(name): raise AssertionError("should not build model")
    out = transcribe_words(audio, tmp_path, model_factory=explode)
    assert json.loads(out.read_text())[0]["word"] == "cached"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_transcribe.py -k words -v`
Expected: FAIL with `ImportError: cannot import name 'transcribe_words'`

- [ ] **Step 3: Write minimal implementation**

```python
# sermon_cut/transcribe.py  (add)
def transcribe_words(audio_path, workdir, *, model_name="small",
                     model_factory=None, out_name="words.json"):
    workdir = Path(workdir)
    out = workdir / out_name
    if out.exists():
        return out
    factory = model_factory or _default_model_factory
    model = factory(model_name)
    seg_iter, _info = model.transcribe(
        str(audio_path), vad_filter=True, condition_on_previous_text=False,
        word_timestamps=True)
    words = []
    for seg in seg_iter:
        for w in (seg.words or []):
            words.append({"start": float(w.start), "end": float(w.end),
                          "word": w.word.strip()})
    out.write_text(json.dumps(words, indent=2))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_transcribe.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add sermon_cut/transcribe.py tests/test_transcribe.py
git commit -m "feat: word-timestamp transcription for shorts"
```

---

### Task 3: Candidate model, validation, time mapping

**Files:**
- Create: `sermon_cut/shorts_select.py`
- Test: `tests/test_shorts_select.py`

**Interfaces:**
- Produces:
  - `@dataclass ShortCandidate(slug, title, source_start, source_end, rationale, standalone_confidence, context_integrity="ok", chosen=False)`
  - `load_candidates(path: Path) -> list[ShortCandidate]`, `save_candidates(cands, path: Path) -> None`
  - `candidate_band(c, cfg) -> str` returning `"IN_BAND"|"WARN_LONG"|"TOO_SHORT"|"TOO_LONG"`
  - `validate_candidate(c, cfg) -> None` (raises `ValueError` if `TOO_SHORT`/`TOO_LONG`)
  - `source_to_clip_time(source_t: float, plan_start: float, front_padding: float) -> float`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_shorts_select.py
import json, pytest
from sermon_cut.config import ShortsConfig
from sermon_cut.shorts_select import (
    ShortCandidate, load_candidates, save_candidates,
    candidate_band, validate_candidate, source_to_clip_time)

def _c(start=0.0, end=60.0):
    return ShortCandidate(slug="hook", title="Hook", source_start=start,
                          source_end=end, rationale="stands alone",
                          standalone_confidence="high")

def test_band_in_and_warn_and_reject():
    cfg = ShortsConfig()
    assert candidate_band(_c(0, 60), cfg) == "IN_BAND"
    assert candidate_band(_c(0, 100), cfg) == "WARN_LONG"
    assert candidate_band(_c(0, 10), cfg) == "TOO_SHORT"
    assert candidate_band(_c(0, 200), cfg) == "TOO_LONG"

def test_validate_raises_out_of_band():
    cfg = ShortsConfig()
    with pytest.raises(ValueError):
        validate_candidate(_c(0, 10), cfg)
    validate_candidate(_c(0, 60), cfg)  # no raise

def test_source_to_clip_time_maps_into_sermon_mp4():
    # sermon.mp4 begins at plan.start - front_padding
    assert source_to_clip_time(3865.0, 3850.0, 1.0) == 3865.0 - (3850.0 - 1.0)

def test_candidates_round_trip(tmp_path):
    p = tmp_path / "candidates.json"
    save_candidates([_c()], p)
    got = load_candidates(p)
    assert got[0].slug == "hook" and got[0].chosen is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_shorts_select.py -v`
Expected: FAIL with `ModuleNotFoundError: sermon_cut.shorts_select`

- [ ] **Step 3: Write minimal implementation**

```python
# sermon_cut/shorts_select.py
from __future__ import annotations
import json
from dataclasses import dataclass, asdict
from pathlib import Path

@dataclass
class ShortCandidate:
    slug: str
    title: str
    source_start: float
    source_end: float
    rationale: str
    standalone_confidence: str
    context_integrity: str = "ok"
    chosen: bool = False

def _dur(c): return c.source_end - c.source_start

def candidate_band(c, cfg) -> str:
    d = _dur(c)
    if d < cfg.hard_min: return "TOO_SHORT"
    if d > cfg.hard_max: return "TOO_LONG"
    if d > cfg.target_max: return "WARN_LONG"
    return "IN_BAND"

def validate_candidate(c, cfg) -> None:
    band = candidate_band(c, cfg)
    if band in ("TOO_SHORT", "TOO_LONG"):
        raise ValueError(f"candidate '{c.slug}' {band}: {_dur(c):.1f}s")

def source_to_clip_time(source_t, plan_start, front_padding) -> float:
    return source_t - (plan_start - front_padding)

def load_candidates(path) -> list[ShortCandidate]:
    data = json.loads(Path(path).read_text())
    return [ShortCandidate(**c) for c in data["candidates"]]

def save_candidates(cands, path) -> None:
    Path(path).write_text(json.dumps({"candidates": [asdict(c) for c in cands]},
                                     indent=2))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_shorts_select.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add sermon_cut/shorts_select.py tests/test_shorts_select.py
git commit -m "feat: shorts candidate model + validation + time mapping"
```

---

### Task 4: Phrase-boundary caption chunking

**Files:**
- Create: `sermon_cut/shorts_captions.py`
- Test: `tests/test_shorts_captions.py`

**Interfaces:**
- Produces: `LEAD: set[str]` and `chunk_words(words: list[dict], *, max_words: int = 4, lead: set[str] | None = None) -> list[list[dict]]`. Each `word` is `{"start","end","word"}`. Breaks before a lead connector once a chunk has ≥2 words, on trailing punctuation, or at `max_words`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_shorts_captions.py
from sermon_cut.shorts_captions import chunk_words

def _w(s): return [{"start": i*0.5, "end": i*0.5+0.4, "word": t}
                   for i, t in enumerate(s.split())]

def test_chunk_breaks_before_lead_word_not_midphrase():
    chunks = chunk_words(_w("sometimes people seek refuge in their work"))
    texts = [" ".join(x["word"] for x in c) for c in chunks]
    # "in" is a lead word -> starts a new chunk; never "seek refuge in"
    assert all("refuge in" not in t for t in texts)
    assert any(t.startswith("in ") or t == "in their work" for t in texts)

def test_chunk_breaks_on_punctuation():
    chunks = chunk_words(_w("where do you go? when life gets hard"))
    first = " ".join(x["word"] for x in chunks[0])
    assert first.endswith("go?")

def test_chunk_caps_at_max_words():
    chunks = chunk_words(_w("one two three four five six"), max_words=4)
    assert all(len(c) <= 4 for c in chunks)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_shorts_captions.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# sermon_cut/shorts_captions.py
from __future__ import annotations

LEAD = {"and", "but", "so", "or", "where", "when", "that", "to", "for", "of",
        "in", "on", "with", "as", "if", "because", "who", "which", "into"}

def chunk_words(words, *, max_words=4, lead=None):
    lead = LEAD if lead is None else lead
    chunks, cur = [], []
    for w in words:
        word = w["word"].strip()
        low = word.lower().strip(".,?!;:")
        if cur and low in lead and len(cur) >= 2:
            chunks.append(cur); cur = []
        cur.append(w)
        if word.endswith((".", "?", "!", ",", ";", ":")) or len(cur) >= max_words:
            chunks.append(cur); cur = []
    if cur:
        chunks.append(cur)
    return chunks
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_shorts_captions.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add sermon_cut/shorts_captions.py tests/test_shorts_captions.py
git commit -m "feat: phrase-boundary caption chunking"
```

---

### Task 5: Caption timeline + overlay render

**Files:**
- Modify: `sermon_cut/shorts_captions.py`
- Test: `tests/test_shorts_captions.py`

**Interfaces:**
- Produces:
  - `build_caption_timeline(chunks, clip_start, dur) -> list[tuple[float, float, tuple[int,int] | None]]` — spans covering `[0, dur]`; state is `(chunk_idx, active_word_idx)` or `None` (blank).
  - `render_overlay(words, workdir, cfg, *, clip_start, dur, font_dir=Path("assets/fonts"), runner=subprocess.run, out_name="overlay.mov") -> Path` — renders per-state scrim PNGs (Pillow, lazy import) and concats to a qtrle alpha `.mov` via `runner`. Idempotent.

- [ ] **Step 1: Write the failing test (timeline is the pure, tested part)**

```python
# tests/test_shorts_captions.py  (add)
from sermon_cut.shorts_captions import build_caption_timeline, chunk_words

def test_timeline_covers_clip_and_marks_active_word():
    words = [{"start": 10.0, "end": 10.4, "word": "Where"},
             {"start": 10.4, "end": 10.8, "word": "do"}]
    chunks = chunk_words(words)
    tl = build_caption_timeline(chunks, clip_start=10.0, dur=2.0)
    assert tl[0][0] == 0.0                      # starts at clip 0
    assert abs(tl[-1][1] - 2.0) < 1e-6          # ends at dur
    active = [s for s in tl if s[2] is not None]
    assert active[0][2] == (0, 0)               # first word of first chunk
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_shorts_captions.py -k timeline -v`
Expected: FAIL `cannot import name 'build_caption_timeline'`

- [ ] **Step 3: Write minimal implementation**

```python
# sermon_cut/shorts_captions.py  (add)
from pathlib import Path
import subprocess

def build_caption_timeline(chunks, clip_start, dur):
    tl, prev_end = [], 0.0
    for ci, ch in enumerate(chunks):
        ch_start = ch[0]["start"] - clip_start
        ch_end = ((chunks[ci+1][0]["start"] - clip_start) if ci+1 < len(chunks)
                  else (ch[-1]["end"] - clip_start + 0.5))
        if ch_start > prev_end + 0.02:
            tl.append((prev_end, ch_start, None))
        for wi, w in enumerate(ch):
            ws = w["start"] - clip_start
            we = ((ch[wi+1]["start"] - clip_start) if wi+1 < len(ch) else ch_end)
            tl.append((max(ws, ch_start), we, (ci, wi)))
        prev_end = ch_end
    if prev_end < dur:
        tl.append((prev_end, dur, None))
    return tl
```

Then `render_overlay` (Pillow lazy inside), reusing Task 4 `chunk_words` + the timeline; render one scrim-card PNG per state (font `Nunito.ttf` with `set_variation_by_name("Bold")`, `cfg.caption_font_size`, `cfg.cap_y`, white text, `cfg.amber` active word, rounded translucent plate `fill=(0,0,0,150)`), write a concat file, and run:

```python
def render_overlay(words, workdir, cfg, *, clip_start, dur,
                   font_dir=Path("assets/fonts"), runner=subprocess.run,
                   out_name="overlay.mov"):
    from PIL import Image, ImageDraw, ImageFont
    workdir = Path(workdir); out = workdir / out_name
    if out.exists():
        return out
    cap_dir = workdir / "caps"; cap_dir.mkdir(parents=True, exist_ok=True)
    W, H = 1080, 1920
    font = ImageFont.truetype(str(font_dir / "Nunito.ttf"), cfg.caption_font_size)
    font.set_variation_by_name("Bold")
    Image.new("RGBA", (W, H), (0, 0, 0, 0)).save(cap_dir / "blank.png")
    chunks = chunk_words(words)
    # (render each (chunk,word) state PNG — scrim card, amber active word;
    #  see experiments/shorts/build4.py render_caption for the exact drawing)
    timeline = build_caption_timeline(chunks, clip_start, dur)
    lines = []
    for s, e, state in timeline:
        png = cap_dir / ("blank.png" if state is None
                         else f"s{state[0]:03d}_{state[1]:02d}.png")
        if state is not None and not png.exists():
            _render_state(png, chunks[state[0]], state[1], font, cfg, W, H)
        lines.append(f"file '{png}'\nduration {max(0.05, e - s):.3f}")
    lines.append(f"file '{timeline[-1] and cap_dir / 'blank.png'}'")
    (cap_dir / "concat.txt").write_text("\n".join(lines))
    runner(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
            "-i", str(cap_dir / "concat.txt"), "-r", "30", "-c:v", "qtrle",
            "-pix_fmt", "argb", str(out)], check=True)
    return out
```

Port `_render_state(png, chunk, active_idx, font, cfg, W, H)` from `experiments/shorts/build4.py:render_caption` (word tokens upper-cased, wrap ≤900px, rounded scrim, `stroke_width=4`, amber active).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_shorts_captions.py -v`
Expected: PASS (timeline test; render is exercised in Task 13 integration)

- [ ] **Step 5: Commit**

```bash
git add sermon_cut/shorts_captions.py tests/test_shorts_captions.py
git commit -m "feat: caption timeline + karaoke overlay render"
```

---

### Task 6: Camera-track math (pure)

**Files:**
- Create: `sermon_cut/shorts_track.py`
- Test: `tests/test_shorts_track.py`

**Interfaces:**
- Produces (pure Python, no numpy/cv2):
  - `ema(values: list[float], alpha: float) -> list[float]`
  - `deadzone_follow(values: list[float], deadzone: float) -> list[float]`
  - `velocity_cap(values: list[float], max_vel: float) -> list[float]`
  - `smooth_camera(measurements: list[float], cfg) -> list[float]` (pre-ema → deadzone → velocity_cap → zero-phase ema)
  - `crop_x_from_center(centers: list[float], crop_w: int, xmax: int) -> list[float]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_shorts_track.py
from sermon_cut.config import ShortsConfig
from sermon_cut.shorts_track import (
    ema, deadzone_follow, velocity_cap, smooth_camera, crop_x_from_center)

def test_deadzone_holds_small_moves():
    out = deadzone_follow([100, 110, 90, 105], deadzone=50)
    assert out == [100, 100, 100, 100]  # all within +/-50 of start

def test_deadzone_eases_on_big_move():
    out = deadzone_follow([100, 300], deadzone=50)
    assert out[1] == 250  # keeps subject at deadzone edge

def test_velocity_cap_limits_step():
    assert velocity_cap([0, 100], max_vel=10)[1] == 10

def test_smooth_camera_reduces_jitter():
    noisy = [100 + (i % 2) * 40 for i in range(20)]  # oscillates 100/140
    sm = smooth_camera(noisy, ShortsConfig())
    assert max(sm) - min(sm) < 40  # jitter compressed

def test_crop_x_clamps_to_bounds():
    assert crop_x_from_center([0, 5000], crop_w=430, xmax=1490) == [0, 1490]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_shorts_track.py -v`
Expected: FAIL `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# sermon_cut/shorts_track.py
from __future__ import annotations

def ema(values, alpha):
    out = list(values)
    for i in range(1, len(out)):
        out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]
    return out

def deadzone_follow(values, deadzone):
    if not values: return []
    out, c = [], values[0]
    for x in values:
        if x > c + deadzone: c = x - deadzone
        elif x < c - deadzone: c = x + deadzone
        out.append(c)
    return out

def velocity_cap(values, max_vel):
    if not values: return []
    out, c = [values[0]], values[0]
    for x in values[1:]:
        dv = max(-max_vel, min(max_vel, x - c))
        c += dv; out.append(c)
    return out

def smooth_camera(measurements, cfg):
    xs = ema(measurements, cfg.pre_alpha)
    xs = deadzone_follow(xs, cfg.deadzone)
    xs = velocity_cap(xs, cfg.max_vel)
    xs = ema(xs, cfg.post_alpha)
    xs = list(reversed(ema(list(reversed(xs)), cfg.post_alpha)))
    return xs

def crop_x_from_center(centers, crop_w, xmax):
    return [max(0.0, min(float(xmax), c - crop_w / 2.0)) for c in centers]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_shorts_track.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add sermon_cut/shorts_track.py tests/test_shorts_track.py
git commit -m "feat: camera-track smoothing math (dead-zone follow)"
```

---

### Task 7: Tracking measure + tracked-body render (cv2)

**Files:**
- Modify: `sermon_cut/shorts_track.py`
- Modify: `pyproject.toml`
- Test: `tests/test_shorts_track.py`

**Interfaces:**
- Produces:
  - `median_column_center(colsum: list[float], rx0: int, scale: float) -> float` (pure; weighted-median column → source-space x)
  - `measure_centers(window_path, clip_start, dur, cfg, *, fps, video_capture=None) -> tuple[list[float], list[float]]` (returns `(times, centers)`; imports `cv2`/`numpy` lazily)
  - `render_tracked_body(window_path, overlay_path, times, crop_x, cfg, *, clip_start, dur, out_path, fps, runner=subprocess.run) -> Path` (OpenCV crop piped to ffmpeg; captions overlaid; audio from window; `cv2` lazy)

- [ ] **Step 1: Write the failing test (pure helper only)**

```python
# tests/test_shorts_track.py  (add)
from sermon_cut.shorts_track import median_column_center

def test_median_column_center_is_robust_to_far_noise():
    # bulk mass at columns 10-12, a tiny far cluster at 100
    cols = [0]*10 + [255, 255, 255] + [0]*87 + [5]
    c = median_column_center(cols, rx0=0, scale=1.0)
    assert 9 <= c <= 13   # median sits in the bulk, not pulled to 100
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_shorts_track.py -k median -v`
Expected: FAIL `cannot import name 'median_column_center'`

- [ ] **Step 3: Write minimal implementation**

```python
# sermon_cut/shorts_track.py  (add)
def median_column_center(colsum, rx0, scale):
    total = sum(colsum)
    if total <= 0:
        return rx0 / scale
    half, run = total / 2.0, 0.0
    for i, v in enumerate(colsum):
        run += v
        if run >= half:
            return (rx0 + i) / scale
    return (rx0 + len(colsum) - 1) / scale
```

Then add `measure_centers` and `render_tracked_body` porting `experiments/shorts/track2.py` (`measure()` → `measure_centers`, using `median_column_center`, `MAXJUMP`/`MIN_FG` from `cfg`, holding last on gate/low-fg; the OpenCV crop-and-pipe loop → `render_tracked_body`). Import `cv2`/`numpy` **inside** these functions. `measure_centers` accepts an injectable `video_capture` factory (default `cv2.VideoCapture`) so it *can* be faked later, but offline tests only cover `median_column_center`.

- [ ] **Step 4: Add deps to `pyproject.toml`**

Add to `[project].dependencies`: `"opencv-python-headless"`, `"numpy"`. Then `.venv/bin/pip install -e .`

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_shorts_track.py -v`
Expected: PASS
Run: `.venv/bin/python -c "import sermon_cut.cli"` (still imports with no cv2 at top level — verify by grep that `import cv2` appears only inside functions)
Expected: no error

- [ ] **Step 6: Commit**

```bash
git add sermon_cut/shorts_track.py pyproject.toml tests/test_shorts_track.py
git commit -m "feat: cv2 subject tracking (measure + tracked-body render)"
```

---

### Task 8: Window extraction + bounds

**Files:**
- Create: `sermon_cut/shorts_render.py`
- Test: `tests/test_shorts_render.py`

**Interfaces:**
- Produces:
  - `window_bounds(clip_start: float, clip_end: float, *, pad: float = 5.0) -> tuple[float, float]` (pure; clamps start ≥ 0)
  - `extract_window(sermon_path, win_start, win_end, workdir, *, out_name="window.mp4", runner=subprocess.run) -> Path` (`ffmpeg -ss .. -to .. -i sermon.mp4 -c copy`; idempotent)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_shorts_render.py
from pathlib import Path
from sermon_cut.shorts_render import window_bounds, extract_window

def test_window_bounds_pads_and_clamps():
    assert window_bounds(20.0, 90.0, pad=5.0) == (15.0, 95.0)
    assert window_bounds(2.0, 40.0, pad=5.0) == (0.0, 45.0)

def test_extract_window_builds_copy_command(tmp_path):
    sermon = tmp_path / "sermon.mp4"; sermon.write_bytes(b"v")
    calls = {}
    def runner(cmd, check): calls["cmd"] = cmd
    out = extract_window(sermon, 15.0, 95.0, tmp_path, runner=runner)
    assert out == tmp_path / "window.mp4"
    assert "-c" in calls["cmd"] and "copy" in calls["cmd"]
    assert str(sermon) in calls["cmd"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_shorts_render.py -v`
Expected: FAIL `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# sermon_cut/shorts_render.py
from __future__ import annotations
import subprocess
from pathlib import Path
from .timefmt import seconds_to_timestamp, clamp_nonnegative

def window_bounds(clip_start, clip_end, *, pad=5.0):
    return (clamp_nonnegative(clip_start - pad), clip_end + pad)

def extract_window(sermon_path, win_start, win_end, workdir, *,
                   out_name="window.mp4", runner=subprocess.run):
    workdir = Path(workdir); workdir.mkdir(parents=True, exist_ok=True)
    out = workdir / out_name
    if out.exists():
        return out
    runner(["ffmpeg", "-y", "-v", "error",
            "-ss", seconds_to_timestamp(win_start),
            "-to", seconds_to_timestamp(win_end),
            "-i", str(sermon_path), "-c", "copy", str(out)], check=True)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_shorts_render.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add sermon_cut/shorts_render.py tests/test_shorts_render.py
git commit -m "feat: shorts window extraction from sermon.mp4"
```

---

### Task 9: Branded assemble (cold-open + outro + fades)

**Files:**
- Modify: `sermon_cut/shorts_render.py`
- Test: `tests/test_shorts_render.py`

**Interfaces:**
- Produces:
  - `build_short_filter(body_dur: float, cfg) -> tuple[str, float]` — returns `(filtergraph, total_dur)`. Both xfade branches normalized `fps=30,format=yuv420p,setsar=1,settb=AVTB`; xfade offset `body_dur - dissolve`; final fade-out at `total - fade_out`; audio `afade` at the dissolve.
  - `assemble_short(body_path, outro_png, workdir, cfg, *, out_name="short.mp4", runner=subprocess.run, duration_probe=_default_probe) -> Path`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_shorts_render.py  (add)
from sermon_cut.config import ShortsConfig
from sermon_cut.shorts_render import build_short_filter

def test_short_filter_normalizes_and_offsets():
    fc, total = build_short_filter(79.0, ShortsConfig())
    assert total == 79.0 + 2.4              # body + outro_hold
    assert "settb=AVTB" in fc and "setsar=1" in fc
    assert "xfade=transition=fade:duration=0.6:offset=78.400" in fc
    assert "fade=t=out:st=80.900:d=0.5" in fc  # (81.4 - 0.5)
    assert "afade=t=out:st=78.400:d=0.6" in fc
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_shorts_render.py -k filter -v`
Expected: FAIL `cannot import name 'build_short_filter'`

- [ ] **Step 3: Write minimal implementation**

```python
# sermon_cut/shorts_render.py  (add)
def build_short_filter(body_dur, cfg):
    xf = body_dur - cfg.dissolve
    total = body_dur + cfg.outro_hold
    fo = total - cfg.fade_out
    norm = "fps=30,format=yuv420p,setsar=1,settb=AVTB"
    fc = (f"[0:v]scale=1080:1920,{norm},fade=t=in:st=0:d={cfg.fade_up}[bd];"
          f"[1:v]scale=1080:1920,{norm}[o];"
          f"[bd][o]xfade=transition=fade:duration={cfg.dissolve}:offset={xf:.3f}[xv];"
          f"[xv]fade=t=out:st={fo:.3f}:d={cfg.fade_out}[v];"
          f"[0:a]afade=t=out:st={xf:.3f}:d={cfg.dissolve},apad[a]")
    return fc, total
```

Add `assemble_short` (import `_default_probe` from `.assemble`), probing `body_dur`, building the filter, running ffmpeg with inputs `body` + `-loop 1 -t {outro_hold+dissolve} outro_png`, `-map [v] -map [a] -t {total}`; idempotent.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_shorts_render.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add sermon_cut/shorts_render.py tests/test_shorts_render.py
git commit -m "feat: branded short assemble (cold-open + outro + fades)"
```

---

### Task 10: 9:16 outro card

**Files:**
- Modify: `sermon_cut/shorts_render.py`
- Test: `tests/test_shorts_render.py`

**Interfaces:**
- Produces: `build_vertical_outro_card(src_card_path, workdir, *, out_name="outro_9x16.png", width=1080, height=1920) -> Path` — seamless scale of the 16:9 card onto a near-black 1080×1920 canvas (bg sampled from the source). Optional override: if `workdir.parent / "outro_9x16_override.png"` exists, copy it. Idempotent. PIL lazy.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_shorts_render.py  (add)
from sermon_cut.shorts_render import build_vertical_outro_card

def test_vertical_outro_card_is_1080x1920(tmp_path):
    from PIL import Image
    src = tmp_path / "outro_card.png"
    Image.new("RGB", (1920, 1080), (17, 17, 19)).save(src)
    out = build_vertical_outro_card(src, tmp_path)
    im = Image.open(out)
    assert im.size == (1080, 1920)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_shorts_render.py -k outro -v`
Expected: FAIL `cannot import name 'build_vertical_outro_card'`

- [ ] **Step 3: Write minimal implementation**

```python
# sermon_cut/shorts_render.py  (add)
def build_vertical_outro_card(src_card_path, workdir, *, out_name="outro_9x16.png",
                              width=1080, height=1920):
    from PIL import Image
    workdir = Path(workdir); out = workdir / out_name
    if out.exists():
        return out
    src = Image.open(src_card_path).convert("RGB")
    bg = src.getpixel((5, 5))
    canvas = Image.new("RGB", (width, height), bg)
    scaled = src.resize((width, int(width * src.height / src.width)))
    canvas.paste(scaled, (0, (height - scaled.height) // 2))
    canvas.save(out)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_shorts_render.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add sermon_cut/shorts_render.py tests/test_shorts_render.py
git commit -m "feat: 9:16 outro card builder"
```

---

### Task 11: Shorts upload metadata + full-sermon link

**Files:**
- Modify: `sermon_cut/upload.py`
- Test: `tests/test_upload.py`

**Interfaces:**
- Produces:
  - `format_shorts_title(candidate_title: str, sermon_title: str) -> str` → `"<candidate> — <sermon>"`
  - `build_shorts_description(candidate_title, rationale, footer, *, sermon_url: str | None = None) -> str` — includes `#Shorts`, a one-line blurb, `Full sermon: <url>` when present, then footer.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_upload.py  (add)
from sermon_cut.upload import format_shorts_title, build_shorts_description

def test_shorts_title_joins_candidate_and_sermon():
    assert format_shorts_title("Where Do You Run?", "Cities of Refuge") == \
        "Where Do You Run? — Cities of Refuge"

def test_shorts_description_has_hashtag_and_optional_link():
    d = build_shorts_description("Where Do You Run?", "A hook.", "Footer.",
                                 sermon_url="https://youtu.be/abc")
    assert "#Shorts" in d
    assert "Full sermon: https://youtu.be/abc" in d
    assert "Footer." in d
    d2 = build_shorts_description("t", "r", "", sermon_url=None)
    assert "Full sermon:" not in d2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_upload.py -k shorts -v`
Expected: FAIL `cannot import name 'format_shorts_title'`

- [ ] **Step 3: Write minimal implementation**

```python
# sermon_cut/upload.py  (add)
def format_shorts_title(candidate_title, sermon_title):
    return f"{candidate_title} — {sermon_title}"

def build_shorts_description(candidate_title, rationale, footer, *, sermon_url=None):
    parts = [rationale.strip(), "#Shorts"]
    if sermon_url:
        parts.insert(1, f"Full sermon: {sermon_url}")
    if footer.strip():
        parts.append(footer.strip())
    return "\n\n".join(p for p in parts if p)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_upload.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add sermon_cut/upload.py tests/test_upload.py
git commit -m "feat: shorts upload metadata builder"
```

---

### Task 12: Upload receipt (`upload.json`)

**Files:**
- Modify: `sermon_cut/upload.py`
- Modify: `sermon_cut/cli.py` (sermon `upload` writes receipt)
- Test: `tests/test_upload.py`

**Interfaces:**
- Produces:
  - `write_upload_receipt(workdir: Path, video_id: str, url: str) -> Path` (writes `upload.json`)
  - `load_upload_receipt(workdir: Path) -> dict | None`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_upload.py  (add)
from sermon_cut.upload import write_upload_receipt, load_upload_receipt

def test_upload_receipt_round_trip(tmp_path):
    assert load_upload_receipt(tmp_path) is None
    write_upload_receipt(tmp_path, "vid123", "https://youtu.be/vid123")
    got = load_upload_receipt(tmp_path)
    assert got["video_id"] == "vid123"
    assert got["url"] == "https://youtu.be/vid123"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_upload.py -k receipt -v`
Expected: FAIL `cannot import name 'write_upload_receipt'`

- [ ] **Step 3: Write minimal implementation**

```python
# sermon_cut/upload.py  (add)
import json
def write_upload_receipt(workdir, video_id, url):
    p = Path(workdir) / "upload.json"
    p.write_text(json.dumps({"video_id": video_id, "url": url}, indent=2))
    return p

def load_upload_receipt(workdir):
    p = Path(workdir) / "upload.json"
    return json.loads(p.read_text()) if p.exists() else None
```

In `cli.py` sermon `upload` branch, after `url = upload_video(...)`, add
`write_upload_receipt(workdir, url.rsplit("/", 1)[-1], url)` and print the url.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_upload.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add sermon_cut/upload.py sermon_cut/cli.py tests/test_upload.py
git commit -m "feat: persist upload.json receipt on sermon upload"
```

---

### Task 13: CLI subcommands (`shorts-validate` / `shorts-clip` / `shorts-render` / `shorts-upload`)

**Files:**
- Modify: `sermon_cut/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: all functions from Tasks 2–12.
- Produces CLI stages:
  - `shorts-validate --candidates <path> --config <cfg>` → prints per-candidate band.
  - `shorts-clip --sermon <path> --plan <path> --candidate <slug> --candidates <path> --config <cfg>` → extract window (uses `source_to_clip_time` + `window_bounds`).
  - `shorts-render --plan <path> --candidate <slug> --candidates <path> --config <cfg> [--outro-card assets/outro_card.png]` → words re-transcribe + overlay + track + tracked body + vertical outro card + `assemble_short` → `short.mp4`.
  - `shorts-upload --video <short.mp4> --candidate <slug> --candidates <path> --plan <path> --sermon-run <run-dir> --config <cfg> ...` → metadata (with `load_upload_receipt(sermon_run)` link) + `upload_video` unlisted.

- [ ] **Step 1: Write the failing test (parser + one dispatch)**

```python
# tests/test_cli.py  (add)
import json
import sermon_cut.cli as cli

def _cands(tmp_path):
    p = tmp_path / "candidates.json"
    p.write_text(json.dumps({"candidates": [{
        "slug": "hook", "title": "Hook", "source_start": 3865.0,
        "source_end": 3925.0, "rationale": "r",
        "standalone_confidence": "high", "context_integrity": "ok",
        "chosen": True}]}))
    return p

def test_shorts_validate_reports_band(tmp_path, capsys):
    cands = _cands(tmp_path)
    cfg = tmp_path / "config.toml"
    cfg.write_text('church_name="C"\nyoutube_channel_id="Y"\n'
                   'default_speaker="S"\ncanva_template_id="T"\n')
    rc = cli.main(["shorts-validate", "--candidates", str(cands),
                   "--config", str(cfg)])
    out = capsys.readouterr().out
    assert rc == 0 and "hook" in out and "IN_BAND" in out

def test_shorts_clip_dispatches_extract(tmp_path, monkeypatch):
    cands = _cands(tmp_path)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"start": 3850.0, "end": 5610.0, "title": "Cities",
        "speaker": "Clay", "date": "2026-05-17", "description": "d", "chapters": []}))
    sermon = tmp_path / "sermon.mp4"; sermon.write_bytes(b"v")
    cfg = tmp_path / "config.toml"
    cfg.write_text('church_name="C"\nyoutube_channel_id="Y"\n'
                   'default_speaker="S"\ncanva_template_id="T"\n')
    calls = {}
    def fake_extract(sermon_path, ws, we, workdir, **kw):
        calls["bounds"] = (round(ws, 1), round(we, 1)); return workdir / "window.mp4"
    monkeypatch.setattr(cli, "extract_window", fake_extract)
    rc = cli.main(["shorts-clip", "--sermon", str(sermon), "--plan", str(plan),
                   "--candidate", "hook", "--candidates", str(cands),
                   "--config", str(cfg), "--workdir", str(tmp_path)])
    # clip time = 3865 - (3850 - 1) = 16.0 ; window pad 5 -> 11.0 .. (3925-3849)+5=81.0
    assert rc == 0 and calls["bounds"] == (11.0, 81.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cli.py -k shorts -v`
Expected: FAIL (unknown subcommand / `extract_window` not on `cli`)

- [ ] **Step 3: Write minimal implementation**

Import the new functions into `cli.py` (`extract_window`, `window_bounds`, `build_vertical_outro_card`, `assemble_short`, `render_overlay`, `measure_centers`, `render_tracked_body`, `smooth_camera`, `crop_x_from_center`, `transcribe_words`, candidate + config helpers, upload builders). Add the four subparsers to `build_parser` and dispatch branches to `main`. `shorts-clip` computes:

```python
    clip_start = source_to_clip_time(cand.source_start, plan.start, cfg.front_padding_seconds)
    clip_end = source_to_clip_time(cand.source_end, plan.start, cfg.front_padding_seconds)
    ws, we = window_bounds(clip_start, clip_end)
    print(extract_window(Path(args.sermon), ws, we, workdir))
```

`shorts-render` orchestrates words → overlay → measure/smooth/crop → tracked body → vertical outro → `assemble_short`. `shorts-upload` builds metadata (`format_shorts_title`, `build_shorts_description` with `load_upload_receipt(args.sermon_run)`), then `upload_video(..., youtube_client=get_authenticated_service(...))`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_cli.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add sermon_cut/cli.py tests/test_cli.py
git commit -m "feat: shorts CLI stages (validate/clip/render/upload)"
```

---

### Task 14: `sermon-shorts` orchestration skill

**Files:**
- Create: `.claude/skills/sermon-shorts/SKILL.md`

**Interfaces:** No tests (skill doc). Mirrors `.claude/skills/sermon-cut/SKILL.md` structure.

- [ ] **Step 1: Write the skill doc**

Write `SKILL.md` describing: prerequisite (`runs/<id>/sermon.mp4` + `plan.json` exist); **Step A Select** — read `transcript.json`, author `runs/<id>/shorts/candidates.json` (~3–5 candidates, each with slug/title/source_start/source_end/rationale/standalone_confidence/context_integrity), applying the "lands standalone" criteria + context-integrity flag from the spec; **Review gate #1** — present the shortlist (title, `HH:MM:SS`, rationale, confidence, any flag) and set `chosen: true` on the user's picks (never auto-skipped, even with `--yes`); **Per chosen candidate** — run `shorts-validate`, then `shorts-clip`, then re-transcribe words (`transcribe --audio window.mp4`? no — a `shorts-render` internally does words), then `shorts-render`, then `shorts-upload`; **Review gate #2** — report each `https://youtu.be/...`, remind it's Unlisted. Include the invariant reminders (unlisted-only; sub-clips of `sermon.mp4`).

- [ ] **Step 2: Manual verification**

Run: `.venv/bin/python -m pytest -q` (full suite still green)
Expected: all pass. Confirm `SKILL.md` renders and references only real subcommands.

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/sermon-shorts/SKILL.md
git commit -m "feat: sermon-shorts orchestration skill"
```

---

### Task 15: End-to-end smoke on a real run + docs

**Files:**
- Modify: `CLAUDE.md` (add Shorts to architecture/status)
- Modify: `README.md` if setup steps change (new deps)

**Interfaces:** No new code; validates the whole path on `runs/CahMfXJcFCE`.

- [ ] **Step 1: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass (offline).

- [ ] **Step 2: Real smoke (manual, one candidate)**

With `runs/CahMfXJcFCE/sermon.mp4` + `plan.json` present, hand-author a one-candidate `candidates.json` (the opening hook, ~15–95s) and run `shorts-clip` → `shorts-render`. `ffprobe` the resulting `short.mp4` for a valid moov + ~expected duration; extract an opening frame to confirm it opens on the preacher with captions. (Do NOT upload in the smoke.)

- [ ] **Step 3: Update `CLAUDE.md`**

Add a Shorts subsection to Architecture (on-demand `/sermon-shorts`, sub-clip of `sermon.mp4`, cv2/numpy deps, unlisted) and move the item out of "deferred" into "shipped." Note the crop geometry is camera-specific.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md README.md
git commit -m "docs: document sermon Shorts feature"
```

---

## Self-Review

**Spec coverage:** trigger/workflow → Tasks 13/14; selection model + band → Task 3; candidate judgment/gates → Task 14 (skill); window from sermon.mp4 → Task 8; word timestamps → Task 2; captions (chunking/timeline/render) → Tasks 4/5; tracking (math + cv2) → Tasks 6/7; branded assemble + cold-open/outro/fades → Task 9; 9:16 outro card → Task 10; upload metadata + #Shorts + full-sermon link + receipt → Tasks 11/12; deps → Task 7; config → Task 1; tests → every task. All spec sections covered.

**Placeholder scan:** `render_overlay._render_state` and the `measure_centers`/`render_tracked_body` cv2 bodies point to exact spike sources (`experiments/shorts/build4.py`, `track2.py`) to port verbatim — not vague TODOs. No "add error handling"/"TBD" left.

**Type consistency:** `ShortsConfig` field names used identically across Tasks 1/5/6/7/9; `ShortCandidate` fields consistent across Tasks 3/13/14; `smooth_camera`/`crop_x_from_center`/`measure_centers` signatures match between Tasks 6/7/13; `build_short_filter` return `(str, float)` matches Task 13 usage.
