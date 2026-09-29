"""Shared fixtures: everything runs against fakes, never a real desktop."""

from __future__ import annotations

import pytest

from aether.config import Config
from aether.context import Probes
from aether.models import ContextSnapshot
from aether.vault import Vault


@pytest.fixture
def config(tmp_path) -> Config:
    """A fast, isolated config pointed at a throwaway database."""
    cfg = Config()
    cfg.config_dir = tmp_path
    cfg.db_path = tmp_path / "test.db"
    cfg.poll_interval_s = 0.01
    cfg.cooldown_s = 60.0
    cfg.max_suggestions_per_hour = 100
    cfg.__post_init__()
    return cfg


@pytest.fixture
def vault(config) -> Vault:
    return Vault(config.db_file)


@pytest.fixture
def probes() -> Probes:
    """Deterministic probes: a fixed foreground window, no clipboard churn."""
    return Probes(
        active_window=lambda: ("Code.exe", "aether\\app.py - Visual Studio Code"),
        idle_seconds=lambda: 0.0,
        clipboard=lambda: "hello world",
        git_branch=lambda _cwd: "feat/overlay",
    )


@pytest.fixture
def snapshot() -> ContextSnapshot:
    return ContextSnapshot(
        active_app="Code.exe",
        window_title="app.py - Visual Studio Code",
        idle_seconds=0.0,
        git_branch="feat/overlay",
    )
