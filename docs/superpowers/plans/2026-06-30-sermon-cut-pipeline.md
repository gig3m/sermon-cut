# Sermon-Cut Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local pipeline + `/sermon-cut` skill that extracts the sermon from a 2-hour YouTube livestream, prepends an intro card, and uploads it to YouTube as Unlisted.

**Architecture:** A Python package (`sermon_cut`) of small, single-purpose stage functions, each wrapping an external tool (yt-dlp, faster-whisper, ffmpeg, YouTube Data API) behind an injectable `runner`/client for fast, free, offline tests. A thin argparse CLI (`python -m sermon_cut <stage>`) exposes each stage. The `/sermon-cut` skill orchestrates the stages and holds the only judgment step — reading the transcript to locate the sermon and draft metadata — between the `transcribe` and `clip` stages.

**Tech Stack:** Python 3.13, `yt-dlp`, `faster-whisper` (CTranslate2, arm64), `ffmpeg`, `google-api-python-client` + `google-auth-oauthlib`, `pytest`. Config in `config.toml` (stdlib `tomllib`).

## Global Constraints

- **No recurring cost.** Local tools + free YouTube Data API quota + existing Canva account only.
- **Never move the full 2-hour video.** Discovery runs on audio-only; video download is limited to the sermon window via `yt-dlp --download-sections`.
- **Upload is the only outbound action.** Privacy is always `unlisted`; nothing is ever uploaded Public by this pipeline.
- **Tests must not hit the network, GPU, or real binaries.** Every stage takes an injectable `runner` (default `subprocess.run`) or client; tests pass fakes.
- **Platform:** macOS arm64, Python 3.13, Homebrew. Use a project venv at `.venv`.
- **All run artifacts** live under `runs/<video-id>/`. Stages are idempotent: skip work whose output already exists.
- **Package import name:** `sermon_cut`. **CLI:** `python -m sermon_cut <subcommand>`.

---

### Task 1: Project scaffolding + config loader

**Files:**
- Create: `pyproject.toml`
- Create: `sermon_cut/__init__.py`
- Create: `sermon_cut/config.py`
- Create: `config.example.toml`
- Test: `tests/test_config.py`
- Create: `tests/__init__.py`

**Interfaces:**
- Produces: `Config` dataclass with fields `church_name: str`, `youtube_channel_id: str`, `default_speaker: str`, `canva_template_id: str`, `intro_seconds: int = 5`, `clip_padding_seconds: int = 8`, `whisper_model: str = "medium"`, `description_footer: str = ""`. Function `load_config(path: Path) -> Config`.

- [ ] **Step 1: Create the venv and install dev/runtime deps**

```bash
cd ~/projects/sermon-cut
python3 -m venv .venv
.venv/bin/python -m pip install -U pip pytest
```

- [ ] **Step 2: Write `pyproject.toml`**

```toml
[project]
name = "sermon-cut"
version = "0.1.0"
description = "Extract the sermon from a church livestream and upload it to YouTube."
requires-python = ">=3.13"
dependencies = [
    "faster-whisper>=1.0",
    "google-api-python-client>=2.0",
    "google-auth-oauthlib>=1.2",
]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["sermon_cut*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 3: Create empty `sermon_cut/__init__.py` and `tests/__init__.py`**

```bash
mkdir -p sermon_cut tests
touch sermon_cut/__init__.py tests/__init__.py
```

- [ ] **Step 4: Write the failing test** in `tests/test_config.py`

```python
from pathlib import Path
import textwrap
from sermon_cut.config import load_config, Config


def test_load_config_reads_values_and_defaults(tmp_path: Path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(textwrap.dedent("""
        church_name = "Grace Church"
        youtube_channel_id = "UC123"
        default_speaker = "Pastor Dan"
        canva_template_id = "TPL_abc"
        description_footer = "Join us Sundays at 10am."
    """))
    cfg = load_config(cfg_file)
    assert isinstance(cfg, Config)
    assert cfg.church_name == "Grace Church"
    assert cfg.default_speaker == "Pastor Dan"
    # defaults applied
    assert cfg.intro_seconds == 5
    assert cfg.clip_padding_seconds == 8
    assert cfg.whisper_model == "medium"


def test_load_config_missing_required_field_raises(tmp_path: Path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('church_name = "Grace Church"\n')
    import pytest
    with pytest.raises(KeyError):
        load_config(cfg_file)
```

- [ ] **Step 5: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sermon_cut.config'`

- [ ] **Step 6: Write `sermon_cut/config.py`**

```python
from __future__ import annotations
import tomllib
from dataclasses import dataclass
from pathlib import Path

REQUIRED = ("church_name", "youtube_channel_id", "default_speaker", "canva_template_id")


@dataclass
class Config:
    church_name: str
    youtube_channel_id: str
    default_speaker: str
    canva_template_id: str
    intro_seconds: int = 5
    clip_padding_seconds: int = 8
    whisper_model: str = "medium"
    description_footer: str = ""


def load_config(path: Path) -> Config:
    data = tomllib.loads(Path(path).read_text())
    for key in REQUIRED:
        if key not in data:
            raise KeyError(f"config.toml missing required field: {key}")
    return Config(**data)
```

- [ ] **Step 7: Write `config.example.toml`**

```toml
# Copy to config.toml and fill in. config.toml is gitignored.
church_name = "Your Church Name"
youtube_channel_id = "UCxxxxxxxxxxxxxxxxxxxxxx"  # your channel id
default_speaker = "Pastor Name"                  # assumed preacher; override per-run with --speaker
canva_template_id = "TPLxxxxxxxxx"               # brand template for the intro card
intro_seconds = 5
clip_padding_seconds = 8
whisper_model = "medium"
description_footer = """
—
Join us Sundays at 10:00am.
Website: https://example.church
"""
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_config.py -v`
Expected: PASS (2 passed)

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml sermon_cut/ tests/ config.example.toml
git commit -m "feat: project scaffolding and config loader"
```

---

### Task 2: Time-format helpers

**Files:**
- Create: `sermon_cut/timefmt.py`
- Test: `tests/test_timefmt.py`

**Interfaces:**
- Produces: `seconds_to_timestamp(seconds: float) -> str` returning `"HH:MM:SS"` (floor to whole seconds, zero-padded, hours unbounded). `clamp_nonnegative(seconds: float) -> float`.

- [ ] **Step 1: Write the failing test** in `tests/test_timefmt.py`

```python
from sermon_cut.timefmt import seconds_to_timestamp, clamp_nonnegative


def test_seconds_to_timestamp():
    assert seconds_to_timestamp(0) == "00:00:00"
    assert seconds_to_timestamp(65) == "00:01:05"
    assert seconds_to_timestamp(3661) == "01:01:01"
    assert seconds_to_timestamp(45.9) == "00:00:45"  # floors


def test_clamp_nonnegative():
    assert clamp_nonnegative(-3) == 0
    assert clamp_nonnegative(10) == 10
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_timefmt.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write `sermon_cut/timefmt.py`**

```python
from __future__ import annotations


def seconds_to_timestamp(seconds: float) -> str:
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def clamp_nonnegative(seconds: float) -> float:
    return max(0.0, seconds)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_timefmt.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add sermon_cut/timefmt.py tests/test_timefmt.py
git commit -m "feat: time-format helpers"
```

---

### Task 3: `fetch_audio` stage (yt-dlp audio-only + latest-resolver)

**Files:**
- Create: `sermon_cut/fetch_audio.py`
- Test: `tests/test_fetch_audio.py`

**Interfaces:**
- Consumes: nothing from prior tasks.
- Produces:
  - `AudioResult` dataclass: `audio_path: Path`, `info_path: Path`, `video_id: str`, `title: str`, `duration: float`.
  - `fetch_audio(url: str, workdir: Path, *, runner=subprocess.run) -> AudioResult` — runs yt-dlp to download bestaudio as `audio.m4a` and write `source.info.json`; parses the info json for `id`/`title`/`duration`. Idempotent: if `audio.m4a` and `source.info.json` already exist, skip the download and just parse.
  - `resolve_latest(channel_id: str, *, runner=subprocess.run) -> str` — returns the watch URL of the channel's most recent upload.

- [ ] **Step 1: Write the failing test** in `tests/test_fetch_audio.py`

```python
import json
import subprocess
from pathlib import Path
from sermon_cut.fetch_audio import fetch_audio, resolve_latest, AudioResult


class FakeRunner:
    def __init__(self, info: dict, workdir: Path):
        self.info = info
        self.workdir = workdir
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        # simulate yt-dlp writing the audio file and info json
        (self.workdir / "audio.m4a").write_bytes(b"fake-audio")
        (self.workdir / "source.info.json").write_text(json.dumps(self.info))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


def test_fetch_audio_downloads_and_parses(tmp_path: Path):
    info = {"id": "vid123", "title": "Sunday Service", "duration": 7200}
    runner = FakeRunner(info, tmp_path)
    result = fetch_audio("https://youtu.be/vid123", tmp_path, runner=runner)
    assert isinstance(result, AudioResult)
    assert result.video_id == "vid123"
    assert result.title == "Sunday Service"
    assert result.duration == 7200
    assert result.audio_path == tmp_path / "audio.m4a"
    assert any("yt-dlp" in part for part in runner.calls[0])
    # audio-only: must request bestaudio, not full video
    assert "-f" in runner.calls[0] and "bestaudio" in " ".join(runner.calls[0])


def test_fetch_audio_idempotent_skips_download(tmp_path: Path):
    (tmp_path / "audio.m4a").write_bytes(b"already")
    (tmp_path / "source.info.json").write_text(
        json.dumps({"id": "v", "title": "t", "duration": 1})
    )

    def explode(cmd, **kwargs):
        raise AssertionError("runner should not be called when artifacts exist")

    result = fetch_audio("https://youtu.be/v", tmp_path, runner=explode)
    assert result.video_id == "v"


def test_resolve_latest_returns_watch_url(tmp_path: Path):
    def runner(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="abc987\n", stderr="")

    url = resolve_latest("UC123", runner=runner)
    assert url == "https://www.youtube.com/watch?v=abc987"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_fetch_audio.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write `sermon_cut/fetch_audio.py`**

```python
from __future__ import annotations
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class AudioResult:
    audio_path: Path
    info_path: Path
    video_id: str
    title: str
    duration: float


def fetch_audio(url: str, workdir: Path, *, runner=subprocess.run) -> AudioResult:
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    audio_path = workdir / "audio.m4a"
    info_path = workdir / "source.info.json"

    if not (audio_path.exists() and info_path.exists()):
        cmd = [
            "yt-dlp",
            "-f", "bestaudio[ext=m4a]/bestaudio",
            "--extract-audio", "--audio-format", "m4a",
            "--write-info-json",
            "-o", str(workdir / "audio.%(ext)s"),
            "--no-progress",
            url,
        ]
        runner(cmd, check=True)

    info = json.loads(info_path.read_text())
    return AudioResult(
        audio_path=audio_path,
        info_path=info_path,
        video_id=info["id"],
        title=info["title"],
        duration=float(info["duration"]),
    )


def resolve_latest(channel_id: str, *, runner=subprocess.run) -> str:
    cmd = [
        "yt-dlp",
        f"https://www.youtube.com/channel/{channel_id}/videos",
        "--flat-playlist",
        "--playlist-items", "1",
        "--print", "id",
        "--no-warnings",
    ]
    proc = runner(cmd, check=True, capture_output=True, text=True)
    video_id = proc.stdout.strip().splitlines()[0]
    return f"https://www.youtube.com/watch?v={video_id}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_fetch_audio.py -v`
Expected: PASS (3 passed)

> Note: the idempotent test passes a 2-arg `explode(cmd, **kwargs)`; `fetch_audio` calls `runner(cmd, check=True)`. The `resolve_latest` test's fake ignores `capture_output`/`text` via `**kwargs`. Keep that shape.

- [ ] **Step 5: Commit**

```bash
git add sermon_cut/fetch_audio.py tests/test_fetch_audio.py
git commit -m "feat: fetch_audio stage with latest-resolver"
```

---

### Task 4: `transcribe` stage (faster-whisper → json + srt)

**Files:**
- Create: `sermon_cut/transcribe.py`
- Test: `tests/test_transcribe.py`

**Interfaces:**
- Consumes: `AudioResult.audio_path` from Task 3.
- Produces:
  - `TranscriptResult` dataclass: `json_path: Path`, `srt_path: Path`, `segments: list[dict]` where each dict is `{"start": float, "end": float, "text": str}`.
  - `transcribe(audio_path: Path, workdir: Path, *, model_name: str = "medium", model_factory=None) -> TranscriptResult` — uses `model_factory(model_name)` (default builds a real `faster_whisper.WhisperModel`) whose `.transcribe(path)` yields `(segments_iter, info)`; each segment has `.start`, `.end`, `.text`. Writes `transcript.json` and `transcript.srt`. Idempotent on `transcript.json`.

- [ ] **Step 1: Write the failing test** in `tests/test_transcribe.py`

```python
from pathlib import Path
import json
from sermon_cut.transcribe import transcribe, TranscriptResult


class FakeSegment:
    def __init__(self, start, end, text):
        self.start = start
        self.end = end
        self.text = text


class FakeModel:
    def __init__(self, name):
        self.name = name

    def transcribe(self, path, **kwargs):
        segs = [
            FakeSegment(0.0, 2.0, " Welcome to worship."),
            FakeSegment(2.0, 5.0, " Let us pray."),
        ]
        return iter(segs), {"language": "en"}


def test_transcribe_writes_json_and_srt(tmp_path: Path):
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"fake")
    result = transcribe(audio, tmp_path, model_name="medium",
                        model_factory=lambda name: FakeModel(name))
    assert isinstance(result, TranscriptResult)
    assert result.json_path.exists()
    assert result.srt_path.exists()
    data = json.loads(result.json_path.read_text())
    assert data[0] == {"start": 0.0, "end": 2.0, "text": "Welcome to worship."}
    assert result.segments[1]["text"] == "Let us pray."
    srt = result.srt_path.read_text()
    assert "00:00:00,000 --> 00:00:02,000" in srt
    assert "Welcome to worship." in srt


def test_transcribe_idempotent(tmp_path: Path):
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"fake")
    (tmp_path / "transcript.json").write_text(
        json.dumps([{"start": 0.0, "end": 1.0, "text": "cached"}])
    )
    (tmp_path / "transcript.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\ncached\n")

    def explode(name):
        raise AssertionError("model_factory should not be called when transcript exists")

    result = transcribe(audio, tmp_path, model_factory=explode)
    assert result.segments[0]["text"] == "cached"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_transcribe.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write `sermon_cut/transcribe.py`**

```python
from __future__ import annotations
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class TranscriptResult:
    json_path: Path
    srt_path: Path
    segments: list[dict]


def _default_model_factory(model_name: str):
    from faster_whisper import WhisperModel
    return WhisperModel(model_name, device="cpu", compute_type="int8")


def _srt_timestamp(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _write_srt(segments: list[dict], path: Path) -> None:
    lines = []
    for i, seg in enumerate(segments, start=1):
        lines.append(str(i))
        lines.append(f"{_srt_timestamp(seg['start'])} --> {_srt_timestamp(seg['end'])}")
        lines.append(seg["text"])
        lines.append("")
    path.write_text("\n".join(lines))


def transcribe(audio_path: Path, workdir: Path, *, model_name: str = "medium",
               model_factory=None) -> TranscriptResult:
    workdir = Path(workdir)
    json_path = workdir / "transcript.json"
    srt_path = workdir / "transcript.srt"

    if json_path.exists() and srt_path.exists():
        return TranscriptResult(json_path, srt_path, json.loads(json_path.read_text()))

    factory = model_factory or _default_model_factory
    model = factory(model_name)
    seg_iter, _info = model.transcribe(str(audio_path))
    segments = [
        {"start": float(s.start), "end": float(s.end), "text": s.text.strip()}
        for s in seg_iter
    ]
    json_path.write_text(json.dumps(segments, indent=2))
    _write_srt(segments, srt_path)
    return TranscriptResult(json_path, srt_path, segments)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_transcribe.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add sermon_cut/transcribe.py tests/test_transcribe.py
git commit -m "feat: transcribe stage (faster-whisper to json/srt)"
```

---

### Task 5: Sermon plan schema + validation + sanity band

**Files:**
- Create: `sermon_cut/plan.py`
- Test: `tests/test_plan.py`

**Interfaces:**
- Consumes: a `plan.json` file authored by the skill (Claude) after reading `transcript.json`.
- Produces:
  - `Chapter` dataclass: `time: float`, `label: str`.
  - `SermonPlan` dataclass: `start: float`, `end: float`, `title: str`, `speaker: str`, `date: str`, `description: str`, `chapters: list[Chapter]`.
  - `load_plan(path: Path) -> SermonPlan`.
  - `validate_plan(plan: SermonPlan) -> None` — raises `ValueError` if `end <= start`, title/speaker/date empty, or any chapter time outside `[start, end]`.
  - `segment_within_band(plan: SermonPlan, *, min_seconds: int = 600, max_seconds: int = 2700) -> bool` — True when sermon length is within 10–45 min.

- [ ] **Step 1: Write the failing test** in `tests/test_plan.py`

```python
import json
from pathlib import Path
import pytest
from sermon_cut.plan import load_plan, validate_plan, segment_within_band, SermonPlan


def _good_plan_dict():
    return {
        "start": 3000.0,
        "end": 4500.0,
        "title": "Walking by Faith",
        "speaker": "Pastor Dan",
        "date": "2026-06-28",
        "description": "A sermon on Hebrews 11.",
        "chapters": [
            {"time": 3000.0, "label": "Introduction"},
            {"time": 3600.0, "label": "Main point"},
        ],
    }


def test_load_and_validate_good_plan(tmp_path: Path):
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(_good_plan_dict()))
    plan = load_plan(p)
    assert isinstance(plan, SermonPlan)
    assert plan.title == "Walking by Faith"
    assert plan.chapters[1].label == "Main point"
    validate_plan(plan)  # should not raise


def test_validate_rejects_end_before_start(tmp_path: Path):
    d = _good_plan_dict()
    d["end"] = d["start"] - 1
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(d))
    with pytest.raises(ValueError):
        validate_plan(load_plan(p))


def test_validate_rejects_chapter_out_of_range(tmp_path: Path):
    d = _good_plan_dict()
    d["chapters"].append({"time": 99999.0, "label": "Bad"})
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(d))
    with pytest.raises(ValueError):
        validate_plan(load_plan(p))


def test_validate_rejects_empty_speaker(tmp_path: Path):
    d = _good_plan_dict()
    d["speaker"] = ""
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(d))
    with pytest.raises(ValueError):
        validate_plan(load_plan(p))


def test_segment_within_band():
    d = _good_plan_dict()  # 1500s = 25 min
    plan = SermonPlan(**{**d, "chapters": []})
    assert segment_within_band(plan) is True
    short = SermonPlan(**{**d, "start": 0.0, "end": 300.0, "chapters": []})  # 5 min
    assert segment_within_band(short) is False
    long = SermonPlan(**{**d, "start": 0.0, "end": 3600.0, "chapters": []})  # 60 min
    assert segment_within_band(long) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_plan.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write `sermon_cut/plan.py`**

```python
from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Chapter:
    time: float
    label: str


@dataclass
class SermonPlan:
    start: float
    end: float
    title: str
    speaker: str
    date: str
    description: str
    chapters: list[Chapter] = field(default_factory=list)


def load_plan(path: Path) -> SermonPlan:
    data = json.loads(Path(path).read_text())
    chapters = [Chapter(time=float(c["time"]), label=c["label"])
                for c in data.get("chapters", [])]
    return SermonPlan(
        start=float(data["start"]),
        end=float(data["end"]),
        title=data["title"],
        speaker=data["speaker"],
        date=data["date"],
        description=data["description"],
        chapters=chapters,
    )


def validate_plan(plan: SermonPlan) -> None:
    if plan.end <= plan.start:
        raise ValueError(f"end ({plan.end}) must be greater than start ({plan.start})")
    for name in ("title", "speaker", "date"):
        if not str(getattr(plan, name)).strip():
            raise ValueError(f"{name} must not be empty")
    for ch in plan.chapters:
        if not (plan.start <= ch.time <= plan.end):
            raise ValueError(
                f"chapter '{ch.label}' time {ch.time} outside [{plan.start}, {plan.end}]"
            )


def segment_within_band(plan: SermonPlan, *, min_seconds: int = 600,
                        max_seconds: int = 2700) -> bool:
    length = plan.end - plan.start
    return min_seconds <= length <= max_seconds
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_plan.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add sermon_cut/plan.py tests/test_plan.py
git commit -m "feat: sermon plan schema, validation, sanity band"
```

---

### Task 6: `clip_video` stage (yt-dlp download-sections)

**Files:**
- Create: `sermon_cut/clip_video.py`
- Test: `tests/test_clip_video.py`

**Interfaces:**
- Consumes: `SermonPlan.start`/`.end` (Task 5); `seconds_to_timestamp`/`clamp_nonnegative` (Task 2).
- Produces: `clip_video(url: str, start: float, end: float, workdir: Path, *, padding: int = 8, runner=subprocess.run) -> Path` — downloads only `[start-padding, end+padding]` of the video as `sermon.mp4` and returns its path. Idempotent on `sermon.mp4`.

- [ ] **Step 1: Write the failing test** in `tests/test_clip_video.py`

```python
import subprocess
from pathlib import Path
from sermon_cut.clip_video import clip_video


def test_clip_video_builds_download_sections(tmp_path: Path):
    captured = {}

    def runner(cmd, **kwargs):
        captured["cmd"] = cmd
        (tmp_path / "sermon.mp4").write_bytes(b"video")
        return subprocess.CompletedProcess(cmd, 0)

    out = clip_video("https://youtu.be/v", 3000.0, 4500.0, tmp_path,
                     padding=8, runner=runner)
    assert out == tmp_path / "sermon.mp4"
    cmd = captured["cmd"]
    assert "yt-dlp" in cmd[0]
    joined = " ".join(cmd)
    # 3000-8 = 2992 -> 00:49:52 ; 4500+8 = 4508 -> 01:15:08
    assert "--download-sections" in cmd
    assert "*00:49:52-01:15:08" in joined


def test_clip_video_clamps_padding_at_zero(tmp_path: Path):
    captured = {}

    def runner(cmd, **kwargs):
        captured["cmd"] = cmd
        (tmp_path / "sermon.mp4").write_bytes(b"video")
        return subprocess.CompletedProcess(cmd, 0)

    clip_video("https://youtu.be/v", 3.0, 100.0, tmp_path, padding=8, runner=runner)
    assert "*00:00:00-00:01:48" in " ".join(captured["cmd"])  # start clamps to 0


def test_clip_video_idempotent(tmp_path: Path):
    (tmp_path / "sermon.mp4").write_bytes(b"exists")

    def explode(cmd, **kwargs):
        raise AssertionError("should not download when sermon.mp4 exists")

    out = clip_video("https://youtu.be/v", 0.0, 10.0, tmp_path, runner=explode)
    assert out == tmp_path / "sermon.mp4"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_clip_video.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write `sermon_cut/clip_video.py`**

```python
from __future__ import annotations
import subprocess
from pathlib import Path
from .timefmt import seconds_to_timestamp, clamp_nonnegative


def clip_video(url: str, start: float, end: float, workdir: Path, *,
               padding: int = 8, runner=subprocess.run) -> Path:
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    out = workdir / "sermon.mp4"
    if out.exists():
        return out

    a = seconds_to_timestamp(clamp_nonnegative(start - padding))
    b = seconds_to_timestamp(end + padding)
    cmd = [
        "yt-dlp",
        "-f", "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
        "--download-sections", f"*{a}-{b}",
        "--force-keyframes-at-cuts",
        "--merge-output-format", "mp4",
        "-o", str(out),
        "--no-progress",
        url,
    ]
    runner(cmd, check=True)
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_clip_video.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add sermon_cut/clip_video.py tests/test_clip_video.py
git commit -m "feat: clip_video stage (download sermon window only)"
```

---

### Task 7: `make_intro` stage (image → intro clip)

**Files:**
- Create: `sermon_cut/intro.py`
- Test: `tests/test_intro.py`

**Interfaces:**
- Consumes: an intro card image path (PNG/JPG). For v1 this image is supplied to the stage; the Canva-fill-and-export step is a documented future seam (see README) and is NOT built here.
- Produces: `make_intro(image_path: Path, workdir: Path, *, seconds: int = 5, width: int = 1920, height: int = 1080, fps: int = 30, runner=subprocess.run) -> Path` — renders `intro.mp4`: the still image held for `seconds`, scaled/padded to `width`x`height` at `fps`, with a silent stereo audio track (so concat has matching streams). Idempotent on `intro.mp4`.

- [ ] **Step 1: Write the failing test** in `tests/test_intro.py`

```python
import subprocess
from pathlib import Path
from sermon_cut.intro import make_intro


def test_make_intro_builds_ffmpeg_command(tmp_path: Path):
    img = tmp_path / "card.png"
    img.write_bytes(b"png")
    captured = {}

    def runner(cmd, **kwargs):
        captured["cmd"] = cmd
        (tmp_path / "intro.mp4").write_bytes(b"intro")
        return subprocess.CompletedProcess(cmd, 0)

    out = make_intro(img, tmp_path, seconds=5, width=1920, height=1080, fps=30,
                     runner=runner)
    assert out == tmp_path / "intro.mp4"
    cmd = captured["cmd"]
    joined = " ".join(cmd)
    assert cmd[0] == "ffmpeg"
    assert str(img) in joined
    assert "anullsrc" in joined            # silent audio track
    assert "-t" in cmd and "5" in cmd      # duration
    assert "1920" in joined and "1080" in joined
    assert "-r" in cmd and "30" in cmd     # fps


def test_make_intro_idempotent(tmp_path: Path):
    img = tmp_path / "card.png"
    img.write_bytes(b"png")
    (tmp_path / "intro.mp4").write_bytes(b"exists")

    def explode(cmd, **kwargs):
        raise AssertionError("should not render when intro.mp4 exists")

    out = make_intro(img, tmp_path, runner=explode)
    assert out == tmp_path / "intro.mp4"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_intro.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write `sermon_cut/intro.py`**

```python
from __future__ import annotations
import subprocess
from pathlib import Path


def make_intro(image_path: Path, workdir: Path, *, seconds: int = 5,
               width: int = 1920, height: int = 1080, fps: int = 30,
               runner=subprocess.run) -> Path:
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    out = workdir / "intro.mp4"
    if out.exists():
        return out

    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1"
    )
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", str(image_path),
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-t", str(seconds),
        "-vf", vf,
        "-r", str(fps),
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest",
        str(out),
    ]
    runner(cmd, check=True)
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_intro.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add sermon_cut/intro.py tests/test_intro.py
git commit -m "feat: make_intro stage (still image to intro clip)"
```

---

### Task 8: `assemble` stage (concat intro + sermon, normalize audio)

**Files:**
- Create: `sermon_cut/assemble.py`
- Test: `tests/test_assemble.py`

**Interfaces:**
- Consumes: `intro.mp4` (Task 7), `sermon.mp4` (Task 6).
- Produces: `assemble(intro_path: Path, sermon_path: Path, workdir: Path, *, width: int = 1920, height: int = 1080, fps: int = 30, runner=subprocess.run) -> Path` — concatenates intro then sermon using the ffmpeg `concat` filter (re-encode so mismatched source params normalize), scaling/padding both to `width`x`height`@`fps` and loudness-normalizing audio. Writes `final.mp4`. Idempotent on `final.mp4`.

- [ ] **Step 1: Write the failing test** in `tests/test_assemble.py`

```python
import subprocess
from pathlib import Path
from sermon_cut.assemble import assemble


def test_assemble_builds_concat_filter(tmp_path: Path):
    intro = tmp_path / "intro.mp4"; intro.write_bytes(b"i")
    sermon = tmp_path / "sermon.mp4"; sermon.write_bytes(b"s")
    captured = {}

    def runner(cmd, **kwargs):
        captured["cmd"] = cmd
        (tmp_path / "final.mp4").write_bytes(b"final")
        return subprocess.CompletedProcess(cmd, 0)

    out = assemble(intro, sermon, tmp_path, runner=runner)
    assert out == tmp_path / "final.mp4"
    cmd = captured["cmd"]
    joined = " ".join(cmd)
    assert cmd[0] == "ffmpeg"
    assert str(intro) in joined and str(sermon) in joined
    assert "concat=n=2:v=1:a=1" in joined
    assert "loudnorm" in joined


def test_assemble_idempotent(tmp_path: Path):
    intro = tmp_path / "intro.mp4"; intro.write_bytes(b"i")
    sermon = tmp_path / "sermon.mp4"; sermon.write_bytes(b"s")
    (tmp_path / "final.mp4").write_bytes(b"exists")

    def explode(cmd, **kwargs):
        raise AssertionError("should not render when final.mp4 exists")

    out = assemble(intro, sermon, tmp_path, runner=explode)
    assert out == tmp_path / "final.mp4"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_assemble.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write `sermon_cut/assemble.py`**

```python
from __future__ import annotations
import subprocess
from pathlib import Path


def assemble(intro_path: Path, sermon_path: Path, workdir: Path, *,
             width: int = 1920, height: int = 1080, fps: int = 30,
             runner=subprocess.run) -> Path:
    workdir = Path(workdir)
    out = workdir / "final.mp4"
    if out.exists():
        return out

    scale = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}"
    )
    filtergraph = (
        f"[0:v]{scale}[v0];[1:v]{scale}[v1];"
        f"[v0][0:a][v1][1:a]concat=n=2:v=1:a=1[v][a];"
        f"[a]loudnorm[ao]"
    )
    cmd = [
        "ffmpeg", "-y",
        "-i", str(intro_path),
        "-i", str(sermon_path),
        "-filter_complex", filtergraph,
        "-map", "[v]", "-map", "[ao]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        str(out),
    ]
    runner(cmd, check=True)
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_assemble.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add sermon_cut/assemble.py tests/test_assemble.py
git commit -m "feat: assemble stage (concat + audio normalize)"
```

---

### Task 9: `upload` stage (description builder + YouTube Data API)

**Files:**
- Create: `sermon_cut/upload.py`
- Create: `sermon_cut/youtube_auth.py`
- Test: `tests/test_upload.py`

**Interfaces:**
- Consumes: `SermonPlan` (Task 5), `Config.description_footer` (Task 1); `seconds_to_timestamp` (Task 2).
- Produces:
  - `build_description(plan: SermonPlan, footer: str) -> str` — sermon description, then a blank line, then `Chapters:` with one `MM:SS Label` line per chapter **measured relative to the final video** (i.e. chapter time minus `plan.start`, plus the intro offset passed in), then a blank line, then the footer. Signature: `build_description(plan, footer, *, intro_seconds: int = 0) -> str`.
  - `VideoMetadata` dataclass: `title: str`, `description: str`, `tags: list[str]`, `privacy: str = "unlisted"`.
  - `upload_video(file_path: Path, metadata: VideoMetadata, *, youtube_client, media_factory=None) -> str` — calls the injected `youtube_client.videos().insert(...).execute()` and returns the watch URL `https://youtu.be/<id>`.
  - `youtube_auth.get_authenticated_service(client_secrets: Path, token_path: Path)` — builds an OAuth'd YouTube client (real Google libs; not unit-tested, exercised via setup).

- [ ] **Step 1: Write the failing test** in `tests/test_upload.py`

```python
from pathlib import Path
from sermon_cut.plan import SermonPlan, Chapter
from sermon_cut.upload import build_description, upload_video, VideoMetadata


def test_build_description_offsets_chapters_to_final_video():
    plan = SermonPlan(
        start=3000.0, end=4500.0, title="Faith", speaker="Pastor Dan",
        date="2026-06-28", description="On Hebrews 11.",
        chapters=[Chapter(3000.0, "Intro"), Chapter(3600.0, "Point One")],
    )
    text = build_description(plan, "Footer line.", intro_seconds=5)
    # chapter at sermon start -> 5s into final video -> 00:05
    assert "00:05 Intro" in text
    # chapter 600s after sermon start + 5s intro -> 605s -> 10:05
    assert "10:05 Point One" in text
    assert "On Hebrews 11." in text
    assert "Footer line." in text
    assert "Chapters:" in text


class FakeInsert:
    def __init__(self, store): self.store = store
    def execute(self): return {"id": "newvid42"}


class FakeVideos:
    def __init__(self, store): self.store = store
    def insert(self, **kwargs):
        self.store["insert_kwargs"] = kwargs
        return FakeInsert(self.store)


class FakeYouTube:
    def __init__(self): self.store = {}
    def videos(self): return FakeVideos(self.store)


def test_upload_video_inserts_and_returns_url(tmp_path: Path):
    video = tmp_path / "final.mp4"; video.write_bytes(b"v")
    yt = FakeYouTube()
    meta = VideoMetadata(title="Faith", description="desc", tags=["sermon"])
    # media_factory avoids touching real MediaFileUpload
    url = upload_video(video, meta, youtube_client=yt,
                       media_factory=lambda path: f"MEDIA:{path}")
    assert url == "https://youtu.be/newvid42"
    body = yt.store["insert_kwargs"]["body"]
    assert body["snippet"]["title"] == "Faith"
    assert body["status"]["privacyStatus"] == "unlisted"
    assert yt.store["insert_kwargs"]["part"] == "snippet,status"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_upload.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write `sermon_cut/upload.py`**

```python
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from .plan import SermonPlan


def _mmss(seconds: float) -> str:
    total = int(seconds)
    m, s = divmod(total, 60)
    return f"{m:02d}:{s:02d}"


def build_description(plan: SermonPlan, footer: str, *, intro_seconds: int = 0) -> str:
    parts = [plan.description.strip()]
    if plan.chapters:
        lines = ["", "Chapters:"]
        for ch in plan.chapters:
            offset = (ch.time - plan.start) + intro_seconds
            lines.append(f"{_mmss(offset)} {ch.label}")
        parts.append("\n".join(lines))
    if footer.strip():
        parts.append(footer.strip())
    return "\n\n".join(parts)


@dataclass
class VideoMetadata:
    title: str
    description: str
    tags: list[str] = field(default_factory=list)
    privacy: str = "unlisted"


def _default_media_factory(path: Path):
    from googleapiclient.http import MediaFileUpload
    return MediaFileUpload(str(path), chunksize=-1, resumable=True)


def upload_video(file_path: Path, metadata: VideoMetadata, *, youtube_client,
                 media_factory=None) -> str:
    factory = media_factory or _default_media_factory
    media = factory(file_path)
    body = {
        "snippet": {
            "title": metadata.title,
            "description": metadata.description,
            "tags": metadata.tags,
            "categoryId": "29",  # Nonprofits & Activism
        },
        "status": {"privacyStatus": metadata.privacy, "selfDeclaredMadeForKids": False},
    }
    request = youtube_client.videos().insert(part="snippet,status", body=body,
                                             media_body=media)
    response = request.execute()
    return f"https://youtu.be/{response['id']}"
```

- [ ] **Step 4: Write `sermon_cut/youtube_auth.py`**

```python
from __future__ import annotations
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def get_authenticated_service(client_secrets: Path, token_path: Path):
    """Build an OAuth'd YouTube Data API client.

    Runs the installed-app flow on first use (opens a browser), then caches the
    refresh token at token_path for unattended reuse. Free; uses YouTube Data
    API v3 quota only.
    """
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    token_path = Path(token_path)
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json())
    return build("youtube", "v3", credentials=creds)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_upload.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add sermon_cut/upload.py sermon_cut/youtube_auth.py tests/test_upload.py
git commit -m "feat: upload stage (description builder + YouTube Data API)"
```

---

### Task 10: CLI dispatcher (`python -m sermon_cut`)

**Files:**
- Create: `sermon_cut/__main__.py`
- Create: `sermon_cut/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: all stage functions (Tasks 3–9), `load_config` (Task 1).
- Produces: `build_parser() -> argparse.ArgumentParser` and `main(argv: list[str]) -> int`. Subcommands, each taking `--workdir` and writing the stage's artifact, printing the output path (or URL) to stdout:
  - `fetch-audio URL` / `resolve-latest CHANNEL_ID`
  - `transcribe --audio PATH [--model NAME]`
  - `validate-plan --plan PATH` (prints `OK` + `IN_BAND`/`OUT_OF_BAND`)
  - `clip URL --start S --end S [--padding N]`
  - `intro --image PATH [--seconds N]`
  - `assemble --intro PATH --sermon PATH`
  - `upload --video PATH --plan PATH [--config PATH] [--client-secrets PATH] [--token PATH]`

  Test only the arg-parsing/dispatch wiring with stage functions monkeypatched (no real tools).

- [ ] **Step 1: Write the failing test** in `tests/test_cli.py`

```python
from pathlib import Path
import sermon_cut.cli as cli


def test_validate_plan_subcommand_reports_band(tmp_path, capsys, monkeypatch):
    import json
    plan = {
        "start": 3000.0, "end": 4500.0, "title": "T", "speaker": "S",
        "date": "2026-06-28", "description": "d", "chapters": [],
    }
    p = tmp_path / "plan.json"; p.write_text(json.dumps(plan))
    rc = cli.main(["validate-plan", "--plan", str(p)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "OK" in out and "IN_BAND" in out


def test_clip_subcommand_dispatches(tmp_path, capsys, monkeypatch):
    called = {}

    def fake_clip(url, start, end, workdir, *, padding=8, runner=None):
        called["args"] = (url, start, end, Path(workdir), padding)
        return Path(workdir) / "sermon.mp4"

    monkeypatch.setattr(cli, "clip_video", fake_clip)
    rc = cli.main(["clip", "https://youtu.be/v", "--start", "3000", "--end",
                   "4500", "--padding", "8", "--workdir", str(tmp_path)])
    assert rc == 0
    assert called["args"][0] == "https://youtu.be/v"
    assert called["args"][1] == 3000.0
    assert str(tmp_path / "sermon.mp4") in capsys.readouterr().out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sermon_cut.cli'`

- [ ] **Step 3: Write `sermon_cut/cli.py`**

```python
from __future__ import annotations
import argparse
from pathlib import Path

from .fetch_audio import fetch_audio, resolve_latest
from .transcribe import transcribe
from .plan import load_plan, validate_plan, segment_within_band
from .clip_video import clip_video
from .intro import make_intro
from .assemble import assemble
from .upload import build_description, upload_video, VideoMetadata
from .config import load_config


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sermon_cut")
    # Shared parent so every subcommand accepts --workdir AFTER the subcommand name.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workdir", default=".")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("fetch-audio", parents=[common]); s.add_argument("url")
    s = sub.add_parser("resolve-latest", parents=[common]); s.add_argument("channel_id")
    s = sub.add_parser("transcribe", parents=[common]); s.add_argument("--audio", required=True); s.add_argument("--model", default="medium")
    s = sub.add_parser("validate-plan", parents=[common]); s.add_argument("--plan", required=True)
    s = sub.add_parser("clip", parents=[common]); s.add_argument("url"); s.add_argument("--start", type=float, required=True); s.add_argument("--end", type=float, required=True); s.add_argument("--padding", type=int, default=8)
    s = sub.add_parser("intro", parents=[common]); s.add_argument("--image", required=True); s.add_argument("--seconds", type=int, default=5)
    s = sub.add_parser("assemble", parents=[common]); s.add_argument("--intro", required=True); s.add_argument("--sermon", required=True)
    s = sub.add_parser("upload", parents=[common]); s.add_argument("--video", required=True); s.add_argument("--plan", required=True); s.add_argument("--config", default="config.toml"); s.add_argument("--client-secrets", default="client_secrets.json"); s.add_argument("--token", default="token.json")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    workdir = Path(args.workdir)

    if args.cmd == "fetch-audio":
        print(fetch_audio(args.url, workdir).audio_path)
    elif args.cmd == "resolve-latest":
        print(resolve_latest(args.channel_id))
    elif args.cmd == "transcribe":
        print(transcribe(Path(args.audio), workdir, model_name=args.model).json_path)
    elif args.cmd == "validate-plan":
        plan = load_plan(Path(args.plan))
        validate_plan(plan)
        band = "IN_BAND" if segment_within_band(plan) else "OUT_OF_BAND"
        print(f"OK {band}")
    elif args.cmd == "clip":
        print(clip_video(args.url, args.start, args.end, workdir, padding=args.padding))
    elif args.cmd == "intro":
        print(make_intro(Path(args.image), workdir, seconds=args.seconds))
    elif args.cmd == "assemble":
        print(assemble(Path(args.intro), Path(args.sermon), workdir))
    elif args.cmd == "upload":
        from .youtube_auth import get_authenticated_service
        cfg = load_config(Path(args.config))
        plan = load_plan(Path(args.plan))
        description = build_description(plan, cfg.description_footer,
                                       intro_seconds=cfg.intro_seconds)
        meta = VideoMetadata(title=plan.title, description=description,
                             tags=[cfg.church_name, "sermon", plan.speaker])
        yt = get_authenticated_service(Path(args.client_secrets), Path(args.token))
        print(upload_video(Path(args.video), meta, youtube_client=yt))
    return 0
```

- [ ] **Step 4: Write `sermon_cut/__main__.py`**

```python
import sys
from .cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_cli.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/python -m pytest -v`
Expected: PASS (all tasks' tests green)

- [ ] **Step 7: Commit**

```bash
git add sermon_cut/cli.py sermon_cut/__main__.py tests/test_cli.py
git commit -m "feat: CLI dispatcher for all stages"
```

---

### Task 11: The `/sermon-cut` skill (orchestration + confirm gate)

**Files:**
- Create: `.claude/skills/sermon-cut/SKILL.md`

**Interfaces:**
- Consumes: the CLI (Task 10) and `transcript.json`/`plan.json` formats (Tasks 4–5).
- Produces: a skill that, given a YouTube URL or `latest`, runs the pipeline end-to-end, performing the sermon-finding reasoning and the confirm gate itself.

- [ ] **Step 1: Write `.claude/skills/sermon-cut/SKILL.md`**

````markdown
---
name: sermon-cut
description: Extract the sermon from a 2-hour church livestream archive and upload it to YouTube as Unlisted. Use when the user wants to process a Sunday service recording into a sermon video. Accepts a YouTube URL or "latest", optional --yes (skip confirm gate) and --speaker "Name" (guest preacher).
---

# Sermon Cut

Run the pipeline in `~/projects/sermon-cut` to turn a full service recording into
a sermon video. The Python package does the mechanical work; YOU do the judgment
step (reading the transcript to locate the sermon and draft metadata).

## Setup check (first run only)

Confirm `~/projects/sermon-cut/.venv`, `config.toml`, `client_secrets.json`, and
`token.json` exist. If not, point the user at `README.md` setup. Always invoke the
CLI as `~/projects/sermon-cut/.venv/bin/python -m sermon_cut ...`.

## Arguments

- Positional: a YouTube watch URL, or the word `latest`.
- `--yes` — skip the confirm gate.
- `--speaker "Name"` — override the default preacher (guest weeks).

## Steps

1. **Resolve source.** If the user said `latest`, run `resolve-latest <channel_id>`
   (channel id from `config.toml`) to get the URL.
2. **Pick a run dir.** Use `runs/<video-id>/` under the project. Get the video id
   from the URL (or after fetch-audio). Pass `--workdir runs/<video-id>` to every
   stage.
3. **Fetch audio:** `fetch-audio <url>`.
4. **Transcribe:** `transcribe --audio runs/<id>/audio.m4a`. This can take several
   minutes; tell the user.
5. **Find the sermon (YOUR judgment).** Read `runs/<id>/transcript.json`. Identify
   the single sustained teaching block — the sermon (typically 20–30 min of one
   speaker, after music/announcements). Determine its `start`/`end` (seconds).
   Draft a `title`, a 2–4 sentence `description`, and 3–6 `chapters` (each a
   `{time, label}` within the sermon). Set `speaker` to `--speaker` if given, else
   the config `default_speaker`. Set `date` from the source upload date (in
   `source.info.json` `upload_date`, format `YYYY-MM-DD`). Write all of this to
   `runs/<id>/plan.json`.
6. **Validate:** `validate-plan --plan runs/<id>/plan.json`. If it prints
   `OUT_OF_BAND`, the detected length is suspicious (<10 or >45 min) — do NOT
   honor `--yes`; force the confirm gate and explain why.
7. **Confirm gate** (unless `--yes` and `IN_BAND`): show the user the detected
   start/end (as HH:MM:SS), length, title, speaker, and chapters. Ask them to
   approve or adjust. Apply edits to `plan.json` and re-validate.
8. **Clip the video:** `clip <url> --start <start> --end <end>` (padding from
   config default applies).
9. **Intro card.** For v1, use the configured intro image
   (`assets/intro_card.png` if present). [Canva per-week generation is a future
   seam — see README.] Then `intro --image <path>`.
10. **Assemble:** `assemble --intro runs/<id>/intro.mp4 --sermon runs/<id>/sermon.mp4`.
11. **Upload:** `upload --video runs/<id>/final.mp4 --plan runs/<id>/plan.json`.
    Report the returned `https://youtu.be/...` URL and remind the user it is
    **Unlisted** — they review and publish.

## Rules

- Never set privacy to anything but unlisted (the CLI enforces this).
- Stages are idempotent; on retry, completed artifacts are reused.
- If any stage errors, stop and report; the run dir keeps all artifacts for retry.
````

- [ ] **Step 2: Manually verify the skill file is valid frontmatter**

Run: `.venv/bin/python -c "import re,sys; t=open('.claude/skills/sermon-cut/SKILL.md').read(); assert t.startswith('---'); print('frontmatter ok')"`
Expected: prints `frontmatter ok`

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/sermon-cut/SKILL.md
git commit -m "feat: /sermon-cut orchestration skill"
```

---

### Task 12: README + setup docs + assets dir

**Files:**
- Create: `README.md`
- Create: `assets/.gitkeep`

**Interfaces:** none (documentation).

- [ ] **Step 1: Write `README.md`**

```markdown
# sermon-cut

Extract the sermon from a 2-hour church livestream archive, add an intro card,
and upload it to YouTube as Unlisted for review. Local + free; no cost beyond a
Claude Max subscription.

## How it works

Two passes so the full 2-hour video is never moved:
1. **Discovery** — download audio only, transcribe locally (faster-whisper),
   Claude reads the transcript to find the sermon and draft title/description/
   chapters.
2. **Render** — download only the sermon window (`yt-dlp --download-sections`),
   prepend an intro card, normalize audio, upload Unlisted.

Run it with the `/sermon-cut` skill (URL or `latest`; `--yes` to skip the confirm
gate; `--speaker "Name"` for guest weeks).

## One-time setup

1. **System tools:** `brew install yt-dlp ffmpeg`
2. **Python env:**
   ```bash
   cd ~/projects/sermon-cut
   python3 -m venv .venv
   .venv/bin/python -m pip install -U pip -e .
   ```
3. **Config:** `cp config.example.toml config.toml` and fill in `church_name`,
   `youtube_channel_id`, `default_speaker`, `canva_template_id`,
   `description_footer`.
4. **YouTube upload auth (free):**
   - Create a Google Cloud project; enable **YouTube Data API v3**.
   - Create an **OAuth client ID → Desktop app**; download it as
     `client_secrets.json` into the project root.
   - First upload opens a browser to authorize your channel; the refresh token
     is cached to `token.json`. Daily quota (10,000 units) covers a ~1,600-unit
     weekly upload.
5. **Intro card:** drop `assets/intro_card.png` (1920x1080). Per-week dynamic
   Canva generation is a planned upgrade (see below).

## Running the pipeline manually (without the skill)

Each stage is a CLI subcommand writing into a run dir:
```bash
P=.venv/bin/python
$P -m sermon_cut fetch-audio "https://youtu.be/VIDEOID" --workdir runs/VIDEOID
$P -m sermon_cut transcribe --audio runs/VIDEOID/audio.m4a --workdir runs/VIDEOID
# author runs/VIDEOID/plan.json from the transcript, then:
$P -m sermon_cut validate-plan --plan runs/VIDEOID/plan.json --workdir runs/VIDEOID
$P -m sermon_cut clip "https://youtu.be/VIDEOID" --start 3000 --end 4500 --workdir runs/VIDEOID
$P -m sermon_cut intro --image assets/intro_card.png --workdir runs/VIDEOID
$P -m sermon_cut assemble --intro runs/VIDEOID/intro.mp4 --sermon runs/VIDEOID/sermon.mp4 --workdir runs/VIDEOID
$P -m sermon_cut upload --video runs/VIDEOID/final.mp4 --plan runs/VIDEOID/plan.json --workdir runs/VIDEOID
```

## Future seam: per-week Canva intro card

The `make_intro` stage takes a finished PNG. To generate a per-week card,
add a step before it that fills the Canva brand template
(`canva_template_id`) with the sermon title/speaker/date via the Canva MCP,
exports a PNG to `runs/<id>/intro_card.png`, and passes that to `intro`.
This was intentionally deferred from v1.

## Tests

`.venv/bin/python -m pytest -v` — all stages are unit-tested with injected
fakes; tests never hit the network, a GPU, or real binaries.
```

- [ ] **Step 2: Create assets dir**

```bash
mkdir -p assets && touch assets/.gitkeep
```

- [ ] **Step 3: Update `.gitignore`** to also ignore secrets

Append these lines to `.gitignore`:
```
client_secrets.json
token.json
*.srt
*.json.info
source.info.json
```

- [ ] **Step 4: Commit**

```bash
git add README.md assets/.gitkeep .gitignore
git commit -m "docs: README, setup guide, assets dir, secret ignores"
```

---

## Self-Review notes (for the implementer)

- **Spec coverage:** fetch (T3) → transcribe (T4) → find/validate (T5) →
  clip (T6) → intro (T7) → assemble (T8) → upload (T9) → CLI (T10) →
  skill+confirm gate (T11) → setup/README (T12). Config (T1) and timefmt (T2)
  underpin them. Canva dynamic text is the deferred seam documented in T11/T12,
  matching "iterate on Canva later."
- **Sanity band** (spec: reject <10/>45 min even under `--yes`) is enforced in
  T5 `segment_within_band` and honored by the skill in T11 step 6.
- **Unlisted-only** is enforced by the `VideoMetadata.privacy` default and the
  skill rules; no code path sets Public.
- **Idempotency** (spec: resume from existing artifacts) is in every stage via
  the `out.exists()` guard.
```
