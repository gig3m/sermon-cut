from __future__ import annotations

import subprocess
from pathlib import Path

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


def build_caption_timeline(chunks, clip_start, dur):
    """Pure function: returns [(start, end, state), ...] spans covering [0, dur].

    state is (chunk_idx, active_word_idx) while a word is active, or None
    during a blank (no caption) gap.
    """
    tl, prev_end = [], 0.0
    for ci, ch in enumerate(chunks):
        ch_start = ch[0]["start"] - clip_start
        ch_end = ((chunks[ci + 1][0]["start"] - clip_start) if ci + 1 < len(chunks)
                  else (ch[-1]["end"] - clip_start + 0.5))
        if ch_start > prev_end + 0.02:
            tl.append((prev_end, ch_start, None))
        for wi, w in enumerate(ch):
            ws = w["start"] - clip_start
            we = ((ch[wi + 1]["start"] - clip_start) if wi + 1 < len(ch) else ch_end)
            tl.append((max(ws, ch_start), we, (ci, wi)))
        prev_end = ch_end
    if prev_end < dur:
        tl.append((prev_end, dur, None))

    # Clamp to [0, dur]: the tracking window carries pad on both sides (words
    # before the clip start have negative times; words after the clip end run
    # past dur). Without this the caption overlay spans the whole padded window,
    # which desyncs it from the -ss/-t-trimmed audio and (via overlay's default
    # eof_action=repeat) freezes the last video frame and stretches the clip to
    # window length. Drop out-of-range spans, clip straddling ones to the bounds.
    clamped = []
    for s, e, state in tl:
        s2, e2 = max(0.0, s), min(dur, e)
        if e2 - s2 <= 0.001:
            continue
        # A word that began before the clip (s < 0, i.e. mid-word at the pad
        # boundary) shouldn't flash its pad text at t=0 — show a blank there.
        st = state if (state is None or s >= -0.001) else None
        clamped.append((s2, e2, st))
    if not clamped:
        return [(0.0, dur, None)]
    if clamped[0][0] > 0.001:                       # ensure coverage from 0
        clamped.insert(0, (0.0, clamped[0][0], None))
    return clamped


def _wrap_tokens(d, toks, font, maxw):
    lines, line = [], []
    for tk in toks:
        trial = line + [tk]
        if d.textlength(" ".join(t[0] for t in trial), font=font) <= maxw or not line:
            line = trial
        else:
            lines.append(line); line = [tk]
    if line:
        lines.append(line)
    return lines


def _render_state(png, chunk, active_idx, font, cfg, W, H):
    """Draw one karaoke caption state (a chunk with word `active_idx` highlighted)
    to `png`: upper-cased word tokens wrapped to <=900px, a rounded translucent
    scrim plate behind them, white text with a black outline, and the active
    word in cfg.amber. Ported from experiments/shorts/build4.py:render_caption.
    """
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    toks = [(w["word"].strip().upper().strip(".,?!;:"), i)
            for i, w in enumerate(chunk)]
    lines = _wrap_tokens(d, toks, font, 900)
    lh = font.getbbox("Ay")[3] + 18
    total = lh * len(lines)
    y0 = cfg.cap_y - total // 2
    widths = [d.textlength(" ".join(t[0] for t in ln), font=font) for ln in lines]
    mw = max(widths)
    padx, pady = 46, 28
    x0 = (W - mw) / 2 - padx
    x1 = (W + mw) / 2 + padx
    d.rounded_rectangle([x0, y0 - pady, x1, y0 + total + pady - 8],
                        radius=34, fill=(0, 0, 0, 150))
    white = (255, 255, 255, 255)
    amber = tuple(cfg.amber) if len(cfg.amber) == 4 else (*cfg.amber, 255)
    y = y0
    space = d.textlength(" ", font=font)
    for li, line in enumerate(lines):
        x = (W - widths[li]) / 2
        for tok, gidx in line:
            col = amber if gidx == active_idx else white
            d.text((x, y), tok, font=font, fill=col,
                   stroke_width=4, stroke_fill=(0, 0, 0, 255))
            x += d.textlength(tok, font=font) + space
        y += lh
    img.save(png)


def render_overlay(words, workdir, cfg, *, clip_start, dur,
                   font_dir=Path("assets/fonts"), runner=subprocess.run,
                   out_name="overlay.mov"):
    """Render per-caption-state scrim PNGs and concat them into a qtrle alpha
    overlay.mov via ffmpeg (run through the injectable `runner`). Idempotent:
    returns immediately if `out_name` already exists.
    """
    from PIL import Image, ImageFont

    workdir = Path(workdir); out = workdir / out_name
    if out.exists():
        return out
    cap_dir = workdir / "caps"; cap_dir.mkdir(parents=True, exist_ok=True)
    W, H = 1080, 1920
    font = ImageFont.truetype(str(font_dir / "Nunito.ttf"), cfg.caption_font_size)
    font.set_variation_by_name("Bold")
    Image.new("RGBA", (W, H), (0, 0, 0, 0)).save(cap_dir / "blank.png")
    chunks = chunk_words(words)
    timeline = build_caption_timeline(chunks, clip_start, dur)
    lines = []
    last_png = cap_dir / "blank.png"
    for s, e, state in timeline:
        png = cap_dir / ("blank.png" if state is None
                         else f"s{state[0]:03d}_{state[1]:02d}.png")
        if state is not None and not png.exists():
            _render_state(png, chunks[state[0]], state[1], font, cfg, W, H)
        # Absolute path: the concat demuxer resolves a relative `file` entry
        # against concat.txt's own directory (it's already inside cap_dir),
        # so a cwd-relative path here would get cap_dir's prefix doubled.
        lines.append(f"file '{png.resolve()}'\nduration {max(0.05, e - s):.3f}")
        last_png = png
    # the ffmpeg concat demuxer needs the last file repeated once (no duration)
    # to mark the end of the final segment.
    lines.append(f"file '{last_png.resolve()}'")
    (cap_dir / "concat.txt").write_text("\n".join(lines))
    runner(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
            "-i", str(cap_dir / "concat.txt"), "-r", "30", "-c:v", "qtrle",
            "-pix_fmt", "argb", str(out)], check=True)
    return out
