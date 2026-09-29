"""Vault persistence, cooldown, and rate limiting."""

from __future__ import annotations

from aether.models import Suggestion


def test_record_and_recent(vault) -> None:
    vault.record(Suggestion(title="A", body="B", risk="low"))
    recent = vault.recent()
    assert len(recent) == 1
    assert recent[0].title == "A"


def test_seen_recently_respects_cooldown_window(vault) -> None:
    s = Suggestion(title="A", body="B", risk="low")
    vault.record(s)
    assert vault.seen_recently(s.fingerprint, within_s=60) is True
    assert vault.seen_recently(s.fingerprint, within_s=-1) is False


def test_different_fingerprints_are_distinct(vault) -> None:
    a = Suggestion(title="A", body="B", risk="low")
    b = Suggestion(title="C", body="D", risk="low")
    vault.record(a)
    assert vault.seen_recently(b.fingerprint, within_s=60) is False


def test_count_since(vault) -> None:
    import time

    for _ in range(3):
        vault.record(Suggestion(title="A", body="B", risk="low"))
    assert vault.count_since(time.time() - 3600) == 3
    assert vault.count_since(time.time() + 3600) == 0


def test_decide_persists(vault) -> None:
    s = Suggestion(title="A", body="B", risk="low")
    vault.record(s)
    vault.decide(s.id, "dismissed")
    assert vault.recent()[0].decision == "dismissed"


def test_audit_trail_records_events(vault) -> None:
    vault.audit("displayed", {"title": "A"})
    trail = vault.audit_trail()
    assert trail[0]["event"] == "displayed"
    assert trail[0]["detail"]["title"] == "A"


def test_schema_is_idempotent(config) -> None:
    from aether.vault import Vault

    Vault(config.db_file)
    Vault(config.db_file)  # must not raise
    assert config.db_file.exists()
