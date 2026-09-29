"""The risk gate.

AETHER's defining safety property: **it observes, it never acts.**
A suggestion is data, not an instruction. This module is the single place
where that policy is enforced and audited, so it stays testable in isolation.

Policy:

* ``low``    - shown immediately; informational only.
* ``medium`` - shown but marked as requiring approval; AETHER will not run it.
* ``high``   - held back entirely and recorded, shown only as a warning.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from .config import Config
from .models import Suggestion

log = logging.getLogger(__name__)

#: Reasons recorded in the audit trail.
BLOCK_HIGH = "blocked_high_risk"
HELD_FOR_APPROVAL = "held_for_approval"
ALLOWED = "allowed"


def gate(suggestion: Suggestion) -> tuple[bool, str]:
    """Return ``(should_display, reason)`` for a single suggestion."""
    if suggestion.risk == "high":
        return (False, BLOCK_HIGH)
    if suggestion.is_gated:
        return (True, HELD_FOR_APPROVAL)
    return (True, ALLOWED)


def filter_suggestions(
    suggestions: Iterable[Suggestion], config: Config | None = None
) -> list[Suggestion]:
    """Apply the gate to a batch, then order by confidence (best first)."""
    allowed: list[Suggestion] = []
    for sug in suggestions:
        ok, reason = gate(sug)
        if ok:
            allowed.append(sug)
        else:
            log.info("suggestion %r withheld: %s", sug.title, reason)
    allowed.sort(key=lambda s: s.confidence, reverse=True)
    return allowed


def requires_approval(suggestion: Suggestion) -> bool:
    """True if a human must explicitly accept before any action is taken."""
    return suggestion.is_gated


def never_auto_execute() -> bool:
    """Invariant asserted by the test suite: AETHER has no execute path.

    Returning a constant ``False`` is intentional - it documents the design
    decision in executable form rather than only in prose.
    """
    return False
