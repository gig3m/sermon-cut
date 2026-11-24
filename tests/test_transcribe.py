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
    monkeypatch.delenv("SERMON_CUT_OLLAMA_URL", raising=False)  # stay offline
    monkeypatch.setitem(sys.modules, "faster_whisper",
                        types.SimpleNamespace(WhisperModel=FakeWhisperModel))

    transcribe_mod._default_model_factory("large-v3")
    assert captured == {"name": "large-v3", "device": "cuda",
                        "compute_type": "int8_float16"}


# --- freeing Ollama's VRAM before whisper loads on the GPU -------------------

class FakeOllama:
    """Fake Ollama HTTP: unload removes a model from /api/ps, except a busy one,
    which stays listed for `busy_polls` more /api/ps calls (an in-flight request)."""
    def __init__(self, loaded, busy=None, fail=False, fail_unload=()):
        self.loaded = list(loaded)
        self.busy = dict(busy or {})
        self.fail = fail
        self.fail_unload = set(fail_unload)
        self.expiring = set()
        self.calls = []

    def __call__(self, url, body=None):
        self.calls.append((url, body))
        if self.fail:
            raise OSError("connection refused")
        if url.endswith("/api/ps"):
            for n in list(self.expiring):
                if self.busy.get(n, 0) > 0:
                    self.busy[n] -= 1
                else:
                    self.loaded.remove(n); self.expiring.discard(n)
            return {"models": [{"name": n} for n in self.loaded]}
        name = body["model"]
        if name in self.fail_unload:
            raise OSError("HTTP 400")
        self.expiring.add(name)
        return {"done_reason": "unload"}


def test_free_ollama_vram_unloads_every_loaded_model():
    http = FakeOllama(["gemma4:12b", "qwen3-coder:30b"])
    unloaded = transcribe_mod.free_ollama_vram("http://ollama:11434/", http=http,
                                               sleep=lambda s: None)
    assert unloaded == ["gemma4:12b", "qwen3-coder:30b"]
    assert http.calls[0] == ("http://ollama:11434/api/ps", None)
    assert http.calls[1:3] == [
        ("http://ollama:11434/api/generate", {"model": "gemma4:12b", "keep_alive": 0}),
        ("http://ollama:11434/api/generate", {"model": "qwen3-coder:30b", "keep_alive": 0}),
    ]
    assert http.loaded == []


def test_free_ollama_vram_noop_when_nothing_loaded():
    http = FakeOllama([])
    assert transcribe_mod.free_ollama_vram("http://ollama:11434", http=http) == []
    assert len(http.calls) == 1


def test_free_ollama_vram_never_raises_when_ollama_is_down():
    http = FakeOllama([], fail=True)
    assert transcribe_mod.free_ollama_vram("http://ollama:11434", http=http) == []


def test_free_ollama_vram_waits_for_a_busy_model_to_leave():
    # A busy runner is acknowledged at once but stays resident until its request ends.
    http = FakeOllama(["gemma4:12b"], busy={"gemma4:12b": 3})
    slept = []
    transcribe_mod.free_ollama_vram("http://ollama:11434", http=http, sleep=slept.append,
                                    poll_seconds=2)
    assert http.loaded == []
    assert slept == [2, 2, 2]


class FakeClock:
    def __init__(self):
        self.t = 0.0
    def __call__(self):
        return self.t
    def sleep(self, s):
        self.t += s


def test_free_ollama_vram_gives_up_at_the_deadline():
    http = FakeOllama(["gemma4:12b"], busy={"gemma4:12b": 1000})
    clock = FakeClock()
    unloaded = transcribe_mod.free_ollama_vram("http://ollama:11434", http=http,
                                               sleep=clock.sleep, clock=clock,
                                               wait_seconds=10, poll_seconds=2)
    assert unloaded == ["gemma4:12b"]
    assert clock.t == 10


def test_free_ollama_vram_deadline_counts_slow_polls():
    # Each /api/ps takes 9s: the 120s budget must be wall-clock, not just sleeps.
    clock = FakeClock()
    inner = FakeOllama(["gemma4:12b"], busy={"gemma4:12b": 1000})
    def slow_http(url, body=None):
        if url.endswith("/api/ps"):
            clock.t += 9
        return inner(url, body)
    transcribe_mod.free_ollama_vram("http://ollama:11434", http=slow_http,
                                    sleep=clock.sleep, clock=clock,
                                    wait_seconds=120, poll_seconds=2)
    # initial /api/ps (9) + 120s budget + at most one poll in flight at the deadline (9)
    assert clock.t <= 9 + 120 + 9


def test_free_ollama_vram_one_failed_unload_does_not_stop_the_rest():
    http = FakeOllama(["embed:latest", "gemma4:12b"], fail_unload={"embed:latest"})
    unloaded = transcribe_mod.free_ollama_vram("http://ollama:11434", http=http,
                                               sleep=lambda s: None)
    assert unloaded == ["gemma4:12b"]
    assert http.loaded == ["embed:latest"]


def test_factory_frees_ollama_only_on_cuda_when_url_set(monkeypatch):
    freed = []
    monkeypatch.setattr(transcribe_mod, "free_ollama_vram", lambda url: freed.append(url) or [])
    fake_fw = types.ModuleType("faster_whisper")
    fake_fw.WhisperModel = lambda name, device, compute_type: (name, device)
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_fw)

    monkeypatch.setenv("SERMON_CUT_OLLAMA_URL", "http://ollama:11434")
    monkeypatch.setenv("SERMON_CUT_WHISPER_DEVICE", "cpu")
    transcribe_mod._default_model_factory("medium")
    assert freed == []

    monkeypatch.setenv("SERMON_CUT_WHISPER_DEVICE", "cuda")
    transcribe_mod._default_model_factory("medium")
    assert freed == ["http://ollama:11434"]

    monkeypatch.delenv("SERMON_CUT_OLLAMA_URL")
    transcribe_mod._default_model_factory("medium")
    assert freed == ["http://ollama:11434"]
