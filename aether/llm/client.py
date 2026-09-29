"""Ollama-backed suggestion generation.

Design rules for this module:

* **Never hang.** Every network call has a hard timeout.
* **Never trust the model.** Output is parsed defensively and anything that
  does not validate as a well-formed suggestion is dropped.
* **Never go dark.** If Ollama is unreachable, deterministic rules produce a
  usable suggestion instead of silence.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Protocol

from ..config import Config
from ..models import ContextSnapshot, Suggestion

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are AETHER, a discreet ambient assistant. You look at a very small,
redacted snapshot of what the user is doing right now and offer ONE short,
genuinely useful suggestion.

Hard rules:
- Reply with raw JSON only. No prose, no markdown fences.
- At most 2 suggestions.
- Each suggestion: title (<= 60 chars), body (<= 180 chars), risk, confidence.
- risk must be exactly one of: low, medium, high.
  low    = purely informational, nothing is changed
  medium = would open, move, or create a file/tab
  high   = would send data, spend money, or delete something
- Never invent facts about the user. Never include credentials or PII.
- If the context is not interesting, return an empty list: {"suggestions": []}
"""

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


class OllamaTransport(Protocol):
    """Minimal transport contract, so tests can inject a fake."""

    def generate(self, model: str, prompt: str, system: str, timeout_s: float) -> str:
        ...


class HttpOllamaTransport:
    """Talks to a local Ollama daemon over plain HTTP via ``urllib``.

    ``urllib`` is used instead of a third-party client so AETHER has no
    network dependency beyond the standard library.
    """

    def __init__(self, host: str) -> None:
        self.host = host.rstrip("/")

    def generate(self, model: str, prompt: str, system: str, timeout_s: float) -> str:
        import urllib.request

        payload = json.dumps(
            {
                "model": model,
                "prompt": prompt,
                "system": system,
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.4, "num_predict": 400},
            }
        ).encode("utf-8")

        req = urllib.request.Request(
            f"{self.host}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace"))
        return str(body.get("response", ""))


class NullTransport:
    """Always fails - used to force the offline path in tests."""

    def generate(self, model: str, prompt: str, system: str, timeout_s: float) -> str:
        raise ConnectionError("no LLM transport configured")


def parse_suggestions(raw: str) -> list[Suggestion]:
    """Parse model output into validated :class:`Suggestion` objects.

    Tolerant of markdown fences and surrounding chatter, strict about the
    fields themselves. Invalid entries are skipped, not raised on.
    """
    if not raw or not raw.strip():
        return []

    candidates: list[Any] = []
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            candidates = list(data.get("suggestions") or [])
        elif isinstance(data, list):
            candidates = data
    except json.JSONDecodeError:
        block = _JSON_BLOCK.search(raw)
        if block:
            try:
                data = json.loads(block.group(0))
                if isinstance(data, dict):
                    candidates = list(data.get("suggestions") or [])
                elif isinstance(data, list):
                    candidates = data
            except json.JSONDecodeError:
                log.debug("could not recover JSON from model output")

    out: list[Suggestion] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        body = str(item.get("body", "")).strip()
        if not title or not body:
            continue
        try:
            confidence = float(item.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        out.append(
            Suggestion(
                title=title[:60],
                body=body[:180],
                risk=str(item.get("risk", "low")),
                confidence=confidence,
                action=str(item["action"]) if item.get("action") else None,
                source="llm",
            )
        )
    return out[:2]



def _rule_based(snapshot: ContextSnapshot) -> list[Suggestion]:
    """Deterministic fallback used when the LLM is unavailable."""
    title = snapshot.window_title.lower()

    if "error" in title or "traceback" in title:
        return [
            Suggestion(
                title="Read the error before editing",
                body="The active window looks like a failure. Capture the exact "
                     "message and stack before changing anything.",
                risk="low",
                confidence=0.6,
                source="rules",
            )
        ]
    if snapshot.git_branch and snapshot.git_branch.lower() not in ("main", "master"):
        return [
            Suggestion(
                title=f"On branch '{snapshot.git_branch}'",
                body="Not a default branch - worth a commit before switching context.",
                risk="low",
                confidence=0.45,
                source="rules",
            )
        ]
    if snapshot.clipboard_hash:
        return [
            Suggestion(
                title="Clipboard changed",
                body="You copied something recently. Worth confirming it went "
                     "where you intended.",
                risk="low",
                confidence=0.35,
                source="rules",
            )
        ]
    if snapshot.is_idle(120):
        return [
            Suggestion(
                title="You've been idle a while",
                body="Good moment to stretch, or to jot down the next task.",
                risk="low",
                confidence=0.3,
                source="rules",
            )
        ]
    return []


class SuggestionEngine:
    """Turns snapshots into suggestions, with LLM and rule-based paths."""

    def __init__(self, config: Config, transport: OllamaTransport | None = None) -> None:
        self.config = config
        self.transport = transport or HttpOllamaTransport(config.ollama_host)

    def generate(self, snapshot: ContextSnapshot) -> list[Suggestion]:
        if not snapshot.active_app or snapshot.active_app == "unknown":
            # Still allow rules (idle hint) but do not bother the LLM.
            return _rule_based(snapshot)

        prompt = (
            "User context right now:\n"
            f"{snapshot.to_llm_prompt()}\n\n"
            'Reply as: {"suggestions": [{"title": ..., "body": ..., '
            '"risk": ..., "confidence": ...}]}'
        )

        try:
            raw = self.transport.generate(
                self.config.ollama_model, prompt, SYSTEM_PROMPT, self.config.ollama_timeout_s
            )
        except Exception as exc:
            log.warning("LLM unavailable (%s); using rule fallback", exc)
            return _rule_based(snapshot) if self.config.offline_fallback else []

        suggestions = parse_suggestions(raw)
        if not suggestions and self.config.offline_fallback:
            return _rule_based(snapshot)
        return suggestions

