"""End-to-end orchestrator tests with every dependency faked."""

from __future__ import annotations

import threading
import time

import pytest

from aether.app import Aether
from aether.models import ContextSnapshot, Suggestion


class StubContext:
    def __init__(self, snapshot: ContextSnapshot) -> None:
        self.snapshot = snapshot

    def capture(self, cwd=None) -> ContextSnapshot:
        return self.snapshot


class StubSuggestions:
    def __init__(self, items: list[Suggestion] | Exception) -> None:
        self.items = items

    def generate(self, snapshot: ContextSnapshot) -> list[Suggestion]:
        if isinstance(self.items, Exception):
            raise self.items
        return list(self.items)


def _app(config, vault, snapshot, items, shown: list) -> Aether:
    return Aether(
        config,
        context_engine=StubContext(snapshot),
        suggestion_engine=StubSuggestions(items),
        vault=vault,
        presenter=shown.append,
    )


def test_happy_path_displays_suggestion(config, vault, snapshot) -> None:
    shown: list[Suggestion] = []
    app = _app(config, vault, snapshot, [Suggestion(title="A", body="B")], shown)
    assert len(app.tick()) == 1
    assert shown[0].title == "A"
    assert app.stats.displayed == 1


def test_idle_user_is_never_interrupted(config, vault, snapshot) -> None:
    snapshot.idle_seconds = config.idle_threshold_s + 1
    shown: list[Suggestion] = []
    app = _app(config, vault, snapshot, [Suggestion(title="A", body="B")], shown)
    assert app.tick() == []
    assert shown == []
    assert app.stats.suppressed_idle == 1


def test_high_risk_never_reaches_the_user(config, vault, snapshot) -> None:
    shown: list[Suggestion] = []
    items = [Suggestion(title="Delete everything", body="rm -rf", risk="high")]
    app = _app(config, vault, snapshot, items, shown)
    assert app.tick() == []
    assert shown == []
    assert app.stats.suppressed_risk == 1


def test_blocked_suggestion_is_audited(config, vault, snapshot) -> None:
    """Withholding must be provable after the fact, not silent."""
    items = [Suggestion(title="Delete everything", body="rm -rf", risk="high")]
    _app(config, vault, snapshot, items, []).tick()
    events = [e for e in vault.audit_trail(limit=20) if e["event"] == "blocked"]
    assert events, "a withheld suggestion left no trace"
    assert events[0]["detail"]["reason"] == "blocked_high_risk"
    assert events[0]["detail"]["risk"] == "high"
    assert events[0]["detail"]["context"] == snapshot.fingerprint()
    assert vault.recent() == []


def test_medium_can_be_switched_off_entirely(config, vault, snapshot) -> None:
    config.allow_medium_risk = False
    shown: list[Suggestion] = []
    items = [
        Suggestion(title="Gated", body="B", risk="medium"),
        Suggestion(title="Fine", body="B", risk="low"),
    ]
    app = _app(config, vault, snapshot, items, shown)
    app.tick()
    assert [s.title for s in shown] == ["Fine"]
    reasons = [
        e["detail"]["reason"] for e in vault.audit_trail(limit=20) if e["event"] == "blocked"
    ]
    assert "blocked_medium_disabled" in reasons


def test_cooldown_prevents_repeats(config, vault) -> None:
    shown: list[Suggestion] = []
    items = [Suggestion(title="A", body="B")]

    class _RotatingContext:
        """Same suggestion, but a different context each cycle."""

        def __init__(self) -> None:
            self.n = 0

        def capture(self, cwd=None):
            self.n += 1
            return ContextSnapshot(active_app="Code.exe", window_title=f"file{self.n}.py")

    app = Aether(
        config,
        context_engine=_RotatingContext(),
        suggestion_engine=StubSuggestions(items),
        vault=vault,
        presenter=shown.append,
    )
    app.tick()
    app.tick()
    assert len(shown) == 1
    assert app.stats.suppressed_cooldown == 1


def test_unchanged_context_skips_a_second_identical_cycle(config, vault, snapshot) -> None:
    """No new situation, no new suggestion - and no second model round trip."""
    shown: list[Suggestion] = []
    app = _app(config, vault, snapshot, [Suggestion(title="A", body="B")], shown)
    app.tick()
    app.tick()
    assert len(shown) == 1
    assert app.stats.suppressed_duplicate == 1
    assert app.stats.suppressed_cooldown == 0


def test_changed_clipboard_reopens_the_same_context(config, vault, snapshot) -> None:
    shown: list[Suggestion] = []
    app = _app(config, vault, snapshot, [Suggestion(title="A", body="B")], shown)
    app.tick()
    snapshot.clipboard_hash = "deadbeef1234"
    app.tick()
    assert len(shown) == 1  # identical fingerprint, so still stopped by cooldown
    assert app.stats.suppressed_cooldown == 1


def test_hourly_budget_is_enforced(config, vault, snapshot) -> None:
    config.max_suggestions_per_hour = 2
    config.cooldown_s = 0.0
    shown: list[Suggestion] = []

    def make(i: int) -> list[Suggestion]:
        return [Suggestion(title=f"T{i}", body=f"B{i}")]

    for i in range(4):
        _app(config, vault, snapshot, make(i), shown).tick()

    assert len(shown) == 2
    assert shown[0].title == "T0"


def test_zero_budget_disables_suggestions_entirely(config, vault, snapshot) -> None:
    config.max_suggestions_per_hour = 0
    shown: list[Suggestion] = []
    app = _app(config, vault, snapshot, [Suggestion(title="A", body="B")], shown)
    app.tick()
    assert app.tick() == []
    assert shown == []
    assert app.stats.suppressed_rate == 2


def test_negative_budget_means_uncapped(config, vault) -> None:
    """-1 removes the hourly ceiling; it is opt-in and discouraged."""
    config.max_suggestions_per_hour = -1
    config.cooldown_s = 0.0
    shown: list[Suggestion] = []

    class _RotatingContext:
        def __init__(self) -> None:
            self.n = 0

        def capture(self, cwd=None):
            self.n += 1
            return ContextSnapshot(active_app="Code.exe", window_title=f"f{self.n}.py")

    app = Aether(
        config,
        context_engine=_RotatingContext(),
        suggestion_engine=StubSuggestions([Suggestion(title="T", body="B")]),
        vault=vault,
        presenter=shown.append,
    )
    for _ in range(5):
        app.tick()
    assert app.stats.displayed == 5
    assert app.stats.suppressed_rate == 0


def test_generation_error_does_not_crash_the_loop(config, vault, snapshot) -> None:
    shown: list[Suggestion] = []
    app = _app(config, vault, snapshot, RuntimeError("model exploded"), shown)
    assert app.tick() == []
    assert app.stats.errors == 1
    app.tick()  # still alive
    assert app.stats.cycles == 2


def test_duplicate_suggestions_within_one_batch_show_once(config, vault, snapshot) -> None:
    shown: list[Suggestion] = []
    items = [
        Suggestion(title="Same", body="Body"),
        Suggestion(title="Same", body="Body"),
    ]
    app = _app(config, vault, snapshot, items, shown)
    app.tick()
    assert len(shown) == 1


def test_dismiss_is_recorded(config, vault, snapshot) -> None:
    shown: list[Suggestion] = []
    app = _app(config, vault, snapshot, [Suggestion(title="A", body="B")], shown)
    app.tick()
    app.dismiss(shown[0].id)
    assert vault.recent()[0].decision == "dismissed"


def test_snoozed_suggestion_is_never_shown_again(config, vault) -> None:
    """'Never show me this again' outlives the cooldown and the hourly budget."""
    shown: list[Suggestion] = []
    app = _app(config, vault, _rotating_snapshot(0), [Suggestion(title="A", body="B")], shown)
    app.tick()
    app.dismiss(shown[0].id, snooze=True)

    shown.clear()
    for i in range(1, 6):
        app = _app(
            config,
            vault,
            _rotating_snapshot(i),
            [Suggestion(title="A", body="B")],
            shown,
        )
        app.tick()
    assert shown == []
    assert vault.active_suppressions(), "snooze was not persisted"


def test_snooze_does_not_leak_into_other_suggestions(config, vault) -> None:
    shown: list[Suggestion] = []
    app = _app(config, vault, _rotating_snapshot(0), [Suggestion(title="A", body="B")], shown)
    app.tick()
    app.dismiss(shown[0].id, snooze=True)

    other: list[Suggestion] = []
    _app(config, vault, _rotating_snapshot(9), [Suggestion(title="B", body="D")], other).tick()
    assert [s.title for s in other] == ["B"]


def test_accept_is_recorded(config, vault, snapshot) -> None:
    shown: list[Suggestion] = []
    app = _app(config, vault, snapshot, [Suggestion(title="A", body="B")], shown)
    app.tick()
    app.accept(shown[0].id)
    assert vault.recent()[0].decision == "accepted"


def test_retention_prunes_old_history_on_shutdown(config, vault) -> None:
    config.history_retention_days = 1
    config.poll_interval_s = 0.01
    stale = Suggestion(title="Old", body="B")
    stale.created_at = time.time() - 86400 * 30
    vault.record(stale)

    app = Aether(
        config,
        context_engine=_CountingContext(),
        suggestion_engine=StubSuggestions([]),
        vault=vault,
        presenter=lambda _s: None,
    )
    worker = threading.Thread(target=app.run_forever, daemon=True)
    worker.start()
    time.sleep(0.15)
    app.stop()
    worker.join(timeout=5.0)

    assert vault.recent(limit=50) == []


def _rotating_snapshot(n: int) -> ContextSnapshot:
    return ContextSnapshot(active_app="Code.exe", window_title=f"file{n}.py")


def test_cycle_increments_stats(config, vault, snapshot) -> None:
    app = _app(config, vault, snapshot, [], [])
    app.tick()
    app.tick()
    assert app.stats.cycles == 2
    assert app.stats.as_dict()["cycles"] == 2


def test_full_stack_with_real_context_engine(config, vault, probes) -> None:
    """The real engine and a rule-only brain, wired together."""
    app = Aether(
        config,
        suggestion_engine=StubSuggestions([Suggestion(title="Branch", body="commit?")]),
        vault=vault,
        presenter=lambda s: None,
        probes=probes,
    )
    assert app.context.capture().git_branch == "feat/overlay"
    assert len(app.tick()) == 1


@pytest.mark.parametrize("risk,expected_shown", [("low", 1), ("medium", 1), ("high", 0)])
def test_risk_matrix(config, vault, snapshot, risk, expected_shown) -> None:
    shown: list[Suggestion] = []
    app = _app(config, vault, snapshot, [Suggestion(title="T", body="B", risk=risk)], shown)
    app.tick()
    assert len(shown) == expected_shown


# --------------------------------------------------------------------------
# Loop lifecycle
#
# Regression cover for the "window is not responding" bug: the polling loop
# must never own the thread that runs Tk's main loop, and stop() must take
# effect immediately rather than after a full poll interval.
# --------------------------------------------------------------------------


class _CountingContext:
    """Context stub that always reports an active, non-idle user."""

    def __init__(self) -> None:
        self.calls = 0

    def capture(self, cwd=None) -> ContextSnapshot:
        self.calls += 1
        return ContextSnapshot(active_app="Code.exe", idle_seconds=0.0)


def test_poller_does_not_run_on_the_calling_thread(config, vault) -> None:
    """The poller must be safe to run on a worker, never blocking the UI."""
    config.poll_interval_s = 0.01
    seen: list[int] = []

    app = Aether(
        config,
        context_engine=_CountingContext(),
        suggestion_engine=StubSuggestions([Suggestion(title="T", body="B")]),
        vault=vault,
        presenter=lambda _s: seen.append(threading.get_ident()),
    )

    worker = threading.Thread(target=app.run_forever, name="aether-poller")
    worker.start()
    try:
        deadline = time.monotonic() + 5.0
        while not seen and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        app.stop()
        worker.join(timeout=5.0)

    assert not worker.is_alive(), "poller did not honour stop()"
    assert seen, "presenter was never called"
    assert threading.get_ident() not in seen, "poller ran on the caller's thread"


def test_stop_interrupts_a_long_poll_interval(config, vault) -> None:
    """stop() must not wait out the interval, even a very long one."""
    config.poll_interval_s = 30.0  # a time.sleep loop would hang for 30s
    app = Aether(
        config,
        context_engine=_CountingContext(),
        suggestion_engine=StubSuggestions([]),
        vault=vault,
        presenter=lambda _s: None,
    )

    worker = threading.Thread(target=app.run_forever, daemon=True)
    worker.start()
    time.sleep(0.2)  # let it enter the wait
    app.stop()
    worker.join(timeout=5.0)

    assert not worker.is_alive(), "stop() did not interrupt the poll wait"


def test_stop_before_start_prevents_any_cycle(config, vault) -> None:
    app = Aether(
        config,
        context_engine=_CountingContext(),
        suggestion_engine=StubSuggestions([]),
        vault=vault,
        presenter=lambda _s: None,
    )
    app.stop()
    app.run_forever()
    assert app.stats.cycles == 0


def test_stop_is_safe_from_another_thread_and_repeatable(config, vault) -> None:
    app = Aether(
        config,
        context_engine=_CountingContext(),
        suggestion_engine=StubSuggestions([]),
        vault=vault,
        presenter=lambda _s: None,
    )
    stopper = threading.Thread(target=app.stop, daemon=True)
    stopper.start()
    app.stop()  # concurrent duplicate
    stopper.join(timeout=2.0)

    assert app._stop.is_set()
    app.run_forever()  # returns immediately
    assert app.stats.cycles == 0


def test_run_forever_always_records_a_stop_audit_entry(config, vault) -> None:
    config.poll_interval_s = 0.01
    app = Aether(
        config,
        context_engine=_CountingContext(),
        suggestion_engine=StubSuggestions([]),
        vault=vault,
        presenter=lambda _s: None,
    )
    worker = threading.Thread(target=app.run_forever, daemon=True)
    worker.start()
    time.sleep(0.1)
    app.stop()
    worker.join(timeout=5.0)

    events = [entry["event"] for entry in vault.audit_trail(limit=10)]
    assert "started" in events
    assert "stopped" in events

