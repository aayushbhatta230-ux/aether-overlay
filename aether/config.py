"""Typed configuration for AETHER.

Configuration is layered: built-in defaults, then an optional JSON file
(``%APPDATA%\\AETHER\\config.json`` or the path in ``AETHER_CONFIG``), then
``AETHER_*`` environment variables. Nothing here ever reads or stores secrets.

Every layer is funnelled through :meth:`Config.__post_init__`, which coerces
*all* fields to their declared type and clamps them into range. A config file
or env var can therefore never leave the runtime holding a string where a
float is expected.
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

#: Valid screen corners for the overlay.
CORNERS: tuple[str, ...] = ("bottom-right", "bottom-left", "top-right", "top-left")

#: Fields that may *not* be set from the config file or the environment.
#: ``config_dir`` has to be settled before ``db_path`` is derived from it, so
#: both are resolved in a fixed order inside ``__post_init__``.
_PATH_FIELDS = ("config_dir", "db_path")

_TRUE = ("1", "true", "yes", "on")


def _default_config_dir() -> Path:
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / "AETHER"
    return Path.home() / ".aether"


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in _TRUE


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass(slots=True)
class Config:
    """Runtime configuration. Every field is overridable from the config file."""

    # --- LLM / brain -----------------------------------------------------
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "llama3.2"
    ollama_timeout_s: float = 20.0
    #: When the LLM is unreachable, fall back to deterministic rule-based hints
    #: instead of failing silently. Turning this off means AETHER only ever
    #: speaks when the local model answered.
    offline_fallback: bool = True

    # --- Context engine --------------------------------------------------
    #: How often a fresh context snapshot is taken.
    poll_interval_s: float = 5.0
    #: Do not generate suggestions while the user has been idle this long.
    idle_threshold_s: float = 180.0
    #: Suppress suggestions for a given fingerprint for this long.
    cooldown_s: float = 900.0
    #: Hard cap on suggestions produced per hour, so AETHER can never nag.
    #: ``0`` switches suggestions off entirely; ``-1`` removes the cap.
    max_suggestions_per_hour: int = 8
    #: Track the clipboard via a *hash only* - raw contents never leave RAM.
    track_clipboard: bool = True
    #: Opt-in: read the current git branch of the repo under CWD.
    track_git_branch: bool = True
    #: Nudge after this long in a single application. ``0`` disables the nudge.
    dwell_reminder_s: float = 2700.0

    # --- Overlay ---------------------------------------------------------
    overlay_corner: str = "bottom-right"
    overlay_opacity: float = 0.96
    overlay_timeout_s: float = 12.0
    overlay_always_on_top: bool = True

    # --- Privacy ---------------------------------------------------------
    #: Redact emails, phone numbers, card-like digit runs, API keys and
    #: absolute user paths before anything reaches the LLM.
    redact_pii: bool = True
    #: Show ``medium`` risk suggestions (flagged as needing approval). Turning
    #: this off leaves ``low`` only. ``high`` is *never* displayable.
    allow_medium_risk: bool = True

    # --- Storage ---------------------------------------------------------
    config_dir: Path = field(default_factory=_default_config_dir)
    db_path: Path | None = None
    audit_log: bool = True
    #: How long suggestions, decisions and audit rows are kept. ``0`` keeps
    #: everything forever.
    history_retention_days: int = 30
    #: How long "never show me this again" (snoozed) suggestions stay hidden.
    dismiss_snooze_s: float = 86400.0

    def __post_init__(self) -> None:
        # -- strings ------------------------------------------------------
        self.ollama_host = str(self.ollama_host).rstrip("/")
        self.ollama_model = str(self.ollama_model).strip() or "llama3.2"
        if self.overlay_corner not in CORNERS:
            self.overlay_corner = "bottom-right"

        # -- paths (order matters: db_path defaults under config_dir) -------
        if self.config_dir is not None:
            self.config_dir = Path(self.config_dir).expanduser()
        if self.db_path is None:
            self.db_path = self.config_dir / "aether.db"
        else:
            self.db_path = Path(self.db_path).expanduser()

        # -- booleans ------------------------------------------------------
        self.offline_fallback = _as_bool(self.offline_fallback)
        self.track_clipboard = _as_bool(self.track_clipboard)
        self.track_git_branch = _as_bool(self.track_git_branch)
        self.overlay_always_on_top = _as_bool(self.overlay_always_on_top)
        self.redact_pii = _as_bool(self.redact_pii)
        self.allow_medium_risk = _as_bool(self.allow_medium_risk)
        self.audit_log = _as_bool(self.audit_log)

        # -- floats --------------------------------------------------------
        self.ollama_timeout_s = min(300.0, max(0.5, _as_float(self.ollama_timeout_s, 20.0)))
        self.poll_interval_s = min(3600.0, max(0.5, _as_float(self.poll_interval_s, 5.0)))
        self.idle_threshold_s = max(0.0, _as_float(self.idle_threshold_s, 180.0))
        self.cooldown_s = max(0.0, _as_float(self.cooldown_s, 900.0))
        self.dwell_reminder_s = max(0.0, _as_float(self.dwell_reminder_s, 2700.0))
        self.overlay_opacity = min(1.0, max(0.2, _as_float(self.overlay_opacity, 0.96)))
        self.overlay_timeout_s = min(3600.0, max(1.0, _as_float(self.overlay_timeout_s, 12.0)))
        self.dismiss_snooze_s = max(0.0, _as_float(self.dismiss_snooze_s, 86400.0))

        # -- ints ----------------------------------------------------------
        self.max_suggestions_per_hour = max(-1, _as_int(self.max_suggestions_per_hour, 8))
        self.history_retention_days = max(0, _as_int(self.history_retention_days, 30))

    # -- helpers ---------------------------------------------------------
    @property
    def db_file(self) -> Path:
        """Always a ``Path``; ``db_path`` is populated in ``__post_init__``."""
        assert self.db_path is not None
        return self.db_path

    @property
    def suggestions_enabled(self) -> bool:
        """False when the user has switched suggestions off with a ``0`` budget."""
        return self.max_suggestions_per_hour != 0

    @property
    def rate_capped(self) -> bool:
        """True when an hourly ceiling is in force (``-1`` disables it)."""
        return self.max_suggestions_per_hour > 0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["config_dir"] = str(self.config_dir)
        data["db_path"] = str(self.db_path)
        return data


def _coerce(value: str, current: Any) -> Any:
    """Coerce an environment string to the type of the current value.

    Mirrors the coercions in :meth:`Config.__post_init__`; anything
    unparseable raises and is swallowed by the caller, leaving the default.
    """
    if isinstance(current, bool):
        return _as_bool(value)
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
                # ``utf-8-sig`` so a config written by PowerShell's default
                # encoding (which emits a BOM) still loads.
                raw = json.loads(candidate.read_text(encoding="utf-8-sig"))
            except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
                raise ValueError(f"invalid AETHER config at {candidate}: {exc}") from exc
            if not isinstance(raw, dict):
                raise ValueError(f"AETHER config at {candidate} must be a JSON object")
            known = {f.name for f in fields(Config)}
            unknown = sorted(set(raw) - known)
            if unknown:
                raise ValueError(
                    f"AETHER config at {candidate} has unknown key(s): "
                    f"{', '.join(unknown)}"
                )
            for key, value in raw.items():
                if key not in _PATH_FIELDS:
                    setattr(cfg, key, value)
            # Paths are resolved in a fixed order: a ``config_dir`` override
            # re-homes the default ``db_path``, and an explicit ``db_path``
            # always wins.
            if "config_dir" in raw:
                cfg.config_dir = Path(str(raw["config_dir"]))
                if "db_path" not in raw:
                    cfg.db_path = None
            if "db_path" in raw:
                cfg.db_path = Path(str(raw["db_path"]))
            cfg.__post_init__()

    for f in fields(Config):
        if f.name in _PATH_FIELDS:
            continue
        env_key = f"AETHER_{f.name.upper()}"
        if env_key in os.environ:
            try:
                setattr(cfg, f.name, _coerce(os.environ[env_key], getattr(cfg, f.name)))
            except (TypeError, ValueError):
                continue

    cfg.__post_init__()
    return cfg