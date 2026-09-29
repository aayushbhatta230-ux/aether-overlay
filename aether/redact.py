"""Deterministic PII redaction.

This runs *before* anything is sent to the LLM. It is intentionally
regex-based and conservative: over-redacting a window title is a far better
failure mode than leaking an API key into a model prompt.
"""

from __future__ import annotations

import hashlib
import re
from re import Pattern

#: Applied in order. Order matters: specific patterns run before generic digit
#: runs, otherwise a credit card would be mangled into five separate matches.
_RULES: tuple[tuple[str, Pattern[str]], ...] = (
    # Private keys / long hex-or-base64 secrets.
    ("[REDACTED_KEY]", re.compile(r"\bsk-[A-Za-z0-9]{16,}\b")),
    ("[REDACTED_TOKEN]", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}\b")),
    ("[REDACTED_KEY]", re.compile(r"\bey[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b")),
    ("[REDACTED_TOKEN]", re.compile(r"\b[A-Fa-f0-9]{32,}\b")),
    # Credentials embedded in URLs.
    ("[REDACTED_URL]", re.compile(r"(?<=://)[^/\s:@]+:[^/\s@]+@")),
    # Email addresses.
    ("[REDACTED_EMAIL]", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]{2,}\b")),
    # Credit-card-like runs (13-19 digits, optionally spaced/dashed).
    ("[REDACTED_CARD]", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    # Phone numbers (international-ish, 7+ digits with separators).
    ("[REDACTED_PHONE]", re.compile(r"(?<![\w.])\+\d[\d\s().-]{7,}\d")),
    # Windows user paths -> keep the tail, drop the account name.
    ("[REDACTED_PATH]", re.compile(r"[A-Za-z]:\\Users\\[^\\\s]+")),
    # Bare IP addresses.
    ("[REDACTED_IP]", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
)

#: Title-bar noise that carries no signal but a lot of noise. The editor's
#: leading ``*`` is deliberately *not* here: it means "unsaved changes", which
#: is signal, so it is lifted into a flag by the context engine instead.
_NOISE_TOKENS = (
    " - Google Chrome", " - Microsoft Edge", " - Firefox",
    "Administrator:", " - Notepad", " - Windows Explorer",
)

#: Characters editors prepend to a title to flag unsaved work.
_MODIFIED_MARKERS = ("*", "•", "●")


def redact(text: str) -> str:
    """Replace every known-sensitive span in ``text`` with a marker."""
    if not text:
        return text
    out = text
    for replacement, pattern in _RULES:
        out = pattern.sub(replacement, out)
    return out


def strip_modified_marker(title: str) -> tuple[bool, str]:
    """Split an editor's "unsaved changes" marker off the front of a title.

    Returns ``(is_modified, cleaned_title)``. Keeping this out of
    :func:`normalize_title` preserves the signal instead of discarding it.
    """
    if not title:
        return (False, title)
    stripped = title.lstrip()
    modified = stripped.startswith(_MODIFIED_MARKERS)
    if modified:
        cleaned = stripped.lstrip("".join(_MODIFIED_MARKERS)).strip()
        return (bool(cleaned), cleaned)
    return (False, stripped)


def normalize_title(title: str, max_len: int = 120) -> str:
    """Trim browser suffixes, collapse whitespace, and cap the length.

    The "unsaved changes" marker is *not* touched here - it is signal, and
    :func:`strip_modified_marker` lifts it into a flag separately.
    """
    if not title:
        return ""
    cleaned = " ".join(title.split())
    for token in _NOISE_TOKENS:
        if cleaned.endswith(token):
            cleaned = cleaned[: -len(token)]
    cleaned = cleaned.strip()
    if len(cleaned) > max_len:
        cleaned = cleaned[: max_len - 1].rstrip() + "…"
    return cleaned


def fingerprint_clipboard(text: str) -> str | None:
    """Return a stable short hash of clipboard contents, never the content.

    ``None`` is returned for empty or whitespace-only clipboards so the
    context engine can distinguish "empty clipboard" from "unchanged".
    """
    if not text or not text.strip():
        return None
    digest = hashlib.sha256(text.strip().encode("utf-8", "ignore")).hexdigest()
    return digest[:12]