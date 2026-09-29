"""LLM output parsing and the offline fallback path."""

from __future__ import annotations

import json

from aether.llm import NullTransport, SuggestionEngine, parse_suggestions
from aether.models import ContextSnapshot


class FakeTransport:
    """Returns a canned string (or raises) to simulate the daemon."""

    def __init__(self, response: str = "", error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[str] = []

    def generate(self, model, prompt, system, timeout_s) -> str:
        self.calls.append(prompt)
        if self.error:
            raise self.error
        return self.response


def test_parses_wellformed_output() -> None:
    out = parse_suggestions(
        '{"suggestions": [{"title": "Commit", "body": "You have changes.", '
        '"risk": "low", "confidence": 0.8}]}'
    )
    assert len(out) == 1
    assert out[0].title == "Commit"
    assert out[0].confidence == 0.8


def test_recovers_json_from_markdown_fence() -> None:
    raw = '```json\n{"suggestions": [{"title": "A", "body": "B", "risk": "low"}]}\n```'
    assert len(parse_suggestions(raw)) == 1


def test_recovers_json_from_surrounding_prose() -> None:
    raw = 'Sure! {"suggestions": [{"title": "A", "body": "B", "risk": "low"}]} hope that helps'
    assert len(parse_suggestions(raw)) == 1


def test_skips_entries_missing_title_or_body() -> None:
    out = parse_suggestions(
        '{"suggestions": [{"title": "", "body": "B"}, {"title": "T", "body": ""}]}'
    )
    assert out == []


def test_caps_at_two_suggestions() -> None:
    items = ", ".join(
        f'{{"title": "T{i}", "body": "B{i}", "risk": "low"}}' for i in range(6)
    )
    assert len(parse_suggestions(f'{{"suggestions": [{items}]}}')) == 2


def test_bad_confidence_falls_back_to_default() -> None:
    out = parse_suggestions(
        '{"suggestions": [{"title": "T", "body": "B", "risk": "low", '
        '"confidence": "very high"}]}'
    )
    assert out[0].confidence == 0.5


def test_garbage_returns_empty_list() -> None:
    assert parse_suggestions("not json at all") == []
    assert parse_suggestions("") == []


def test_clamps_overlong_fields() -> None:
    payload = json.dumps(
        {"suggestions": [{"title": "T" * 200, "body": "B" * 500, "risk": "low"}]}
    )
    out = parse_suggestions(payload)
    assert len(out) == 1
    assert len(out[0].title) == 60
    assert len(out[0].body) == 180


def test_engine_uses_llm_when_available(config, snapshot) -> None:
    transport = FakeTransport(
        '{"suggestions": [{"title": "Run tests", "body": "CI is red.", "risk": "low"}]}'
    )
    engine = SuggestionEngine(config, transport)
    out = engine.generate(snapshot)
    assert out[0].source == "llm"
    assert transport.calls, "LLM should have been consulted"


def test_engine_falls_back_when_llm_unreachable(config, snapshot) -> None:
    engine = SuggestionEngine(config, NullTransport())
    out = engine.generate(snapshot)
    # Snapshot is on a non-default branch, so the rule engine has something to say.
    assert out and out[0].source == "rules"


def test_fallback_can_be_disabled(config, snapshot) -> None:
    config.offline_fallback = False
    engine = SuggestionEngine(config, NullTransport())
    assert engine.generate(snapshot) == []


def test_unknown_app_skips_llm_entirely(config) -> None:
    transport = FakeTransport('{"suggestions": []}')
    engine = SuggestionEngine(config, transport)
    engine.generate(ContextSnapshot(active_app="unknown"))
    assert transport.calls == []


# --------------------------------------------------------------------------
# Deterministic rules: the product still has to be worth running offline.
# --------------------------------------------------------------------------


def _rules_for(config, **kwargs) -> list:
    engine = SuggestionEngine(config, NullTransport())
    snapshot = ContextSnapshot(**kwargs)
    return engine.generate(snapshot)


def test_error_rule_fires_on_a_failing_window(config) -> None:
    out = _rules_for(config, active_app="Code.exe", window_title="Traceback (most recent call last)")
    assert out and "error" in out[0].title.lower()
    assert out[0].source == "rules"
    assert out[0].risk == "low", "a hint must never be gated or risky"


def test_meeting_rule_fires_on_a_conference_app(config) -> None:
    out = _rules_for(config, active_app="Zoom.exe", window_title="Weekly sync")
    assert out and "meeting" in out[0].title.lower()


def test_meeting_rule_also_matches_the_title(config) -> None:
    out = _rules_for(config, active_app="chrome.exe", window_title="Meet - standup")
    assert out and "meeting" in out[0].title.lower()


def test_unsaved_document_rule(config) -> None:
    out = _rules_for(config, active_app="Code.exe", window_title="notes.md",
                     document_modified=True)
    assert out and "unsaved" in out[0].title.lower()


def test_dwell_rule_fires_after_the_configured_time(config) -> None:
    config.dwell_reminder_s = 600
    out = _rules_for(config, active_app="Code.exe", window_title="app.py",
                     app_dwell_seconds=1200)
    assert out and "minutes in Code.exe" in out[0].title
    assert "20 minutes" in out[0].title


def test_dwell_rule_stays_quiet_below_the_threshold(config) -> None:
    config.dwell_reminder_s = 3600
    out = _rules_for(config, active_app="Code.exe", window_title="app.py",
                     app_dwell_seconds=1200)
    assert not any("minutes in" in s.title for s in out)


def test_dwell_rule_can_be_switched_off(config) -> None:
    config.dwell_reminder_s = 0
    out = _rules_for(config, active_app="Code.exe", window_title="app.py",
                     app_dwell_seconds=99999)
    assert not any("minutes in" in s.title for s in out)


def test_branch_rule_is_silent_on_a_default_branch(config) -> None:
    """`main` with nothing else going on is not worth a card."""
    out = _rules_for(config, active_app="Code.exe", window_title="app.py",
                     git_branch="main", app_dwell_seconds=0.0)
    assert out == []


def test_dwell_rule_still_fires_on_a_default_branch(config) -> None:
    """Dwell is about the session, not the branch: it is orthogonal."""
    config.dwell_reminder_s = 600
    out = _rules_for(config, active_app="Code.exe", window_title="app.py",
                     git_branch="main", app_dwell_seconds=1200)
    assert out and "minutes in Code.exe" in out[0].title


def test_focused_work_outranks_a_bare_branch_hint(config) -> None:
    """An error beats a branch reminder: order the rules by usefulness."""
    out = _rules_for(config, active_app="Code.exe", window_title="TypeError in build",
                     git_branch="feat/x")
    assert out and "error" in out[0].title.lower()


def test_rules_can_be_disabled_entirely(config, snapshot) -> None:
    config.offline_fallback = False
    assert SuggestionEngine(config, NullTransport()).generate(snapshot) == []


def test_rules_use_the_configured_dwell_threshold(config) -> None:
    """A different threshold is a different rule, not a duplicate card."""
    config.dwell_reminder_s = 600
    first = _rules_for(config, active_app="Code.exe", window_title="a.py",
                       app_dwell_seconds=700)[0]
    config.dwell_reminder_s = 3600
    out = _rules_for(config, active_app="Code.exe", window_title="a.py",
                     app_dwell_seconds=3700)
    assert out and out[0].fingerprint != first.fingerprint
