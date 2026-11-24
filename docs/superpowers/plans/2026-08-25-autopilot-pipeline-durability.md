# Autopilot Pipeline-Durability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the hand-run render workflow (full-download → drift-pin → cut → chapter-repin) and the Shorts-moment selection into tested, reusable Python, so autonomous processing can match interactive quality.

**Architecture:** New additive modules under `sermon_cut/auto/`: `drift.py` (pure word/segment-transcript matching — no heavy deps), `render_robust.py` (yt-dlp/ffmpeg wrappers via an injectable `runner`, plus an integrated `render_sermon` flow that composes them with an injectable `transcriber`), and a `select_short_moments` function added to the existing `shorts_select.py` (a headless judge wrapper). Everything is offline-testable with fakes.

**Tech Stack:** Python 3.12, stdlib only in new modules (subprocess/json/re/dataclasses); pytest offline. yt-dlp + ffmpeg invoked via injected runner; faster-whisper via the existing injected transcriber.

**Spec:** `docs/superpowers/specs/2026-08-25-autopilot-webui-design.md` (Phase 1).

## Global Constraints

- Privacy is always `unlisted`; no code path uploads Public (copied from spec).
- Discovery is audio-only; full-video download happens only in the render step (copied from spec).
- Heavy deps (faster_whisper, googleapiclient, PIL, cv2, numpy) and binaries (yt-dlp, ffmpeg) are NEVER imported at module top-level — invoked via injected runner/transcriber (copied from spec).
- Tests run with only pytest, fully offline — no network, GPU, or real binaries (copied from spec).
- New code is additive under `sermon_cut/auto/`; existing modules' behavior is unchanged.
- Every stage is idempotent-by-artifact: if the output file exists, return it without re-running (matches the existing `clip_video` pattern).

---

## File Structure

- Create `sermon_cut/auto/drift.py` — pure functions that locate frame-accurate boundaries and chapter times in word/segment transcripts. No I/O, no heavy deps.
- Create `sermon_cut/auto/render_robust.py` — mechanical yt-dlp/ffmpeg wrappers (injectable `runner`) + the integrated `render_sermon` and `repin_chapters` flows (injectable `transcriber`).
- Modify `sermon_cut/shorts_select.py` — add `select_short_moments` (headless selection via an injectable `judge`, validated against the existing `ShortCandidate`/band helpers).
- Create `tests/auto/test_drift.py`, `tests/auto/test_render_robust.py`; add to `tests/test_shorts_select.py` (or create `tests/auto/test_shorts_select_headless.py`).

Note: `tests/auto/__init__.py` already exists (from the autopilot-core work).

---

## Task 1: Drift-pin & chapter-pin pure logic (`drift.py`)

**Files:**
- Create: `sermon_cut/auto/drift.py`
- Test: `tests/auto/test_drift.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `normalize(text: str) -> list[str]` — lowercase, strip to `[a-z0-9 ]`, split.
  - `find_word_time(words: list[dict], phrase: str, near: float, edge: str) -> float | None` — `words` are `{"word","start","end"}` in absolute seconds; find the contiguous run matching `phrase`'s tokens whose start is nearest `near`; return the first word's `start` (edge=`"start"`) or last word's `end` (edge=`"end"`); `None` if no match.
  - `pin_boundaries(start_words, end_words, *, in_phrase, out_phrase, rough_start, rough_end) -> tuple[float, float]` — returns frame-accurate `(start, end)`, falling back to the rough value when a phrase isn't found.
  - `pin_chapters(segments: list[dict], topics: list[dict], phys_start: float) -> list[dict]` — `segments` are `{"text","start"}` (clip-relative); `topics` are `{"phrase","label"}`; returns ordered, de-duplicated `[{"time": int, "label": str}]` where `time = round(phys_start + segment.start)` for the earliest segment whose normalized text contains the normalized phrase.

- [ ] **Step 1: Write the failing tests**

```python
# tests/auto/test_drift.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/auto/test_drift.py -q`
Expected: FAIL (`ModuleNotFoundError: sermon_cut.auto.drift`)

- [ ] **Step 3: Write the implementation**

```python
# sermon_cut/auto/drift.py
from __future__ import annotations

import re


def normalize(text: str) -> list[str]:
    return re.sub(r"[^a-z0-9 ]", " ", text.lower()).split()


def find_word_time(words: list[dict], phrase: str, near: float, edge: str) -> float | None:
    """Find the contiguous run of `words` whose normalized tokens equal the
    normalized `phrase`, choosing the occurrence whose first word starts nearest
    `near`. Return the first word's start (edge='start') or the last word's end
    (edge='end'); None if no occurrence matches."""
    tokens = [normalize(w["word"])[0] if normalize(w["word"]) else "" for w in words]
    target = normalize(phrase)
    n = len(target)
    if n == 0:
        return None
    best_i, best_dist = None, None
    for i in range(0, len(tokens) - n + 1):
        if tokens[i:i + n] == target:
            dist = abs(words[i]["start"] - near)
            if best_dist is None or dist < best_dist:
                best_i, best_dist = i, dist
    if best_i is None:
        return None
    return words[best_i]["start"] if edge == "start" else words[best_i + n - 1]["end"]


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/auto/test_drift.py -q`
Expected: PASS (9 passed)

- [ ] **Step 5: Commit**

```bash
git add sermon_cut/auto/drift.py tests/auto/test_drift.py
git commit -m "feat(auto): drift-pin & chapter-pin pure logic"
```

---

## Task 2: Render mechanical wrappers (`render_robust.py`)

**Files:**
- Create: `sermon_cut/auto/render_robust.py`
- Test: `tests/auto/test_render_robust.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces (each idempotent-by-artifact; `runner` defaults to `subprocess.run`):
  - `full_download(url: str, out_path, *, fmt: str = "bestvideo[height<=1080][ext=mp4]/bestvideo[height<=1080]", runner=subprocess.run) -> Path`
  - `mux_av(video_path, audio_path, out_path, *, runner=subprocess.run) -> Path`
  - `cut_window(av_path, start: float, dur: float, out_path, *, fps: int = 30, crf: int = 18, runner=subprocess.run) -> Path`
  - `extract_probe(src, start: float, dur: float, out_wav, *, runner=subprocess.run) -> Path`

- [ ] **Step 1: Write the failing tests**

```python
# tests/auto/test_render_robust.py
from pathlib import Path
from sermon_cut.auto import render_robust as rr


class Rec:
    """Fake runner: records commands and creates the -o/output file so idempotency
    and downstream existence checks behave like the real thing."""
    def __init__(self): self.cmds = []
    def __call__(self, cmd, check=None, **kw):
        self.cmds.append([str(c) for c in cmd])
        out = None
        if "-o" in cmd:
            out = cmd[cmd.index("-o") + 1]
        else:
            out = cmd[-1]  # ffmpeg: output is last arg
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(b"x")
        return type("P", (), {"returncode": 0})()


def test_full_download_builds_ytdlp_command(tmp_path):
    r = Rec()
    out = rr.full_download("https://youtu.be/x", tmp_path / "full.mp4", runner=r)
    assert out == tmp_path / "full.mp4" and out.exists()
    assert r.cmds[0][0] == "yt-dlp"
    assert "-f" in r.cmds[0] and "https://youtu.be/x" in r.cmds[0]


def test_full_download_idempotent(tmp_path):
    (tmp_path / "full.mp4").write_bytes(b"already")
    r = Rec()
    rr.full_download("https://youtu.be/x", tmp_path / "full.mp4", runner=r)
    assert r.cmds == []  # skipped


def test_mux_av_maps_video_and_audio_copy(tmp_path):
    r = Rec()
    rr.mux_av(tmp_path / "v.mp4", tmp_path / "a.m4a", tmp_path / "av.mp4", runner=r)
    cmd = r.cmds[0]
    assert cmd[0] == "ffmpeg"
    assert "-map" in cmd and "0:v:0" in cmd and "1:a:0" in cmd
    assert "-c" in cmd and "copy" in cmd


def test_cut_window_frame_accurate_reencode(tmp_path):
    r = Rec()
    rr.cut_window(tmp_path / "av.mp4", 3816.0, 1800.0, tmp_path / "sermon.mp4",
                  fps=30, runner=r)
    cmd = r.cmds[0]
    assert cmd[0] == "ffmpeg"
    assert cmd[cmd.index("-ss") + 1] == "3816.0"
    assert cmd[cmd.index("-t") + 1] == "1800.0"
    assert "fps=30" in " ".join(cmd)
    assert "libx264" in cmd


def test_extract_probe_mono_16k_wav(tmp_path):
    r = Rec()
    rr.extract_probe(tmp_path / "a.m4a", 100.0, 90.0, tmp_path / "p.wav", runner=r)
    cmd = r.cmds[0]
    assert cmd[cmd.index("-ar") + 1] == "16000"
    assert cmd[cmd.index("-ac") + 1] == "1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/auto/test_render_robust.py -q`
Expected: FAIL (`ModuleNotFoundError: sermon_cut.auto.render_robust`)

- [ ] **Step 3: Write the implementation**

```python
# sermon_cut/auto/render_robust.py
from __future__ import annotations

import subprocess
from pathlib import Path

_FMT = "bestvideo[height<=1080][ext=mp4]/bestvideo[height<=1080]"


def full_download(url: str, out_path, *, fmt: str = _FMT, runner=subprocess.run) -> Path:
    out = Path(out_path)
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    runner(["yt-dlp", "-f", fmt, "-o", str(out), "--no-progress", url], check=True)
    return out


def mux_av(video_path, audio_path, out_path, *, runner=subprocess.run) -> Path:
    out = Path(out_path)
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    runner(["ffmpeg", "-y", "-i", str(video_path), "-i", str(audio_path),
            "-map", "0:v:0", "-map", "1:a:0", "-c", "copy", "-shortest", str(out)],
           check=True)
    return out


def cut_window(av_path, start: float, dur: float, out_path, *,
               fps: int = 30, crf: int = 18, runner=subprocess.run) -> Path:
    out = Path(out_path)
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    runner(["ffmpeg", "-y", "-ss", str(start), "-i", str(av_path), "-t", str(dur),
            "-vf", f"fps={fps}", "-c:v", "libx264", "-crf", str(crf),
            "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)],
           check=True)
    return out


def extract_probe(src, start: float, dur: float, out_wav, *, runner=subprocess.run) -> Path:
    out = Path(out_wav)
    out.parent.mkdir(parents=True, exist_ok=True)
    runner(["ffmpeg", "-y", "-ss", str(start), "-i", str(src), "-t", str(dur),
            "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(out)],
           check=True)
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/auto/test_render_robust.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add sermon_cut/auto/render_robust.py tests/auto/test_render_robust.py
git commit -m "feat(auto): robust render mechanical wrappers (yt-dlp/ffmpeg)"
```

---

## Task 3: Integrated render_sermon + repin_chapters flow

**Files:**
- Modify: `sermon_cut/auto/render_robust.py` (append the integrated flow + a dataclass)
- Test: `tests/auto/test_render_robust.py` (add flow tests)

**Interfaces:**
- Consumes: `full_download`/`mux_av`/`cut_window`/`extract_probe` (Task 2); `drift.pin_boundaries`/`drift.pin_chapters` (Task 1); an injected `transcriber` with the signature of `sermon_cut.transcribe.transcribe_words(audio_path, workdir, *, model_name=..., out_name=...) -> Path` writing a JSON list of `{"word","start","end"}`, and `sermon_cut.transcribe.transcribe(...)` writing `{"segments":[{"text","start"}]}` or a bare list.
- Produces:
  - `@dataclass RenderResult(sermon_path: Path, phys_start: float, sermon_start: float, sermon_end: float)`
  - `render_sermon(url, audio_path, workdir, *, in_phrase, out_phrase, rough_start, rough_end, front_lead=1.0, probe_pad=25.0, probe_len=110.0, runner=subprocess.run, word_transcriber=None) -> RenderResult` — full-download → mux → probe both boundaries → `word_transcriber` each probe → `drift.pin_boundaries` → frame-accurate cut from `phys_start = start - front_lead`. `sermon.mp4` is idempotent.
  - `repin_chapters(sermon_path, topics, phys_start, workdir, *, segment_transcriber=None) -> list[dict]` — re-transcribe the cut, `drift.pin_chapters`.

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/auto/test_render_robust.py
import json
from sermon_cut.auto import render_robust as rr


def _make_word_transcriber(mapping):
    """mapping: out_name -> list[{"word","start","end"}] (probe-relative seconds)."""
    def fake(audio_path, workdir, *, model_name="small", out_name="words.json"):
        from pathlib import Path
        p = Path(workdir) / out_name
        p.write_text(json.dumps(mapping[out_name]))
        return p
    return fake


def test_render_sermon_pins_boundaries_and_cuts(tmp_path):
    r = Rec()
    # start probe (base = rough_start - probe_pad = 3800-25 = 3775): greeting at rel 20 -> 3795
    start_words = [{"word": "reading", "start": 5.0, "end": 5.4},
                   {"word": "good", "start": 20.0, "end": 20.4},
                   {"word": "morning", "start": 20.4, "end": 20.9}]
    # end probe (base = rough_end - probe_pad = 5600-25 = 5575): amen at rel 30 -> 5605
    end_words = [{"word": "name", "start": 29.0, "end": 29.5},
                 {"word": "amen", "start": 30.0, "end": 30.6}]
    wt = _make_word_transcriber({"sp_words.json": start_words, "ep_words.json": end_words})
    res = rr.render_sermon(
        "https://youtu.be/x", tmp_path / "audio.m4a", tmp_path,
        in_phrase="good morning", out_phrase="amen",
        rough_start=3800.0, rough_end=5600.0, front_lead=1.0,
        probe_pad=25.0, runner=r, word_transcriber=wt)
    assert abs(res.sermon_start - 3795.0) < 0.01   # 3775 + 20.0
    assert abs(res.sermon_end - 5605.6) < 0.01     # 5575 + 30.6 (amen end)
    assert abs(res.phys_start - 3794.0) < 0.01     # start - front_lead
    # the cut command used phys_start and dur = sermon_end - phys_start
    cut = [c for c in r.cmds if c[0] == "ffmpeg" and "-vf" in c][0]
    assert cut[cut.index("-ss") + 1] == "3794.0"
    assert abs(float(cut[cut.index("-t") + 1]) - (5605.6 - 3794.0)) < 0.01
    assert res.sermon_path == tmp_path / "sermon.mp4"


def test_render_sermon_idempotent_when_sermon_exists(tmp_path):
    (tmp_path / "sermon.mp4").write_bytes(b"done")
    r = Rec()
    # even with a transcriber that would KeyError, no work happens
    res = rr.render_sermon(
        "https://youtu.be/x", tmp_path / "audio.m4a", tmp_path,
        in_phrase="a", out_phrase="b", rough_start=1.0, rough_end=2.0,
        runner=r, word_transcriber=lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    assert res.sermon_path.exists()
    assert not any("-vf" in c for c in r.cmds)  # no cut ran


def test_repin_chapters_uses_segment_transcriber(tmp_path):
    (tmp_path / "sermon.mp4").write_bytes(b"x")
    def seg_fake(audio_path, workdir, *, model_name="small", out_name="chap.json"):
        from pathlib import Path
        segs = {"segments": [{"text": "the first point today", "start": 40.0},
                             {"text": "let us pray now", "start": 300.0}]}
        p = Path(workdir) / out_name; p.write_text(json.dumps(segs)); return p
    topics = [{"phrase": "first point", "label": "First point"},
              {"phrase": "let us pray", "label": "Closing prayer"}]
    out = rr.repin_chapters(tmp_path / "sermon.mp4", topics, phys_start=1000.0,
                            workdir=tmp_path, segment_transcriber=seg_fake)
    assert out == [{"time": 1040, "label": "First point"},
                   {"time": 1300, "label": "Closing prayer"}]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/auto/test_render_robust.py -q`
Expected: FAIL (`AttributeError: module 'sermon_cut.auto.render_robust' has no attribute 'render_sermon'`)

- [ ] **Step 3: Append the implementation**

```python
# append to sermon_cut/auto/render_robust.py
import json
from dataclasses import dataclass

from . import drift


@dataclass
class RenderResult:
    sermon_path: Path
    phys_start: float
    sermon_start: float
    sermon_end: float


def _load_words(path, base: float) -> list[dict]:
    raw = json.loads(Path(path).read_text())
    return [{"word": w["word"], "start": w["start"] + base, "end": w["end"] + base}
            for w in raw]


def render_sermon(url, audio_path, workdir, *, in_phrase, out_phrase,
                  rough_start, rough_end, front_lead: float = 1.0,
                  probe_pad: float = 25.0, probe_len: float = 110.0,
                  runner=subprocess.run, word_transcriber=None) -> RenderResult:
    workdir = Path(workdir)
    sermon = workdir / "sermon.mp4"
    if word_transcriber is None:
        from ..transcribe import transcribe_words as word_transcriber

    if not sermon.exists():
        full = full_download(url, workdir / "full.mp4", runner=runner)
        av = mux_av(full, audio_path, workdir / "full_av.mp4", runner=runner)
        # probe windows (clamped at 0) around the rough boundaries
        sbase = max(0.0, rough_start - probe_pad)
        ebase = max(0.0, rough_end - probe_pad)
        extract_probe(audio_path, sbase, probe_len, workdir / "sp.wav", runner=runner)
        extract_probe(audio_path, ebase, probe_len, workdir / "ep.wav", runner=runner)
        sw = _load_words(word_transcriber(workdir / "sp.wav", workdir, out_name="sp_words.json"), sbase)
        ew = _load_words(word_transcriber(workdir / "ep.wav", workdir, out_name="ep_words.json"), ebase)
        start, end = drift.pin_boundaries(sw, ew, in_phrase=in_phrase, out_phrase=out_phrase,
                                          rough_start=rough_start, rough_end=rough_end)
        phys_start = max(0.0, start - front_lead)
        cut_window(av, phys_start, end - phys_start, sermon, runner=runner)
        (workdir / "_render.json").write_text(json.dumps(
            {"phys_start": phys_start, "sermon_start": start, "sermon_end": end}))

    meta = json.loads((workdir / "_render.json").read_text())
    return RenderResult(sermon, meta["phys_start"], meta["sermon_start"], meta["sermon_end"])


def repin_chapters(sermon_path, topics, phys_start, workdir, *, segment_transcriber=None) -> list[dict]:
    workdir = Path(workdir)
    if segment_transcriber is None:
        from ..transcribe import transcribe as segment_transcriber
    p = segment_transcriber(sermon_path, workdir, out_name="chap.json")
    data = json.loads(Path(p).read_text())
    segments = data["segments"] if isinstance(data, dict) else data
    return drift.pin_chapters(segments, topics, phys_start)
```

> Note: the idempotent `render_sermon` writes `_render.json` so a resumed run returns the same pinned boundaries without re-probing. When `sermon.mp4` exists but `_render.json` does not (e.g. a hand-cut from an interactive run), the caller supplies boundaries itself — the orchestrator (Phase 2) handles that; this task's tests cover the fresh-run and both-exist paths.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/auto/test_render_robust.py -q`
Expected: PASS (8 passed total in the file)

- [ ] **Step 5: Run the full suite (no regressions)**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (existing suite + new tests)

- [ ] **Step 6: Commit**

```bash
git add sermon_cut/auto/render_robust.py tests/auto/test_render_robust.py
git commit -m "feat(auto): integrated render_sermon + repin_chapters flow"
```

---

## Task 4: Headless Shorts selection (`select_short_moments`)

**Files:**
- Modify: `sermon_cut/shorts_select.py` (add `select_short_moments`)
- Test: `tests/auto/test_shorts_select_headless.py`

**Interfaces:**
- Consumes: existing `ShortCandidate`, `candidate_band`, `save_candidates` in `sermon_cut/shorts_select.py` (fields: `slug, title, source_start, source_end, rationale, standalone_confidence, context_integrity, chosen`; `candidate_band(c, cfg) -> str` returns one of `IN_BAND`/`WARN_LONG`/`TOO_SHORT`/`TOO_LONG`).
- Produces:
  - `select_short_moments(transcript_path, plan, out_path, cfg, *, judge, max_keep: int = 3) -> Path` — calls `judge(transcript_path, plan) -> list[dict]` (each dict has the ShortCandidate fields), keeps only entries with `chosen` truthy and `context_integrity == "ok"`, drops any whose `candidate_band` is `TOO_SHORT`/`TOO_LONG`, caps at `max_keep`, writes `candidates.json` via `save_candidates`, and returns the path. The default `judge` shells to `claude -p` (a headless selection prompt); tests inject a fake.

- [ ] **Step 1: Write the failing test**

```python
# tests/auto/test_shorts_select_headless.py
import json
from types import SimpleNamespace
from pathlib import Path

from sermon_cut import shorts_select


def _cfg():
    # candidate_band(c, cfg.shorts) reads hard_min/hard_max/target_max directly
    # off the shorts config; these mirror the real ShortsConfig defaults.
    return SimpleNamespace(shorts=SimpleNamespace(
        target_min=20, target_max=60, hard_min=15, hard_max=180))


def _moment(slug, s, e, chosen=True, integrity="ok"):
    return {"slug": slug, "title": slug.title(), "source_start": s, "source_end": e,
            "rationale": "r", "standalone_confidence": "high",
            "context_integrity": integrity, "chosen": chosen}


def test_select_keeps_only_chosen_in_band_ok_and_caps(tmp_path):
    moments = [
        _moment("a", 100.0, 145.0),                       # 45s ok
        _moment("b", 200.0, 260.0),                       # 60s ok
        _moment("c", 300.0, 302.0),                       # 2s -> TOO_SHORT, dropped
        _moment("d", 400.0, 450.0, integrity="risky"),    # excluded by integrity
        _moment("e", 500.0, 540.0, chosen=False),         # not chosen
        _moment("f", 600.0, 640.0),                       # 40s ok -> 4th, capped out
    ]
    judge = lambda tp, plan: moments
    out = shorts_select.select_short_moments(
        tmp_path / "transcript.json", {"start": 0}, tmp_path / "candidates.json",
        _cfg(), judge=judge, max_keep=3)
    saved = json.loads(Path(out).read_text())
    slugs = [c["slug"] for c in saved["candidates"]]
    assert slugs == ["a", "b", "f"]
    assert all(c["chosen"] for c in saved["candidates"])


def test_select_can_return_zero(tmp_path):
    judge = lambda tp, plan: []
    out = shorts_select.select_short_moments(
        tmp_path / "t.json", {"start": 0}, tmp_path / "candidates.json",
        _cfg(), judge=judge)
    assert json.loads(Path(out).read_text())["candidates"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/auto/test_shorts_select_headless.py -q`
Expected: FAIL (`AttributeError: ... has no attribute 'select_short_moments'`)

- [ ] **Step 3: Read the existing module, then add the function**

First read `sermon_cut/shorts_select.py` to confirm `ShortCandidate` fields and `candidate_band`/`save_candidates` signatures, then append:

```python
# append to sermon_cut/shorts_select.py
import subprocess


def _default_judge(transcript_path, plan):
    """Headless Shorts selection: run `claude -p` in the transcript's dir with the
    sermon-shorts selection skill; it writes candidates.json which we read back.
    Replaced by a fake in tests."""
    import json
    from pathlib import Path
    wd = Path(transcript_path).parent
    subprocess.run(["claude", "-p", "/sermon-shorts-select"], cwd=str(wd), check=True)
    cand = wd / "shorts" / "candidates.json"
    return json.loads(cand.read_text()).get("candidates", []) if cand.exists() else []


def select_short_moments(transcript_path, plan, out_path, cfg, *,
                         judge=_default_judge, max_keep: int = 3):
    from pathlib import Path
    moments = judge(transcript_path, plan) or []
    kept = []
    for m in moments:
        if not m.get("chosen"):
            continue
        if m.get("context_integrity") != "ok":
            continue
        c = ShortCandidate(
            slug=m["slug"], title=m["title"],
            source_start=float(m["source_start"]), source_end=float(m["source_end"]),
            rationale=m.get("rationale", ""),
            standalone_confidence=m.get("standalone_confidence", ""),
            context_integrity=m.get("context_integrity", "ok"),
            chosen=True)
        if candidate_band(c, cfg.shorts) in ("TOO_SHORT", "TOO_LONG"):
            continue
        kept.append(c)
        if len(kept) >= max_keep:
            break
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    save_candidates(kept, out)
    return out
```

> If `ShortCandidate` is a `@dataclass` with different field names or `save_candidates` expects a different shape, adapt the constructor/call to the actual definitions found when reading the module — the behavior (filter chosen + integrity + band, cap, save) is the contract; the field plumbing follows the existing type.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/auto/test_shorts_select_headless.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Run the full suite + commit**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS

```bash
git add sermon_cut/shorts_select.py tests/auto/test_shorts_select_headless.py
git commit -m "feat(auto): headless Shorts-moment selection (judge wrapper)"
```

---

## Out of scope (follow-on plans)

- **Orchestrator wiring** (Phase 2): call `render_sermon`/`repin_chapters`/`select_short_moments` from `orchestrator.py`; thread speaker/title/options; title-via-vision judgment call; livestream-thumb step; remove the `needs_approval` branch; error status; discovery watcher.
- **Web UI** (Phase 3) and **Docker packaging + NPM** (Phase 4), gated by the Phase-0 in-container `claude -p` spike.

## Self-Review notes

- Spec coverage (Phase 1): render-robustness = Tasks 2+3 (full-download-trim), drift = Task 1 + Task 3 flow, chapter re-pin = Task 1 `pin_chapters` + Task 3 `repin_chapters`, shorts-select = Task 4. Orchestrator/UI/Docker explicitly deferred.
- Invariants: full-video download only inside `render_sermon` (Task 3); all new modules stdlib-only at top level (transcribe imported lazily inside functions); every wrapper idempotent-by-artifact; all tests offline via injected runner/transcriber/judge.
- Type consistency: `drift.pin_boundaries`/`pin_chapters` signatures match their callers in Task 3; `RenderResult` fields match the Task-3 tests; `select_short_moments` uses the existing `ShortCandidate`/`candidate_band`/`save_candidates` (implementer verifies exact fields when reading the module).
