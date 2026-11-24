from pathlib import Path
import json
import sys
import types

from sermon_cut import transcribe as transcribe_mod
from sermon_cut.transcribe import transcribe, TranscriptResult, transcribe_words


class FakeSegment:
    def __init__(self, start, end, text):
        self.start = start
        self.end = end
        self.text = text


class FakeModel:
    def __init__(self, name):
        self.name = name
        self.transcribe_kwargs = None

    def transcribe(self, path, **kwargs):
        self.transcribe_kwargs = kwargs
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


def test_transcribe_uses_vad_and_no_prev_conditioning(tmp_path: Path):
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"fake")
    captured = {}

    def factory(name):
        m = FakeModel(name)
        captured["model"] = m
        return m

    transcribe(audio, tmp_path, model_factory=factory)
    kwargs = captured["model"].transcribe_kwargs
    assert kwargs["vad_filter"] is True
    assert kwargs["condition_on_previous_text"] is False


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


# --- device selection (GPU) ---------------------------------------------------

def test_resolve_device_prefers_cuda_when_available(monkeypatch):
    monkeypatch.delenv("SERMON_CUT_WHISPER_DEVICE", raising=False)
    monkeypatch.delenv("SERMON_CUT_WHISPER_COMPUTE", raising=False)
    assert transcribe_mod.resolve_device(cuda_count=lambda: 1) == ("cuda", "int8_float16")


def test_resolve_device_falls_back_to_cpu_without_cuda(monkeypatch):
    monkeypatch.delenv("SERMON_CUT_WHISPER_DEVICE", raising=False)
    monkeypatch.delenv("SERMON_CUT_WHISPER_COMPUTE", raising=False)
    assert transcribe_mod.resolve_device(cuda_count=lambda: 0) == ("cpu", "int8")


def test_resolve_device_falls_back_to_cpu_when_probe_raises(monkeypatch):
    monkeypatch.delenv("SERMON_CUT_WHISPER_DEVICE", raising=False)
    monkeypatch.delenv("SERMON_CUT_WHISPER_COMPUTE", raising=False)

    def boom():
        raise RuntimeError("no ctranslate2")

    assert transcribe_mod.resolve_device(cuda_count=boom) == ("cpu", "int8")


def test_resolve_device_env_overrides_probe(monkeypatch):
    monkeypatch.setenv("SERMON_CUT_WHISPER_DEVICE", "cpu")
    monkeypatch.delenv("SERMON_CUT_WHISPER_COMPUTE", raising=False)

    def explode():
        raise AssertionError("probe must not run when the device is pinned")

    # An explicit device pins the matching default compute type...
    assert transcribe_mod.resolve_device(cuda_count=explode) == ("cpu", "int8")
    # ...and the compute type is independently overridable.
    monkeypatch.setenv("SERMON_CUT_WHISPER_COMPUTE", "float32")
    assert transcribe_mod.resolve_device(cuda_count=explode) == ("cpu", "float32")


def test_default_model_factory_uses_resolved_device(monkeypatch):
    captured = {}

    class FakeWhisperModel:
        def __init__(self, name, device=None, compute_type=None):
            captured.update(name=name, device=device, compute_type=compute_type)

    monkeypatch.setattr(transcribe_mod, "resolve_device", lambda: ("cuda", "int8_float16"))
    monkeypatch.setitem(sys.modules, "faster_whisper",
                        types.SimpleNamespace(WhisperModel=FakeWhisperModel))

    transcribe_mod._default_model_factory("large-v3")
    assert captured == {"name": "large-v3", "device": "cuda",
                        "compute_type": "int8_float16"}
