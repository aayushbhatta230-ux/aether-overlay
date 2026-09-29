"""Context engine behaviour with fully injected probes."""

from __future__ import annotations

from aether.context import ContextEngine, Probes
from aether.models import ContextSnapshot


def test_captures_window_and_branch(config, probes) -> None:
    snap = ContextEngine(config, probes).capture()
    assert snap.active_app == "Code.exe"
    assert snap.git_branch == "feat/overlay"


def test_redacts_pii_from_window_title(config, probes) -> None:
    p = Probes(
        active_window=lambda: ("mail.exe", "alice@corp.com - Inbox"),
        idle_seconds=lambda: 0.0,
        clipboard=lambda: None,
        git_branch=lambda _c: None,
    )
    snap = ContextEngine(config, p).capture()
    assert "alice" not in snap.window_title
    assert "[REDACTED_EMAIL]" in snap.window_title


def test_redaction_can_be_disabled(config) -> None:
    config.redact_pii = False
    p = Probes(
        active_window=lambda: ("mail.exe", "alice@corp.com"),
        idle_seconds=lambda: 0.0,
        clipboard=lambda: None,
        git_branch=lambda _c: None,
    )
    assert "alice@corp.com" in ContextEngine(config, p).capture().window_title


def test_clipboard_toggle_honoured(config, probes) -> None:
    config.track_clipboard = False
    assert ContextEngine(config, probes).capture().clipboard_hash is None


def test_unchanged_clipboard_is_dropped_after_first_read(config, probes) -> None:
    engine = ContextEngine(config, probes)
    assert engine.capture().clipboard_hash is not None
    assert engine.capture().clipboard_hash is None


def test_working_dir_is_never_exposed(config, probes) -> None:
    assert ContextEngine(config, probes).capture().working_dir is None


def test_unsaved_marker_becomes_a_flag_not_a_title(config) -> None:
    p = Probes(
        active_window=lambda: ("Code.exe", "*app.py - Visual Studio Code"),
        idle_seconds=lambda: 0.0,
        clipboard=lambda: None,
        git_branch=lambda _c: None,
    )
    snap = ContextEngine(config, p).capture()
    assert snap.document_modified is True
    assert not snap.window_title.startswith("*")
    assert snap.window_title == "app.py - Visual Studio Code"


def test_clean_title_reports_no_modification(config, probes) -> None:
    assert ContextEngine(config, probes).capture().document_modified is False


def test_dwell_grows_while_the_app_is_unchanged(config, probes) -> None:
    engine = ContextEngine(config, probes)
    assert engine.capture().app_dwell_seconds == 0.0
    assert engine.capture().app_dwell_seconds >= 0.0


def test_dwell_resets_when_the_app_changes(config) -> None:
    apps = iter([("Code.exe", "a.py"), ("chrome.exe", "b"), ("Code.exe", "a.py")])
    p = Probes(
        active_window=lambda: next(apps),
        idle_seconds=lambda: 0.0,
        clipboard=lambda: None,
        git_branch=lambda _c: None,
    )
    engine = ContextEngine(config, p)
    assert engine.capture().app_dwell_seconds == 0.0
    assert engine.capture().app_dwell_seconds == 0.0  # switched away
    assert engine.capture().app_dwell_seconds == 0.0  # came back


def test_dwell_is_not_claimed_while_idle(config) -> None:
    p = Probes(
        active_window=lambda: ("Code.exe", "a.py"),
        idle_seconds=lambda: 600.0,
        clipboard=lambda: None,
        git_branch=lambda _c: None,
    )
    engine = ContextEngine(config, p)
    engine.capture()
    assert engine.capture().app_dwell_seconds == 0.0


def test_new_context_fields_reach_the_prompt(snapshot) -> None:
    snapshot.app_dwell_seconds = 3600.0
    snapshot.document_modified = True
    prompt = snapshot.to_llm_prompt()
    assert "minutes_in_app: 60" in prompt
    assert "document_modified: true" in prompt


def test_probe_failure_degrades_gracefully(config) -> None:
    def boom() -> tuple[str, str]:
        raise RuntimeError("win32 exploded")

    p = Probes(active_window=boom, idle_seconds=lambda: 0.0,
               clipboard=lambda: None, git_branch=lambda _c: None)
    snap = ContextEngine(config, p).capture()
    assert snap.active_app == "unknown"


def test_idle_detection(snapshot) -> None:
    snapshot.idle_seconds = 200.0
    assert snapshot.is_idle(180.0) is True
    assert snapshot.is_idle(300.0) is False


def test_fingerprint_is_stable_and_context_sensitive() -> None:
    a = ContextSnapshot(active_app="Code.exe", window_title="a.py")
    b = ContextSnapshot(active_app="Code.exe", window_title="a.py")
    c = ContextSnapshot(active_app="Code.exe", window_title="b.py")
    assert a.fingerprint() == b.fingerprint()
    assert a.fingerprint() != c.fingerprint()


def test_prompt_omits_working_dir_and_raw_clipboard(snapshot) -> None:
    snapshot.clipboard_hash = "abc123def456"
    prompt = snapshot.to_llm_prompt()
    assert "abc123def456" in prompt
    assert "working_dir" not in prompt
