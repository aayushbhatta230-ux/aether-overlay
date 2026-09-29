"""Local SQLite vault: suggestion history, decisions, and an audit trail.

The vault is what makes cooldown, dedupe, and the "no nagging" guarantees
auditable. It stores only redacted suggestion text and metadata - never the
raw context snapshot that produced it.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections.abc import Iterable
from contextlib import closing
from pathlib import Path

from .models import Suggestion

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS suggestions (
    id          TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL,
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,
    risk        TEXT NOT NULL,
    confidence  REAL NOT NULL,
    source      TEXT NOT NULL,
    created_at  REAL NOT NULL,
    decision    TEXT,
    decided_at  REAL
);
CREATE INDEX IF NOT EXISTS idx_suggestions_fp  ON suggestions(fingerprint);
CREATE INDEX IF NOT EXISTS idx_suggestions_time ON suggestions(created_at);

CREATE TABLE IF NOT EXISTS audit (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        REAL NOT NULL,
    event     TEXT NOT NULL,
    detail    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit(ts);
"""


class Vault:
    """Thin, explicit data-access layer. No ORM, no magic."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)
            conn.commit()

    # -- suggestions -----------------------------------------------------
    def record(self, suggestion: Suggestion) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO suggestions "
                "(id, fingerprint, title, body, risk, confidence, source, "
                " created_at, decision, decided_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    suggestion.id, suggestion.fingerprint, suggestion.title,
                    suggestion.body, suggestion.risk, suggestion.confidence,
                    suggestion.source, suggestion.created_at,
                    suggestion.decision, suggestion.decided_at,
                ),
            )
            conn.commit()

    def record_many(self, suggestions: Iterable[Suggestion]) -> None:
        items = list(suggestions)
        if not items:
            return
        with closing(self._connect()) as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO suggestions "
                "(id, fingerprint, title, body, risk, confidence, source, "
                " created_at, decision, decided_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                [
                    (s.id, s.fingerprint, s.title, s.body, s.risk, s.confidence,
                     s.source, s.created_at, s.decision, s.decided_at)
                    for s in items
                ],
            )
            conn.commit()

    def decide(self, suggestion_id: str, decision: str) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                "UPDATE suggestions SET decision = ?, decided_at = ? WHERE id = ?",
                (decision, time.time(), suggestion_id),
            )
            conn.commit()

    def seen_recently(self, fingerprint: str, within_s: float) -> bool:
        """True if this exact suggestion was shown within ``within_s`` seconds."""
        cutoff = time.time() - within_s
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT 1 FROM suggestions WHERE fingerprint = ? AND created_at > ? LIMIT 1",
                (fingerprint, cutoff),
            ).fetchone()
        return row is not None

    def count_since(self, since: float) -> int:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM suggestions WHERE created_at > ?", (since,)
            ).fetchone()
        return int(row["n"]) if row else 0

    def recent(self, limit: int = 20) -> list[Suggestion]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM suggestions ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            Suggestion.from_dict(
                {
                    "id": r["id"], "fingerprint": r["fingerprint"], "title": r["title"],
                    "body": r["body"], "risk": r["risk"], "confidence": r["confidence"],
                    "source": r["source"], "created_at": r["created_at"],
                    "decision": r["decision"], "decided_at": r["decided_at"],
                }
            )
            for r in rows
        ]

    # -- audit -----------------------------------------------------------
    def audit(self, event: str, detail: dict) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                "INSERT INTO audit (ts, event, detail) VALUES (?,?,?)",
                (time.time(), event, json.dumps(detail, default=str)),
            )
            conn.commit()

    def audit_trail(self, limit: int = 50) -> list[dict]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT ts, event, detail FROM audit ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            {"ts": r["ts"], "event": r["event"], "detail": json.loads(r["detail"])}
            for r in rows
        ]
