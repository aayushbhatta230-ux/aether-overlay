"""Overlay behaviour.

Tk needs a display, so the whole module skips when one is not available -
on a headless CI box the card code is simply never exercised.
"""

from __future__ import annotations

import tkinter as tk

import pytest

from aether.models import Suggestion
from aether.overlay import Overlay, console_fallback


@pytest.fixture
def overlay(config):
    """A real card, or a skip: Tk needs a working Tcl installation."""
    try:
        ov = Overlay(config)
    except tk.TclError as exc:
        pytest.skip(f"Tk unavailable: {exc}")
    yield ov
    ov.destroy()


def _sug(**kwargs) -> Suggestion:
    fields = {"title": "Title", "body": "Body", "source": "llm"}
    fields.update(kwargs)
    return Suggestion(**fields)


def test_show_renders_and_hide_withdraws(overlay) -> None:
    overlay.show(_sug())
    assert overlay.root.state() == "normal"
    assert overlay.title_label.cget("text") == "Title"
    assert overlay.body_label.cget("text") == "Body"
    assert overlay._after_id is not None
    overlay.hide()
    assert overlay.root.state() == "withdrawn"
    assert overlay._after_id is None


def test_gated_card_advertises_that_it_needs_approval(overlay) -> None:
    overlay.show(_sug(risk="medium"))
    assert "approval" in overlay.footer.cget("text")


def test_risk_sets_the_accent_colour(overlay) -> None:
    low, medium = _sug(), _sug(risk="medium")
    overlay.show(low)
    low_colour = overlay.dot.cget("fg")
    overlay.show(medium)
    assert overlay.dot.cget("fg") != low_colour


def test_escape_hides_the_card(overlay) -> None:
    overlay.show(_sug())
    overlay.root.event_generate("<Escape>")
    overlay.root.update()
    assert overlay.root.state() == "withdrawn"


def test_dismiss_reports_to_the_orchestrator(overlay) -> None:
    seen: list[tuple] = []
    overlay.on_decision = lambda sid, decision, snooze: seen.append((sid, decision, snooze))
    sug = _sug()
    overlay.show(sug)
    overlay._decide("dismissed", snooze=False)
    assert seen == [(sug.id, "dismissed", False)]
    assert overlay.root.state() == "withdrawn"


def test_snooze_reports_the_snooze_flag(overlay) -> None:
    seen: list[tuple] = []
    overlay.on_decision = lambda sid, decision, snooze: seen.append((sid, decision, snooze))
    overlay.show(_sug())
    overlay._decide("snoozed", snooze=True)
    assert seen[0][1] == "snoozed"
    assert seen[0][2] is True


def test_decision_without_a_card_is_a_noop(overlay) -> None:
    calls: list[tuple] = []
    overlay.on_decision = lambda *a: calls.append(a)
    overlay._decide("dismissed", snooze=False)
    assert calls == []


def test_a_failing_decision_hook_does_not_break_the_card(overlay) -> None:
    def boom(*_a):
        raise RuntimeError("vault is gone")

    overlay.on_decision = boom
    overlay.show(_sug())
    overlay._decide("dismissed", snooze=False)
    assert overlay.root.state() == "withdrawn"


def test_post_only_enqueues(overlay) -> None:
    """Cross-thread safety: post must not touch a widget."""
    sug = _sug()
    overlay.post(sug)
    assert overlay._inbox.qsize() == 1
    assert overlay._current is None


def test_pump_drains_the_queue_on_the_ui_thread(overlay) -> None:
    overlay.post(_sug())
    overlay.post(_sug(title="Second"))
    overlay._pump()
    assert overlay._inbox.empty()
    assert overlay._current.title == "Second"


def test_pump_stops_rescheduling_when_not_running(overlay) -> None:
    overlay._running = False
    overlay._pump()
    assert overlay._pump_id is None


def test_wait_for_exit_returns_immediately_with_no_card(overlay) -> None:
    assert overlay.wait_for_exit(poll_ms=10, timeout_s=5.0) is True


def test_wait_for_exit_gives_up_at_the_deadline(overlay) -> None:
    """The regression guard: a card must never block shutdown forever."""
    overlay.show(_sug())
    assert overlay.wait_for_exit(poll_ms=10, timeout_s=0.2) is False


def test_show_clamps_hostile_lengths(overlay) -> None:
    overlay.show(_sug(title="T" * 500, body="B" * 900))
    assert len(overlay.title_label.cget("text")) == 60
    assert len(overlay.body_label.cget("text")) == 180


def test_destroy_is_safe_to_call_twice(config) -> None:
    try:
        ov = Overlay(config)
    except tk.TclError as exc:
        pytest.skip(f"Tk unavailable: {exc}")
    ov.destroy()
    ov.destroy()


# --- console rendering ---------------------------------------------------


def test_console_fallback_marks_the_gate() -> None:
    text = console_fallback(_sug(risk="medium"))
    assert "GATE" in text
    assert "approval required" in text


def test_console_fallback_marks_held() -> None:
    assert "HOLD" in console_fallback(_sug(risk="high"))


def test_console_fallback_shows_the_source() -> None:
    assert "rules" in console_fallback(_sug(source="rules"))