"""Typed configuration for AETHER.

Configuration is layered: built-in defaults, then an optional JSON file
(``%APPDATA%\\AETHER\\config.json`` or the path in ``AETHER_CONFIG``), then
``AETHER_*`` environment variables. Nothing here ever reads or stores secrets.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

#: Risk tiers, ordered from harmless to dangerous.
RISK_ORDER: tuple[str, ...] = ("low", "medium", "high")

#: Tiers that require an explicit human approval before they are ever shown as
#: actionable. ``medium`` and above are never auto-executed by design.
GATED_RISKS: frozenset[str] = frozenset({"medium", "high"})


def _default_config_dir() -> Path:
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / "AETHER"
    return Path.home() / ".aether"


@dataclass(slots=True)
class Config:
    """Runtime configuration. Every field is overridable from the config file."""

    # --- LLM / brain -----------------------------------------------------
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "llama3.2"
    ollama_timeout_s: float = 20.0
    #: When the LLM is unreachable, fall back to deterministic rule-based hints
    #: instead of failing silently.
    offline_fallback: bool = True

    # --- Context engine --------------------------------------------------
    #: How often a fresh context snapshot is taken.
    poll_interval_s: float = 5.0
    #: Do not generate suggestions while the user has been idle this long.
    idle_threshold_s: float = 180.0
    #: Suppress suggestions for a given fingerprint for this long.
    cooldown_s: float = 900.0
    #: Hard cap on suggestions produced per hour, so AETHER can never nag.
    max_suggestions_per_hour: int = 8
    #: Track the clipboard via a *hash only* - raw contents never leave RAM.
    track_clipboard: bool = True
    #: Opt-in: read the current git branch of the repo under CWD.
    track_git_branch: bool = True

    # --- Overlay ---------------------------------------------------------
    overlay_corner: str = "bottom-right"
    overlay_opacity: float = 0.96
    overlay_timeout_s: float = 12.0
    overlay_always_on_top: bool = True

    # --- Privacy ---------------------------------------------------------
    #: Redact emails, phone numbers, card-like digit runs, API keys and
    #: absolute user paths before anything reaches the LLM.
    redact_pii: bool = True

    # --- Storage ---------------------------------------------------------
    config_dir: Path = field(default_factory=_default_config_dir)
    db_path: Path | None = None
    audit_log: bool = True

    def __post_init__(self) -> None:
        if self.db_path is None:
            self.db_path = self.config_dir / "aether.db"
        else:
            self.db_path = Path(self.db_path)
        self.config_dir = Path(self.config_dir)

        self.overlay_opacity = min(1.0, max(0.2, float(self.overlay_opacity)))
        self.poll_interval_s = max(0.5, float(self.poll_interval_s))
        self.max_suggestions_per_hour = max(0, int(self.max_suggestions_per_hour))
        if self.overlay_corner not in ("bottom-right", "bottom-left", "top-right", "top-left"):
            self.overlay_corner = "bottom-right"

    # -- helpers ---------------------------------------------------------
    @property
    def db_file(self) -> Path:
        """Always a ``Path``; ``db_path`` is populated in ``__post_init__``."""
        assert self.db_path is not None
        return self.db_path

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["config_dir"] = str(self.config_dir)
        data["db_path"] = str(self.db_path)
        return data


def _coerce(value: str, current: Any) -> Any:
    """Coerce an environment string to the type of the current value."""
    if isinstance(current, bool):
        return value.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(current, float):
        return float(value)
    if isinstance(current, int):
        return int(value)
    return value


def load_config(path: str | os.PathLike[str] | None = None) -> Config:
    """Build a :class:`Config` from defaults + optional JSON file + env vars."""
    cfg = Config()

    cfg_path = path or os.environ.get("AETHER_CONFIG")
    if cfg_path:
        candidate = Path(cfg_path).expanduser()
        if candidate.is_file():
            try:
                raw = json.loads(candidate.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                raise ValueError(f"invalid AETHER config at {candidate}: {exc}") from exc
            if not isinstance(raw, dict):
                raise ValueError(f"AETHER config at {candidate} must be a JSON object")
            for key, value in raw.items():
                if hasattr(cfg, key) and key not in ("config_dir", "db_path"):
                    setattr(cfg, key, value)
            cfg.__post_init__()

    field_names = [f.name for f in fields(Config)]
    for key in field_names:
        if key in ("config_dir", "db_path"):
            continue
        env_key = f"AETHER_{key.upper()}"
        if env_key in os.environ:
            try:
                setattr(cfg, key, _coerce(os.environ[env_key], getattr(cfg, key)))
            except (TypeError, ValueError):
                continue

    cfg.__post_init__()
    return cfg
