from pathlib import Path

from sermon_cut.shorts_captions import chunk_words, build_caption_timeline, render_overlay
from sermon_cut.config import ShortsConfig

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

def test_timeline_covers_clip_and_marks_active_word():
    words = [{"start": 10.0, "end": 10.4, "word": "Where"},
             {"start": 10.4, "end": 10.8, "word": "do"}]
    chunks = chunk_words(words)
    tl = build_caption_timeline(chunks, clip_start=10.0, dur=2.0)
    assert tl[0][0] == 0.0                      # starts at clip 0
    assert abs(tl[-1][1] - 2.0) < 1e-6          # ends at dur
    active = [s for s in tl if s[2] is not None]
    assert active[0][2] == (0, 0)               # first word of first chunk


def test_timeline_clamps_pad_words_outside_clip_span():
    # The tracking window carries pad on both sides: words before the clip start
    # (negative time) and after clip_start+dur must be dropped so the caption
    # overlay is exactly dur long and stays synced with the -ss/-t audio.
    words = [
        {"start": 8.0, "end": 8.4, "word": "PAD"},      # before clip_start -> negative
        {"start": 10.2, "end": 10.6, "word": "hook"},   # inside
        {"start": 11.0, "end": 11.4, "word": "point"},  # inside
        {"start": 13.5, "end": 13.9, "word": "tail"},   # after clip_start+dur (12.0)
    ]
    chunks = chunk_words(words)
    tl = build_caption_timeline(chunks, clip_start=10.0, dur=2.0)
    assert tl[0][0] == 0.0                       # coverage starts at 0
    assert all(0.0 <= s <= 2.0 and 0.0 <= e <= 2.0 for s, e, _ in tl)  # in-bounds
    labels = {chunks[st[0]][st[1]]["word"] for s, e, st in tl if st is not None}
    assert "PAD" not in labels and "tail" not in labels   # pad words excluded
    assert "hook" in labels


def test_render_overlay_writes_state_pngs_and_calls_ffmpeg(tmp_path):
    calls = []
    def fake_runner(cmd, **kw):
        calls.append(cmd)
        class R: returncode = 0
        return R()

    words = [{"start": 10.0, "end": 10.4, "word": "Where"},
              {"start": 10.4, "end": 10.8, "word": "do"},
              {"start": 10.8, "end": 11.2, "word": "we"},
              {"start": 11.2, "end": 11.6, "word": "go?"}]
    cfg = ShortsConfig()
    out = render_overlay(words, tmp_path, cfg, clip_start=10.0, dur=2.0,
                          font_dir=Path("assets/fonts"), runner=fake_runner)

    assert out == tmp_path / "overlay.mov"
    caps = list((tmp_path / "caps").glob("*.png"))
    assert any(p.name == "blank.png" for p in caps)
    assert any(p.name.startswith("s000_") for p in caps)
    assert (tmp_path / "caps" / "concat.txt").exists()
    assert len(calls) == 1
    assert calls[0][0] == "ffmpeg"
    assert "concat.txt" in calls[0][calls[0].index("-i") + 1]


def test_render_overlay_is_idempotent(tmp_path):
    calls = []
    def fake_runner(cmd, **kw):
        calls.append(cmd)

    out_dir = tmp_path
    (out_dir / "overlay.mov").touch()
    words = [{"start": 0.0, "end": 0.4, "word": "Hi"}]
    cfg = ShortsConfig()
    out = render_overlay(words, out_dir, cfg, clip_start=0.0, dur=1.0,
                          font_dir=Path("assets/fonts"), runner=fake_runner)
    assert out == out_dir / "overlay.mov"
    assert calls == []


def test_render_overlay_concat_entries_are_absolute_for_relative_nested_workdir(tmp_path, monkeypatch):
    # Regression: concat.txt entries must be absolute paths. The ffmpeg concat
    # demuxer resolves a relative `file` entry against concat.txt's own
    # directory, so a cwd-relative path would get cap_dir's prefix doubled
    # once the process's cwd differs from where the workdir was constructed
    # relative to. Run from tmp_path and pass a relative, multi-segment
    # workdir to make sure this can't silently regress.
    font_dir = Path("assets/fonts").resolve()
    monkeypatch.chdir(tmp_path)

    calls = []
    def fake_runner(cmd, **kw):
        calls.append(cmd)
        class R: returncode = 0
        return R()

    workdir = Path("runs/x/shorts/slug")
    words = [{"start": 10.0, "end": 10.4, "word": "Where"},
             {"start": 10.4, "end": 10.8, "word": "do"},
             {"start": 10.8, "end": 11.2, "word": "we"},
             {"start": 11.2, "end": 11.6, "word": "go?"}]
    cfg = ShortsConfig()
    render_overlay(words, workdir, cfg, clip_start=10.0, dur=2.0,
                    font_dir=font_dir, runner=fake_runner)

    concat_text = (workdir / "caps" / "concat.txt").read_text()
    file_lines = [line for line in concat_text.splitlines() if line.startswith("file '")]
    assert file_lines, "expected at least one concat file entry"
    for line in file_lines:
        entry = line[len("file '"):-1]
        assert entry.startswith("/"), f"concat.txt entry is not absolute: {entry!r}"
