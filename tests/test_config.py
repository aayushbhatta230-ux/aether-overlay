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
