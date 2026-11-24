from pathlib import Path
import textwrap
from sermon_cut.config import load_config, Config, ShortsConfig


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
    assert cfg.whisper_model == "medium"
    assert cfg.outro_seconds == 5
    assert cfg.front_padding_seconds == 1
    assert cfg.back_padding_seconds == 4
    assert cfg.dissolve_seconds == 0.8
    assert cfg.fade_seconds == 0.6


def test_load_config_missing_required_field_raises(tmp_path: Path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('church_name = "Grace Church"\n')
    import pytest
    with pytest.raises(KeyError):
        load_config(cfg_file)


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
