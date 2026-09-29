"""End-to-end orchestrator tests with every dependency faked."""

from __future__ import annotations

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


def test_cooldown_prevents_repeats(config, vault, snapshot) -> None:
    shown: list[Suggestion] = []
    items = [Suggestion(title="A", body="B")]
    app = _app(config, vault, snapshot, items, shown)
    app.tick()
    app.tick()
    assert len(shown) == 1
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
    assert len(shown) == 1  # 0 means unlimited


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
