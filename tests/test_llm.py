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
