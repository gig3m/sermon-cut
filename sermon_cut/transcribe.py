from __future__ import annotations
import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class TranscriptResult:
    json_path: Path
    srt_path: Path
    segments: list[dict]


# Per-device compute types. CUDA uses int8_float16: on Turing (compute 7.5) and
# newer that runs on int8 Tensor Cores — ~11x the CPU path at ~2.7GB VRAM, which
# leaves headroom on an 8GB card shared with Plex's nvdec/nvenc. Pascal had no
# int8 Tensor Cores, so this only pays off from Turing on.
_COMPUTE_BY_DEVICE = {"cuda": "int8_float16", "cpu": "int8"}


def _cuda_device_count() -> int:
    # ctranslate2 is faster-whisper's backend; importing it here (never at module
    # top level) keeps the test suite free of heavy deps.
    import ctranslate2
    return ctranslate2.get_cuda_device_count()


def resolve_device(cuda_count=None) -> tuple[str, str]:
    """Pick (device, compute_type) for WhisperModel.

    Auto-detects CUDA so the same image runs on a GPU-less host. Override with
    SERMON_CUT_WHISPER_DEVICE / SERMON_CUT_WHISPER_COMPUTE.
    """
    device = os.environ.get("SERMON_CUT_WHISPER_DEVICE")
    if not device:
        probe = cuda_count or _cuda_device_count
        try:
            device = "cuda" if probe() > 0 else "cpu"
        except Exception:
            # No ctranslate2 CUDA build, no driver, no card — all mean CPU.
            device = "cpu"
    compute = os.environ.get("SERMON_CUT_WHISPER_COMPUTE") \
        or _COMPUTE_BY_DEVICE.get(device, "int8")
    return device, compute


def _default_model_factory(model_name: str):
    from faster_whisper import WhisperModel
    device, compute_type = resolve_device()
    return WhisperModel(model_name, device=device, compute_type=compute_type)


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
    # vad_filter skips non-speech (music/silence); condition_on_previous_text=False
    # stops a degenerate "." repetition loop on music from propagating through the
    # rest of a long recording (e.g. the worship set before the sermon).
    seg_iter, _info = model.transcribe(
        str(audio_path), vad_filter=True, condition_on_previous_text=False
    )
    segments = [
        {"start": float(s.start), "end": float(s.end), "text": s.text.strip()}
        for s in seg_iter
    ]
    json_path.write_text(json.dumps(segments, indent=2))
    _write_srt(segments, srt_path)
    return TranscriptResult(json_path, srt_path, segments)


def transcribe_words(audio_path: Path, workdir: Path, *, model_name: str = "small",
                     model_factory=None, out_name: str = "words.json") -> Path:
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
