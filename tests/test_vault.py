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


def test_record_many(vault) -> None:
    vault.record_many([Suggestion(title=f"T{i}", body="B") for i in range(3)])
    assert len(vault.recent()) == 3


def test_record_many_with_nothing_is_a_noop(vault) -> None:
    vault.record_many([])
    assert vault.recent() == []


# --- snoozes -------------------------------------------------------------


def test_suppress_hides_a_fingerprint(vault) -> None:
    s = Suggestion(title="A", body="B")
    vault.suppress(s.fingerprint, 3600)
    assert vault.is_suppressed(s.fingerprint) is True
    assert vault.is_suppressed(Suggestion(title="Z", body="B").fingerprint) is False


def test_suppression_expires(vault) -> None:
    import time

    s = Suggestion(title="A", body="B")
    vault.suppress_until(s.fingerprint, time.time() - 1)
    assert vault.is_suppressed(s.fingerprint) is False


def test_unsuppress_and_clear(vault) -> None:
    a, b = Suggestion(title="A", body="B"), Suggestion(title="C", body="D")
    vault.suppress(a.fingerprint, 3600)
    vault.suppress(b.fingerprint, 3600)
    assert vault.unsuppress(a.fingerprint) is True
    assert vault.unsuppress(a.fingerprint) is False
    assert vault.clear_suppressions() == 1
    assert vault.active_suppressions() == []


def test_prune_suppressions_only_drops_expired(vault) -> None:
    import time

    fresh = Suggestion(title="A", body="B")
    stale = Suggestion(title="C", body="D")
    vault.suppress(fresh.fingerprint, 3600)
    vault.suppress_until(stale.fingerprint, time.time() - 1)
    assert vault.prune_suppressions() == 1
    assert vault.is_suppressed(fresh.fingerprint) is True


def test_suppress_zero_means_forever(vault) -> None:
    s = Suggestion(title="A", body="B")
    vault.suppress(s.fingerprint, 0)
    assert vault.active_suppressions()[0]["until"] == float("inf")


# --- retention and stats -------------------------------------------------


def test_purge_all(vault) -> None:
    vault.record(Suggestion(title="A", body="B"))
    vault.audit("displayed", {"title": "A"})
    counts = vault.purge()
    assert counts["suggestions"] == 1
    assert counts["audit"] == 1
    assert vault.recent() == []
    assert vault.audit_trail() == []


def test_purge_respects_a_cutoff(vault) -> None:
    import time

    old = Suggestion(title="Old", body="B")
    old.created_at = time.time() - 86400 * 30
    vault.record(old)
    vault.record(Suggestion(title="Fresh", body="B"))
    counts = vault.purge(older_than_days=7)
    assert counts["suggestions"] == 1
    assert [s.title for s in vault.recent()] == ["Fresh"]


def test_stats_counters(vault) -> None:
    import time

    vault.record(Suggestion(title="A", body="B", source="llm"))
    dismissed = Suggestion(title="C", body="B", source="rules")
    vault.record(dismissed)
    vault.decide(dismissed.id, "dismissed")
    vault.suppress("f" * 16, 3600)

    stats = vault.stats()
    assert stats["total"] == 2
    assert stats["dismissed"] == 1
    assert stats["last_hour"] == 2
    assert stats["by_source"] == {"llm": 1, "rules": 1}
    assert stats["suppressed"] == 1
    assert stats["oldest"] <= time.time()


def test_stats_on_an_empty_vault(vault) -> None:
    stats = vault.stats()
    assert stats["total"] == 0
    assert stats["by_risk"] == {}
    assert stats["oldest"] is None
