from __future__ import annotations

import subprocess
from pathlib import Path

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


def median_column_center(colsum, rx0, scale):
    """Weighted-median column of a foreground-mask column-sum row, mapped back
    to source-space x. Robust to a small far-off noise cluster because the
    median sits wherever half the pixel mass is, not at the mean."""
    total = sum(colsum)
    if total <= 0:
        return rx0 / scale
    half, run = total / 2.0, 0.0
    for i, v in enumerate(colsum):
        run += v
        if run >= half:
            return (rx0 + i) / scale
    return (rx0 + len(colsum) - 1) / scale


def measure_centers(window_path, clip_start, dur, cfg, *, fps, video_capture=None):
    """Per-frame robust subject center-x (source px) over the tracking window,
    with holds on low-foreground frames and an outlier gate on teleporting
    measurements. Faithful port of `measure()` in experiments/shorts/track2.py,
    generalized off hardcoded constants onto `cfg` and the actual source width.

    Returns (times, centers): times are clip-relative seconds (0 == clip_start),
    centers are source-space x in pixels.
    """
    import cv2
    import numpy as np

    capture_factory = video_capture or cv2.VideoCapture
    cap = capture_factory(str(window_path))

    src_w = cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1920.0
    scale = 640.0 / src_w
    start_f = max(0, int((clip_start - 2.0) * fps))
    cap.set(cv2.CAP_PROP_POS_FRAMES, start_f)

    mog = cv2.createBackgroundSubtractorMOG2(history=200, varThreshold=40,
                                             detectShadows=True)
    rx0, rx1 = int(200 * scale), int(1500 * scale)
    # Track the upper-body band only (cfg.roi_top..roi_bottom in source px), so an
    # extended-arm gesture doesn't drag the measured center off the speaker.
    ry0, ry1 = int(cfg.roi_top * scale), int(cfg.roi_bottom * scale)
    kernel = np.ones((3, 3), np.uint8)

    times, centers = [], []
    last_x, f = 900.0, start_f
    end_f = int((clip_start + dur + 0.2) * fps)
    held = 0
    while f <= end_f:
        ok, frame = cap.read()
        if not ok:
            break
        small = cv2.resize(frame, (640, 360))
        mask = mog.apply(small)
        _, mask = cv2.threshold(mask, 200, 255, cv2.THRESH_BINARY)   # drop shadows
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)        # de-speckle
        roi = mask[ry0:ry1, rx0:rx1]
        colsum = roi.sum(axis=0).astype(np.float64)
        total = colsum.sum()
        meas = None
        if total > cfg.min_fg:
            cand = median_column_center(colsum.tolist(), rx0, scale)
            if abs(cand - last_x) <= cfg.maxjump:   # outlier gate: ignore teleports
                meas = cand
        if meas is None:
            meas = last_x
            held += 1
        last_x = meas
        clip_t = f / fps - clip_start
        if clip_t >= -0.001:
            times.append(clip_t)
            centers.append(last_x)
        f += 1
    cap.release()
    return times, centers


def render_tracked_body(window_path, overlay_path, times, crop_x, cfg, *,
                         clip_start, dur, out_path, fps, runner=subprocess.Popen):
    """OpenCV crop-and-pipe render: reads the tracking window frame by frame,
    crops the subject-following window computed by `smooth_camera` +
    `crop_x_from_center`, resizes to 1080x1920, and streams raw frames into an
    ffmpeg process that overlays the caption track and mixes in window audio.
    Faithful port of `render()` in experiments/shorts/track2.py.

    NOTE: this must stream frames to ffmpeg's stdin incrementally rather than
    buffer the whole raw clip in memory (a 60-90s 1080x1920 bgr24 clip is many
    GB uncompressed), so the ffmpeg process is launched with `subprocess.Popen`
    (which supports piping stdin while the loop is still producing frames) —
    `subprocess.run` cannot do this without materializing the entire buffer
    first. `runner` therefore defaults to `subprocess.Popen`, not
    `subprocess.run`; it stays injectable so a test double can capture the
    launched command without a real ffmpeg/cv2.
    """
    import cv2
    import numpy as np

    out_path = Path(out_path)
    window_path = str(window_path)
    overlay_path = str(overlay_path)

    cap = cv2.VideoCapture(window_path)
    src_w = cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1920.0
    xmax = int(src_w - cfg.crop_w)
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(clip_start * fps)))

    cmd = ["ffmpeg", "-y", "-v", "error",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", "1080x1920", "-r", f"{fps}",
           "-i", "pipe:0",
           "-ss", f"{clip_start:.3f}", "-t", f"{dur:.3f}", "-i", window_path,
           "-i", overlay_path,
           "-filter_complex", "[0:v][2:v]overlay=0:0:format=auto[out]",
           "-map", "[out]", "-map", "1:a",
           "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "160k", str(out_path)]
    proc = runner(cmd, stdin=subprocess.PIPE)

    n_out = int(round(dur * fps))
    for i in range(n_out):
        ok, frame = cap.read()
        if not ok:
            break
        x = int(round(float(np.interp(i / fps, times, crop_x))))
        x = max(0, min(x, xmax))
        win = frame[cfg.crop_y:cfg.crop_y + cfg.crop_h, x:x + cfg.crop_w]
        proc.stdin.write(cv2.resize(win, (1080, 1920),
                                    interpolation=cv2.INTER_CUBIC).tobytes())
    cap.release()
    proc.stdin.close()
    proc.wait()
    return out_path
