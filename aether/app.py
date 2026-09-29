"""The orchestrator: capture -> generate -> gate -> dedupe -> display.

This is the only module that knows the whole flow. The pieces it drives
(context, LLM, safety, vault) are all independently testable, and the
orchestrator itself is testable by injecting fakes for each of them.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .config import Config
from .context import ContextEngine, Probes
from .llm import SuggestionEngine
from .models import Suggestion
from .safety import filter_suggestions
from .vault import Vault

log = logging.getLogger(__name__)

#: Injected sink for a suggestion that passed the gate.
Presenter = Callable[[Suggestion], None]


@dataclass(slots=True)
class Stats:
    cycles: int = 0
    displayed: int = 0
    suppressed_cooldown: int = 0
    suppressed_idle: int = 0
    suppressed_rate: int = 0
    suppressed_risk: int = 0
    errors: int = 0
    by_source: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "cycles": self.cycles,
            "displayed": self.displayed,
            "suppressed_cooldown": self.suppressed_cooldown,
            "suppressed_idle": self.suppressed_idle,
            "suppressed_rate": self.suppressed_rate,
            "suppressed_risk": self.suppressed_risk,
            "errors": self.errors,
            "by_source": dict(self.by_source),
        }


class Aether:
    """AETHER's runtime. Owns the polling loop and the suppression policy."""

    def __init__(
        self,
        config: Config,
        context_engine: ContextEngine | None = None,
        suggestion_engine: SuggestionEngine | None = None,
        vault: Vault | None = None,
        presenter: Presenter | None = None,
        probes: Probes | None = None,
    ) -> None:
        self.config = config
        self.vault = vault or Vault(config.db_file)
        self.context = context_engine or ContextEngine(config, probes)
        self.suggestions = suggestion_engine or SuggestionEngine(config)
        self.presenter = presenter or (lambda s: log.info("%s", s.title))
        self.stats = Stats()
        self._stop = threading.Event()

    # -- one cycle -------------------------------------------------------
    def tick(self, cwd: str | None = None) -> list[Suggestion]:
        """Run exactly one capture/generate/gate cycle. Returns what was shown."""
        self.stats.cycles += 1
        snapshot = self.context.capture(cwd)

        if snapshot.is_idle(self.config.idle_threshold_s):
            self.stats.suppressed_idle += 1
            log.debug("idle for %.0fs; staying quiet", snapshot.idle_seconds)
            return []

        if self._rate_limited():
            self.stats.suppressed_rate += 1
            log.debug("hourly suggestion budget exhausted")
            return []

        try:
            candidates = self.suggestions.generate(snapshot)
        except Exception as exc:
            self.stats.errors += 1
            log.error("suggestion generation failed: %s", exc)
            return []

        before = len(candidates)
        allowed = filter_suggestions(candidates, self.config)
        self.stats.suppressed_risk += before - len(allowed)

        shown: list[Suggestion] = []
        for sug in allowed:
            if self.vault.seen_recently(sug.fingerprint, self.config.cooldown_s):
                self.stats.suppressed_cooldown += 1
                log.debug("cooldown hit for %r", sug.title)
                continue
            self.vault.record(sug)
            self.vault.audit(
                "displayed",
                {"title": sug.title, "risk": sug.risk, "source": sug.source,
                 "gated": sug.is_gated},
            )
            self.presenter(sug)
            shown.append(sug)
            self.stats.displayed += 1
            key = sug.source
            self.stats.by_source[key] = self.stats.by_source.get(key, 0) + 1

        return shown

    def _rate_limited(self) -> bool:
        if self.config.max_suggestions_per_hour <= 0:
            return False
        return self.vault.count_since(time.time() - 3600) >= self.config.max_suggestions_per_hour

    # -- loop ------------------------------------------------------------
    def run_forever(self, cwd: str | None = None) -> None:
        """Poll until :meth:`stop` is called.

        This is designed to run on a **worker thread**, never the thread that
        owns the UI. Each iteration blocks on ``self._stop`` rather than
        ``time.sleep`` so that :meth:`stop` takes effect immediately instead of
        after a full poll interval.
        """
        log.info(
            "AETHER online (model=%s, interval=%.1fs, max %d/hr)",
            self.config.ollama_model,
            self.config.poll_interval_s,
            self.config.max_suggestions_per_hour,
        )
        self.vault.audit("started", {"version": _version(), "cwd": bool(cwd)})
        try:
            while not self._stop.is_set():
                self.tick(cwd)
                if self._stop.wait(self.config.poll_interval_s):
                    break
        except Exception as exc:  # pragma: no cover - defensive on the worker
            log.exception("polling loop aborted: %s", exc)
            self.stats.errors += 1
        finally:
            self.vault.audit("stopped", self.stats.as_dict())
            log.info("AETHER stopped after %d cycles", self.stats.cycles)

    def stop(self) -> None:
        """Signal the polling loop to finish. Safe to call from any thread."""
        self._stop.set()

    # -- decisions -------------------------------------------------------
    def dismiss(self, suggestion_id: str) -> None:
        """Record that the user rejected a suggestion (used to learn)."""
        self.vault.decide(suggestion_id, "dismissed")
        self.vault.audit("dismissed", {"id": suggestion_id})


def _version() -> str:
    from . import __version__

    return __version__
