"""The risk gate - AETHER's core safety invariant."""

from __future__ import annotations

from aether.models import Suggestion
from aether.safety import (
    BLOCK_HIGH,
    filter_suggestions,
    gate,
    never_auto_execute,
    requires_approval,
)


def _s(risk: str) -> Suggestion:
    return Suggestion(title="t", body="b", risk=risk)


def test_low_risk_is_shown_immediately() -> None:
    ok, reason = gate(_s("low"))
    assert ok is True
    assert reason != BLOCK_HIGH


def test_medium_risk_is_shown_but_gated() -> None:
    sug = _s("medium")
    ok, _ = gate(sug)
    assert ok is True
    assert requires_approval(sug) is True


def test_high_risk_is_withheld_entirely() -> None:
    ok, reason = gate(_s("high"))
    assert ok is False
    assert reason == BLOCK_HIGH


def test_unrecognised_risk_is_treated_as_medium() -> None:
    sug = Suggestion(title="t", body="b", risk="catastrophic")
    assert sug.risk == "medium"
    assert sug.is_gated is True


def test_filter_drops_high_and_keeps_rest() -> None:
    kept = filter_suggestions([_s("low"), _s("high"), _s("medium")])
    assert [s.risk for s in kept] == ["low", "medium"]


def test_filter_sorts_by_confidence() -> None:
    low = Suggestion(title="a", body="b", risk="low", confidence=0.2)
    high = Suggestion(title="c", body="d", risk="low", confidence=0.9)
    kept = filter_suggestions([low, high])
    assert kept[0].confidence == 0.9


def test_aether_has_no_autonomous_execution_path() -> None:
    # Documented invariant: AETHER observes, it never acts.
    assert never_auto_execute() is False
