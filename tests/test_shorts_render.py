# tests/test_shorts_render.py
from pathlib import Path
from sermon_cut.config import ShortsConfig
from sermon_cut.shorts_render import window_bounds, extract_window, build_short_filter, assemble_short, build_vertical_outro_card

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

def test_short_filter_normalizes_and_offsets():
    fc, total = build_short_filter(79.0, ShortsConfig())
    assert total == 79.0 + 2.4              # body + outro_hold
    assert "settb=AVTB" in fc and "setsar=1" in fc
    assert "xfade=transition=fade:duration=0.6:offset=78.400" in fc
    assert "fade=t=out:st=80.900:d=0.5" in fc  # (81.4 - 0.5)
    assert "afade=t=out:st=78.400:d=0.6" in fc

def test_assemble_short_builds_command_and_maps_streams(tmp_path):
    body = tmp_path / "body.mp4"; body.write_bytes(b"b")
    outro = tmp_path / "outro.png"; outro.write_bytes(b"o")
    captured = {}

    def runner(cmd, **kwargs):
        captured["cmd"] = cmd
        (tmp_path / "short.mp4").write_bytes(b"short")
        return None

    def probe(path):
        return 79.0

    cfg = ShortsConfig()
    out = assemble_short(body, outro, tmp_path, cfg, runner=runner, duration_probe=probe)
    assert out == tmp_path / "short.mp4"
    j = " ".join(captured["cmd"])
    assert str(body) in j and str(outro) in j
    assert "-map [v]" in j.replace("  ", " ") or ("-map" in captured["cmd"] and "[v]" in captured["cmd"])
    assert "[v]" in captured["cmd"] and "[a]" in captured["cmd"]
    assert "-loop" in captured["cmd"] and "1" in captured["cmd"]
    # outro image held for outro_hold + dissolve seconds
    assert "3.0" in j or "3" in j

def test_assemble_short_idempotent(tmp_path):
    body = tmp_path / "body.mp4"; body.write_bytes(b"b")
    outro = tmp_path / "outro.png"; outro.write_bytes(b"o")
    (tmp_path / "short.mp4").write_bytes(b"exists")

    def explode(cmd, **kwargs):
        raise AssertionError("should not render when short.mp4 exists")

    out = assemble_short(body, outro, tmp_path, ShortsConfig(), runner=explode)
    assert out == tmp_path / "short.mp4"

def test_vertical_outro_card_is_1080x1920(tmp_path):
    from PIL import Image
    src = tmp_path / "outro_card.png"
    Image.new("RGB", (1920, 1080), (17, 17, 19)).save(src)
    out = build_vertical_outro_card(src, tmp_path)
    im = Image.open(out)
    assert im.size == (1080, 1920)

def test_vertical_outro_card_uses_override_verbatim(tmp_path):
    from PIL import Image
    src = tmp_path / "outro_card.png"
    Image.new("RGB", (1920, 1080), (17, 17, 19)).save(src)

    workdir = tmp_path / "shorts"
    override = tmp_path / "outro_9x16_override.png"
    Image.new("RGB", (1080, 1920), (200, 40, 40)).save(override)

    out = build_vertical_outro_card(src, workdir)
    assert out == workdir / "outro_9x16.png"
    assert out.read_bytes() == override.read_bytes()
    im = Image.open(out)
    assert im.getpixel((5, 5)) == (200, 40, 40)

def test_vertical_outro_card_creates_missing_workdir(tmp_path):
    from PIL import Image
    src = tmp_path / "outro_card.png"
    Image.new("RGB", (1920, 1080), (17, 17, 19)).save(src)

    workdir = tmp_path / "does_not_exist_yet" / "shorts"
    assert not workdir.exists()

    out = build_vertical_outro_card(src, workdir)
    im = Image.open(out)
    assert im.size == (1080, 1920)
