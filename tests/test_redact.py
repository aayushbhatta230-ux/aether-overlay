"""PII redaction and title normalisation."""

from __future__ import annotations

import pytest

from aether.redact import fingerprint_clipboard, normalize_title, redact


@pytest.mark.parametrize(
    "raw,marker",
    [
        ("contact me at bob@example.com", "[REDACTED_EMAIL]"),
        ("my key is sk-abcdef0123456789abcdef", "[REDACTED_KEY]"),
        ("token ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345", "[REDACTED_TOKEN]"),
        ("card 4111 1111 1111 1111", "[REDACTED_CARD]"),
        ("call +1 (555) 010-9999 now", "[REDACTED_PHONE]"),
        ("C:\\Users\\joshi\\secret\\file.txt", "[REDACTED_PATH]"),
        ("host 192.168.1.42 is up", "[REDACTED_IP]"),
    ],
)
def test_redacts_sensitive_spans(raw: str, marker: str) -> None:
    assert marker in redact(raw)


def test_redaction_preserves_harmless_text() -> None:
    text = "app.py - Visual Studio Code"
    assert redact(text) == text


def test_email_redaction_does_not_leak_local_part() -> None:
    out = redact("write to alice@corp.internal please")
    assert "alice" not in out
    assert "corp.internal" not in out


def test_normalize_title_strips_browser_suffix() -> None:
    assert normalize_title("Rust docs - Google Chrome") == "Rust docs"


def test_normalize_title_caps_length() -> None:
    out = normalize_title("x" * 500)
    assert len(out) <= 120


def test_normalize_title_handles_empty() -> None:
    assert normalize_title("") == ""


def test_fingerprint_is_stable_and_never_reveals_content() -> None:
    a = fingerprint_clipboard("secret value")
    b = fingerprint_clipboard("secret value")
    assert a == b
    assert "secret" not in (a or "")
    assert fingerprint_clipboard("different") != a


def test_fingerprint_of_empty_clipboard_is_none() -> None:
    assert fingerprint_clipboard("") is None
    assert fingerprint_clipboard("   \n ") is None
