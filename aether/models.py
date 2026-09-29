"""Core domain types shared across AETHER."""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from .config import GATED_RISKS, RISK_ORDER


def _now() -> float:
    return time.time()


@dataclass(slots=True)
class ContextSnapshot:
    """A small, redacted picture of what the user is doing right now.

    Deliberately coarse: enough for an LLM to be useful, never enough to be
    a surveillance tool. No keystrokes, no screenshots, no file contents.
    """

    active_app: str = "unknown"
    window_title: str = ""
    idle_seconds: float = 0.0
    clipboard_hash: str | None = None
    git_branch: str | None = None
    working_dir: str | None = None
    #: How long the user has been in ``active_app`` without switching away.
    app_dwell_seconds: float = 0.0
    #: True when the window title carried an editor's "modified" marker.
    document_modified: bool = False
    captured_at: float = field(default_factory=_now)

    def is_idle(self, threshold_s: float) -> bool:
        return self.idle_seconds >= threshold_s

    def fingerprint(self) -> str:
        """Stable, non-reversible identity for a context.

        Used to tie audit rows to the situation that produced them without
        storing the situation itself: same app + same title + same branch
        always hashes to the same value.
        """
        raw = f"{self.active_app}|{self.window_title}|{self.git_branch or ''}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_llm_prompt(self) -> str:
        """Render the snapshot as the compact block handed to the LLM."""
        lines = [f"active_app: {self.active_app}"]
        if self.window_title:
            lines.append(f"window_title: {self.window_title}")
        if self.git_branch:
            lines.append(f"git_branch: {self.git_branch}")
        if self.clipboard_hash:
            lines.append(f"clipboard_fingerprint: {self.clipboard_hash}")
        if self.idle_seconds:
            lines.append(f"idle_seconds: {int(self.idle_seconds)}")
        if self.app_dwell_seconds >= 60:
            lines.append(f"minutes_in_app: {int(self.app_dwell_seconds // 60)}")
        if self.document_modified:
            lines.append("document_modified: true")
        return "\n".join(lines)


@dataclass(slots=True)
class Suggestion:
    """One ambient hint. ``risk`` drives the approval gate."""

    title: str
    body: str
    risk: str = "low"
    confidence: float = 0.5
    action: str | None = None
    source: str = "llm"
    fingerprint: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: float = field(default_factory=_now)
    decided_at: float | None = None
    decision: str | None = None

    def __post_init__(self) -> None:
        self.risk = (self.risk or "low").strip().lower()
        if self.risk not in RISK_ORDER:
            # An unrecognised tier is treated as the *more* cautious one: a
            # model that invents "critical" gets gated, not waved through.
            self.risk = "medium" if self.risk else "low"
        self.confidence = min(1.0, max(0.0, float(self.confidence)))
        if not self.fingerprint:
            self.fingerprint = self.compute_fingerprint()

    def compute_fingerprint(self) -> str:
        raw = f"{self.title.strip().lower()}|{self.body.strip().lower()[:120]}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    @property
    def is_gated(self) -> bool:
        """True when a human must approve before this can act."""
        return self.risk in GATED_RISKS

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Suggestion:
        allowed = {
            "title", "body", "risk", "confidence", "action",
            "source", "fingerprint", "id", "created_at",
            "decided_at", "decision",
        }
        return cls(**{k: v for k, v in data.items() if k in allowed})