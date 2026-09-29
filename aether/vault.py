"""Local SQLite vault: suggestion history, decisions, snoozes, and an audit trail.

The vault is what makes cooldown, dedupe, and the "no nagging" guarantees
auditable. It stores only redacted suggestion text and metadata - never the
raw context snapshot that produced it.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
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

-- "Never show me this again": a user-driven block that outlives the cooldown.
CREATE TABLE IF NOT EXISTS suppressed (
    fingerprint TEXT PRIMARY KEY,
    until       REAL NOT NULL,
    reason      TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_suppressed_until ON suppressed(until);

CREATE TABLE IF NOT EXISTS audit (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        REAL NOT NULL,
    event     TEXT NOT NULL,
    detail    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit(ts);
"""

_INSERT = (
    "INSERT OR REPLACE INTO suggestions "
    "(id, fingerprint, title, body, risk, confidence, source, "
    " created_at, decision, decided_at) VALUES (?,?,?,?,?,?,?,?,?,?)"
)


def _row_to_suggestion(row: sqlite3.Row) -> Suggestion:
    return Suggestion.from_dict(
        {
            "id": row["id"],
            "fingerprint": row["fingerprint"],
            "title": row["title"],
            "body": row["body"],
            "risk": row["risk"],
            "confidence": row["confidence"],
            "source": row["source"],
            "created_at": row["created_at"],
            "decision": row["decision"],
            "decided_at": row["decided_at"],
        }
    )


def _as_suggestion(s: Suggestion) -> tuple:
    return (
        s.id, s.fingerprint, s.title, s.body, s.risk, s.confidence,
        s.source, s.created_at, s.decision, s.decided_at,
    )


class Vault:
    """Thin, explicit data-access layer. No ORM, no magic."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        # A fresh connection per call: the poller thread and the Tk thread both
        # touch the vault, and this keeps them from sharing a handle.
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
            conn.execute(_INSERT, _as_suggestion(suggestion))
            conn.commit()

    def record_many(self, suggestions) -> None:
        items = list(suggestions)
        if not items:
            return
        with closing(self._connect()) as conn:
            conn.executemany(_INSERT, [_as_suggestion(s) for s in items])
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
        return [_row_to_suggestion(r) for r in rows]

    # -- snoozes ("never show me this again") ----------------------------
    def suppress(self, fingerprint: str, for_s: float, reason: str = "snoozed") -> None:
        """Block ``fingerprint`` for ``for_s`` seconds. ``0`` blocks forever."""
        now = time.time()
        self.suppress_until(fingerprint, float("inf") if for_s <= 0 else now + for_s, reason)

    def suppress_until(self, fingerprint: str, until: float, reason: str = "snoozed") -> None:
        """Block ``fingerprint`` until an absolute timestamp.

        The primitive behind :meth:`suppress`; also how a caller records a
        block that has already lapsed.
        """
        now = time.time()
        with closing(self._connect()) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO suppressed (fingerprint, until, reason, created_at) "
                "VALUES (?,?,?,?)",
                (fingerprint, float(until), reason, now),
            )
            conn.commit()

    def is_suppressed(self, fingerprint: str) -> bool:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT 1 FROM suppressed WHERE fingerprint = ? AND until > ? LIMIT 1",
                (fingerprint, time.time()),
            ).fetchone()
        return row is not None

    def active_suppressions(self) -> list[dict]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT fingerprint, until, reason FROM suppressed "
                "WHERE until > ? ORDER BY until DESC",
                (time.time(),),
            ).fetchall()
        return [
            {"fingerprint": r["fingerprint"], "until": r["until"], "reason": r["reason"]}
            for r in rows
        ]

    def unsuppress(self, fingerprint: str) -> bool:
        with closing(self._connect()) as conn:
            cur = conn.execute(
                "DELETE FROM suppressed WHERE fingerprint = ?", (fingerprint,)
            )
            conn.commit()
        return cur.rowcount > 0

    def clear_suppressions(self) -> int:
        with closing(self._connect()) as conn:
            cur = conn.execute("DELETE FROM suppressed")
            conn.commit()
        return cur.rowcount

    def prune_suppressions(self) -> int:
        with closing(self._connect()) as conn:
            cur = conn.execute("DELETE FROM suppressed WHERE until <= ?", (time.time(),))
            conn.commit()
        return cur.rowcount

    # -- retention -------------------------------------------------------
    def purge(self, older_than_days: float | None = None) -> dict[str, int]:
        """Delete history. ``None`` wipes everything; otherwise anything older
        than ``older_than_days`` is removed. Returns per-table row counts."""
        counts: dict[str, int] = {}
        with closing(self._connect()) as conn:
            if older_than_days is None or older_than_days <= 0:
                for table in ("suggestions", "audit", "suppressed"):
                    counts[table] = conn.execute(f"DELETE FROM {table}").rowcount
            else:
                cutoff = time.time() - older_than_days * 86400.0
                counts["suggestions"] = conn.execute(
                    "DELETE FROM suggestions WHERE created_at < ?", (cutoff,)
                ).rowcount
                counts["audit"] = conn.execute(
                    "DELETE FROM audit WHERE ts < ?", (cutoff,)
                ).rowcount
                counts["suppressed"] = conn.execute(
                    "DELETE FROM suppressed WHERE created_at < ?", (cutoff,)
                ).rowcount
            conn.commit()
        return counts

    def stats(self) -> dict:
        """Aggregate counters for ``aether --stats``."""
        now = time.time()
        with closing(self._connect()) as conn:
            total = conn.execute("SELECT COUNT(*) AS n FROM suggestions").fetchone()["n"]
            last_24h = conn.execute(
                "SELECT COUNT(*) AS n FROM suggestions WHERE created_at > ?", (now - 86400,)
            ).fetchone()["n"]
            last_hour = conn.execute(
                "SELECT COUNT(*) AS n FROM suggestions WHERE created_at > ?", (now - 3600,)
            ).fetchone()["n"]
            dismissed = conn.execute(
                "SELECT COUNT(*) AS n FROM suggestions WHERE decision = 'dismissed'"
            ).fetchone()["n"]
            accepted = conn.execute(
                "SELECT COUNT(*) AS n FROM suggestions WHERE decision = 'accepted'"
            ).fetchone()["n"]
            by_source = {
                r["source"]: r["n"]
                for r in conn.execute(
                    "SELECT source, COUNT(*) AS n FROM suggestions GROUP BY source"
                )
            }
            by_risk = {
                r["risk"]: r["n"]
                for r in conn.execute(
                    "SELECT risk, COUNT(*) AS n FROM suggestions GROUP BY risk"
                )
            }
            suppressed = conn.execute(
                "SELECT COUNT(*) AS n FROM suppressed WHERE until > ?", (now,)
            ).fetchone()["n"]
            oldest = conn.execute(
                "SELECT MIN(created_at) AS t FROM suggestions"
            ).fetchone()["t"]
        return {
            "total": total,
            "last_hour": last_hour,
            "last_24h": last_24h,
            "dismissed": dismissed,
            "accepted": accepted,
            "suppressed": suppressed,
            "by_source": by_source,
            "by_risk": by_risk,
            "oldest": oldest,
            "db_path": str(self.db_path),
        }

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
        out = []
        for r in rows:
            try:
                detail = json.loads(r["detail"])
            except json.JSONDecodeError:  # pragma: no cover - defensive
                detail = {"raw": r["detail"]}
            out.append({"ts": r["ts"], "event": r["event"], "detail": detail})
        return out