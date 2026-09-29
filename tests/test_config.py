"""Config loading and validation."""

from __future__ import annotations

import json

import pytest

from aether.config import Config, load_config


def test_defaults_are_sane() -> None:
    cfg = Config()
    assert cfg.poll_interval_s >= 0.5
    assert 0.2 <= cfg.overlay_opacity <= 1.0
    assert cfg.db_file.suffix == ".db"


def test_opacity_is_clamped() -> None:
    cfg = Config()
    cfg.overlay_opacity = 5.0
    cfg.__post_init__()
    assert cfg.overlay_opacity == 1.0


def test_unknown_corner_falls_back() -> None:
    cfg = Config()
    cfg.overlay_corner = "middle-of-nowhere"
    cfg.__post_init__()
    assert cfg.overlay_corner == "bottom-right"


def test_loads_json_file(tmp_path) -> None:
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps({"ollama_model": "mistral", "max_suggestions_per_hour": 3}))
    cfg = load_config(path)
    assert cfg.ollama_model == "mistral"
    assert cfg.max_suggestions_per_hour == 3


def test_env_overrides_file(tmp_path, monkeypatch) -> None:
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps({"ollama_model": "mistral"}))
    monkeypatch.setenv("AETHER_OLLAMA_MODEL", "phi3")
    assert load_config(path).ollama_model == "phi3"


def test_env_bool_coercion(monkeypatch) -> None:
    monkeypatch.setenv("AETHER_TRACK_CLIPBOARD", "false")
    assert load_config().track_clipboard is False


def test_invalid_json_raises_clear_error(tmp_path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{not json")
    with pytest.raises(ValueError, match="invalid AETHER config"):
        load_config(path)


def test_non_object_json_rejected(tmp_path) -> None:
    path = tmp_path / "list.json"
    path.write_text("[1, 2, 3]")
    with pytest.raises(ValueError, match="must be a JSON object"):
        load_config(path)


def test_unknown_key_is_rejected_with_the_name(tmp_path) -> None:
    """A typo must fail loudly instead of being silently ignored."""
    path = tmp_path / "typo.json"
    path.write_text(json.dumps({"cooldown": 60}))
    with pytest.raises(ValueError, match="unknown key"):
        load_config(path)


# --- coercion: a config file must never leave a str where a float belongs ---


def test_string_numbers_from_json_are_coerced(tmp_path) -> None:
    path = tmp_path / "strs.json"
    path.write_text(json.dumps({
        "poll_interval_s": "2.5",
        "cooldown_s": "120",
        "idle_threshold_s": "60",
        "overlay_timeout_s": "8",
        "ollama_timeout_s": "11",
        "dwell_reminder_s": "600",
        "max_suggestions_per_hour": "5",
        "history_retention_days": "7",
    }))
    cfg = load_config(path)
    assert cfg.poll_interval_s == 2.5
    assert cfg.cooldown_s == 120.0
    assert isinstance(cfg.cooldown_s, float)
    assert cfg.max_suggestions_per_hour == 5
    assert cfg.history_retention_days == 7


def test_string_booleans_from_json_are_coerced(tmp_path) -> None:
    path = tmp_path / "bools.json"
    path.write_text(json.dumps({"redact_pii": "false", "track_clipboard": "no"}))
    cfg = load_config(path)
    assert cfg.redact_pii is False
    assert cfg.track_clipboard is False


def test_unparseable_number_falls_back_to_default(tmp_path) -> None:
    path = tmp_path / "junk.json"
    path.write_text(json.dumps({"cooldown_s": "soon"}))
    assert load_config(path).cooldown_s == Config().cooldown_s


def test_env_coerces_every_scalar_kind(monkeypatch) -> None:
    monkeypatch.setenv("AETHER_COOLDOWN_S", "42")
    monkeypatch.setenv("AETHER_MAX_SUGGESTIONS_PER_HOUR", "3")
    monkeypatch.setenv("AETHER_REDACT_PII", "0")
    cfg = load_config()
    assert cfg.cooldown_s == 42.0
    assert cfg.max_suggestions_per_hour == 3
    assert cfg.redact_pii is False


def test_env_value_that_cannot_be_parsed_is_ignored(monkeypatch) -> None:
    monkeypatch.setenv("AETHER_COOLDOWN_S", "soon")
    assert load_config().cooldown_s == Config().cooldown_s


def test_db_path_can_be_set_from_the_config_file(tmp_path) -> None:
    target = tmp_path / "nested" / "custom.db"
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps({"db_path": str(target)}))
    assert load_config(path).db_file == target


def test_config_dir_override_rehomes_the_default_db(tmp_path) -> None:
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps({"config_dir": str(tmp_path / "elsewhere")}))
    cfg = load_config(path)
    assert cfg.config_dir == tmp_path / "elsewhere"
    assert cfg.db_file == tmp_path / "elsewhere" / "aether.db"


def test_budget_semantics() -> None:
    assert Config(max_suggestions_per_hour=0).suggestions_enabled is False
    assert Config(max_suggestions_per_hour=0).rate_capped is False
    assert Config(max_suggestions_per_hour=-1).suggestions_enabled is True
    assert Config(max_suggestions_per_hour=-1).rate_capped is False
    assert Config(max_suggestions_per_hour=8).rate_capped is True


def test_negative_budget_clamps_to_minus_one() -> None:
    assert Config(max_suggestions_per_hour=-99).max_suggestions_per_hour == -1


def test_out_of_range_floats_are_clamped() -> None:
    cfg = Config(poll_interval_s=0.0, overlay_timeout_s=0.0, dwell_reminder_s=-5.0)
    cfg.__post_init__()
    assert cfg.poll_interval_s == 0.5
    assert cfg.overlay_timeout_s == 1.0
    assert cfg.dwell_reminder_s == 0.0


def test_ollama_host_is_normalised() -> None:
    assert Config(ollama_host="http://localhost:11434/").ollama_host == "http://localhost:11434"


def test_config_written_by_powershell_loads(tmp_path) -> None:
    """PowerShell's default encoding writes a BOM; it must still load."""
    path = tmp_path / "bom.json"
    path.write_text('{"ollama_model": "phi3"}', encoding="utf-8-sig")
    assert load_config(path).ollama_model == "phi3"
