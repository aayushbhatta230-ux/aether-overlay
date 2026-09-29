"""The risk gate.

AETHER's defining safety property: **it observes, it never acts.**
A suggestion is data, not an instruction. This module is the single place
where that policy is enforced and audited, so it stays testable in isolation.

Policy:

* ``low``    - shown immediately; informational only.
* ``medium`` - shown but marked as requiring approval; AETHER will not run it.
                Can be switched off entirely with ``allow_medium_risk=false``.
* ``high``   - withheld. Never displayed, never stored as a suggestion, and
                recorded in the audit trail so the withholding is provable.

There is deliberately no branch here that turns a suggestion into an action:
:func:`never_auto_execute` documents that invariant in executable form.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from .config import Config
from .models import Suggestion

log = logging.getLogger(__name__)

#: Reasons recorded in the audit trail.
BLOCK_HIGH = "blocked_high_risk"
BLOCK_MEDIUM_DISABLED = "blocked_medium_disabled"
HELD_FOR_APPROVAL = "held_for_approval"
ALLOWED = "allowed"


def gate(suggestion: Suggestion, config: Config | None = None) -> tuple[bool, str]:
    """Return ``(should_display, reason)`` for a single suggestion."""
    if suggestion.risk == "high":
        return (False, BLOCK_HIGH)
    if suggestion.is_gated:
        if config is not None and not config.allow_medium_risk:
            return (False, BLOCK_MEDIUM_DISABLED)
        return (True, HELD_FOR_APPROVAL)
    return (True, ALLOWED)


def partition_suggestions(
    suggestions: Iterable[Suggestion], config: Config | None = None
) -> tuple[list[Suggestion], list[tuple[Suggestion, str]]]:
    """Split a batch into ``(allowed, blocked)``.

    ``blocked`` keeps the reason so the caller can audit what was withheld
    instead of dropping it silently. Allowed suggestions come back sorted by
    confidence, best first.
    """
    allowed: list[Suggestion] = []
    blocked: list[tuple[Suggestion, str]] = []
    for sug in suggestions:
        ok, reason = gate(sug, config)
        if ok:
            allowed.append(sug)
        else:
            blocked.append((sug, reason))
            log.info("suggestion %r withheld: %s", sug.title, reason)
    allowed.sort(key=lambda s: s.confidence, reverse=True)
    return allowed, blocked


def filter_suggestions(
    suggestions: Iterable[Suggestion], config: Config | None = None
) -> list[Suggestion]:
    """Apply the gate to a batch and return only what may be displayed."""
    return partition_suggestions(suggestions, config)[0]


def requires_approval(suggestion: Suggestion) -> bool:
    """True if a human must explicitly accept before any action is taken."""
    return suggestion.is_gated


def never_auto_execute() -> bool:
    """Invariant asserted by the test suite: AETHER has no execute path.

    Returning a constant ``False`` is intentional - it documents the design
    decision in executable form rather than only in prose.
    """
    return False