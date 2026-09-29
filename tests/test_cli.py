"""CLI behaviour with the overlay faked: no window, no event loop, no hang.

The regression these guard: ``--once`` used to block forever because the card
was posted to a pump that only runs inside ``Overlay.run()``.
"""

from __future__ import annotations

import json

import pytest

from aether import cli
from aether.app import Aether


class FakeOverlay:
    """Records what the CLI asked it to do, without touching Tk."""

    instances: list[FakeOverlay] = []

    def __init__(self, config, on_decision=None) -> None:
        self.config = config
        self.on_decision = on_decision
        self.posted = []
        self.presented = []
        self.destroyed = False
        self.ran = False
        self.wait_calls: list[dict] = []
        FakeOverlay.instances.append(self)

    def post(self, suggestion) -> None:
        self.posted.append(suggestion)

    def present(self, suggestion) -> None:
        self.presented.append(suggestion)

    def run(self) -> None:
        self.ran = True

    def wait_for_exit(self, poll_ms=200, timeout_s=None) -> bool:
        self.wait_calls.append({"poll_ms": poll_ms, "timeout_s": timeout_s})
        return True

    def destroy(self) -> None:
        self.destroyed = True


@pytest.fixture
def overlay(monkeypatch):
    FakeOverlay.instances.clear()
    monkeypatch.setattr(cli, "Overlay", FakeOverlay)
    return FakeOverlay


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Point the default config at a throwaway database.

    Also stubs the context engine: a CI runner reports a *very* idle user, and
    a real probe would silently suppress every suggestion.
    """
    monkeypatch.setenv("AETHER_CONFIG", str(tmp_path / "none.json"))
    monkeypatch.setattr(
        cli, "load_config", lambda _p=None: _isolated_config(tmp_path)
    )
    monkeypatch.setattr("aether.app.ContextEngine", StubContext)
    return tmp_path


class StubContext:
    """Always an active, non-idle user with an unremarkable window."""

    def __init__(self, config, probes=None) -> None:
        self.config = config
        self.captures = 0

    def capture(self, cwd=None):
        from aether.models import ContextSnapshot

        self.captures += 1
        return ContextSnapshot(active_app="Code.exe", window_title=f"file{self.captures}.py")


def _isolated_config(tmp_path):
    from aether.config import Config

    cfg = Config()
    cfg.config_dir = tmp_path
    cfg.db_path = tmp_path / "cli.db"
    cfg.poll_interval_s = 0.01
    # Silence by default: these tests must not depend on whatever is on the
    # machine running them.
    cfg.max_suggestions_per_hour = 0
    cfg.__post_init__()
    return cfg


class StubEngine:
    """Always yields one suggestion, so the card path is deterministic."""

    def __init__(self, config, transport=None) -> None:
        self.config = config

    def generate(self, snapshot):
        from aether.models import Suggestion

        return [Suggestion(title="Stub", body="B", risk="low", source="rules")]


def _open_budget(monkeypatch, tmp_path) -> None:
    """Lift the per-hour budget so suggestions are actually produced."""

    def load(_path=None):
        cfg = _isolated_config(tmp_path)
        cfg.max_suggestions_per_hour = 8
        cfg.cooldown_s = 0.0
        cfg.__post_init__()
        return cfg

    monkeypatch.setattr(cli, "load_config", load)


def test_once_presents_on_the_ui_thread_and_returns(isolated, overlay, monkeypatch) -> None:
    """`--once` must render on this thread, not post to a pump that never runs."""
    _open_budget(monkeypatch, isolated)
    monkeypatch.setattr("aether.app.SuggestionEngine", StubEngine)
    assert cli.main(["--once"]) == 0
    card = overlay.instances[0]
    assert card.ran is False, "--once must not enter the long-running main loop"
    assert card.posted == [], "--once must not use the cross-thread pump"
    assert [s.title for s in card.presented] == ["Stub"]
    assert card.destroyed is True
    assert card.wait_calls, "--once must wait for the card, but with a deadline"
    assert card.wait_calls[0]["timeout_s"] is not None


def test_once_returns_even_with_nothing_to_show(isolated, overlay, capsys) -> None:
    assert cli.main(["--once"]) == 0
    assert "No suggestion right now" in capsys.readouterr().out


def test_dry_run_never_builds_an_overlay(isolated, overlay, capsys) -> None:
    assert cli.main(["--dry-run"]) == 0
    assert overlay.instances == []


def test_long_run_uses_the_worker_thread_and_posts(isolated, overlay) -> None:
    """The poller must own a worker thread; the card crosses via post()."""
    card_box: dict = {}

    real_init = Aether.__init__

    def spy_init(self, *a, **kw):
        real_init(self, *a, **kw)
        card_box["presenter"] = self.presenter

    Aether.__init__ = spy_init  # type: ignore[method-assign]
    try:
        assert cli.main([]) == 0
    finally:
        Aether.__init__ = real_init  # type: ignore[method-assign]

    card = overlay.instances[0]
    assert card.ran is True
    assert card_box["presenter"] == card.post
    assert card.destroyed is True


def test_decision_hook_is_wired_to_the_orchestrator(isolated, overlay) -> None:
    cli.main(["--once"])
    card = overlay.instances[0]
    assert callable(card.on_decision)
    assert card.on_decision.__self__.__class__.__name__ == "Aether"


def test_unknown_config_key_is_a_clear_error(tmp_path, capsys, monkeypatch) -> None:
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps({"pol_interval_s": 5}), encoding="utf-8")
    monkeypatch.delenv("AETHER_CONFIG", raising=False)
    assert cli.main(["-c", str(path)]) == 2
    assert "unknown key" in capsys.readouterr().err


def test_stats_on_an_empty_vault(isolated, overlay, capsys) -> None:
    assert cli.main(["--stats"]) == 0
    out = capsys.readouterr().out
    assert "shown (all time): 0" in out


def test_clear_yes_wipes_history(isolated, overlay, capsys) -> None:
    from aether.models import Suggestion
    from aether.vault import Vault

    vault = Vault(isolated / "cli.db")
    vault.record(Suggestion(title="Old", body="B"))
    vault.record(Suggestion(title="Newer", body="B"))

    assert cli.main(["--clear", "--yes"]) == 0
    assert "Cleared AETHER history" in capsys.readouterr().out
    assert vault.recent() == []


def test_clear_older_than_only_drops_old_rows(isolated, overlay, capsys) -> None:
    import time

    from aether.models import Suggestion
    from aether.vault import Vault

    vault = Vault(isolated / "cli.db")
    stale = Suggestion(title="Stale", body="B")
    stale.created_at = time.time() - 86400 * 30
    vault.record(stale)
    vault.record(Suggestion(title="Fresh", body="B"))

    assert cli.main(["--clear", "--clear-older-than", "7"]) == 0
    assert [s.title for s in vault.recent()] == ["Fresh"]


def test_clear_without_confirmation_aborts(isolated, overlay, monkeypatch, capsys) -> None:
    monkeypatch.setattr("builtins.input", lambda *_a: "")
    assert cli.main(["--clear"]) == 1
    assert "Cancelled" in capsys.readouterr().out


# --- doctor and history ---------------------------------------------------


@pytest.fixture
def offline(isolated, monkeypatch):
    """Same isolated config, but pointed at a closed port.

    ``--doctor`` talks to Ollama; the suite must not depend on whether the
    developer happens to have one running.
    """
    cfg = _isolated_config(isolated)
    cfg.ollama_host = "http://127.0.0.1:9"
    cfg.ollama_timeout_s = 1.0
    cfg.__post_init__()
    monkeypatch.setattr(cli, "load_config", lambda _p=None: cfg)
    return cfg


def test_doctor_degrades_without_a_model(offline, overlay, capsys) -> None:
    assert cli.main(["--doctor"]) == 0
    out = capsys.readouterr().out
    assert "Degraded" in out
    assert "rule fallback active" in out
    assert "auto-exec  : NEVER" in out
    assert "redaction  : on" in out
    assert str(offline.db_file) in out


def test_doctor_flags_a_disabled_budget(offline, overlay, capsys) -> None:
    assert cli.main(["--doctor"]) == 0
    assert "DISABLED" in capsys.readouterr().out


def test_doctor_warns_when_redaction_is_off(offline, overlay, monkeypatch, capsys) -> None:
    offline.redact_pii = False
    offline.__post_init__()
    assert cli.main(["--doctor"]) == 0
    assert "redaction is OFF" in capsys.readouterr().out


def test_history_on_an_empty_vault(isolated, overlay, capsys) -> None:
    assert cli.main(["--history"]) == 0
    assert "No suggestions recorded yet" in capsys.readouterr().out


def test_history_marks_decisions(isolated, overlay, capsys) -> None:
    from aether.models import Suggestion
    from aether.vault import Vault

    vault = Vault(isolated / "cli.db")
    shown = Suggestion(title="Kept", body="B")
    vault.record(shown)
    vault.record(Suggestion(title="Rejected", body="B", risk="medium"))
    vault.decide(shown.id, "dismissed")
    vault.audit("displayed", {"title": "Kept"})

    assert cli.main(["--history"]) == 0
    out = capsys.readouterr().out
    assert "Rejected" in out
    assert "GATE" in out or "medium" in out
    assert "Audit trail" in out


def test_stats_reports_a_dismissal_rate(isolated, overlay, capsys) -> None:
    from aether.models import Suggestion
    from aether.vault import Vault

    vault = Vault(isolated / "cli.db")
    for _ in range(4):
        s = Suggestion(title="Same", body="B", source="llm", risk="low")
        vault.record(s)
        vault.decide(s.id, "dismissed")

    assert cli.main(["--stats"]) == 0
    out = capsys.readouterr().out
    assert "dismissal rate  : 100%" in out
    assert "being ignored" in out


def test_history_print_survives_a_corrupt_audit_row(isolated, overlay, capsys) -> None:
    from aether.vault import Vault

    vault = Vault(isolated / "cli.db")
    with vault._connect() as conn:  # deliberately corrupt, to prove the fallback
        conn.execute(
            "INSERT INTO audit (ts, event, detail) VALUES (?,?,?)",
            (0.0, "weird", "{not json"),
        )
        conn.commit()
    assert cli.main(["--history"]) == 0