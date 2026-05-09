"""SQLite metadata store. NEVER stores images. Only spatial metadata."""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from typing import Optional


def _utc_now_iso() -> str:
    """All timestamps in storage are UTC ISO-8601 with 'Z'. The API contract
    is documented: clients should send UTC. Fixes a real bug we hit where
    local-time visits couldn't be matched against UTC broadcast windows."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT NOT NULL,
    csi_level       TEXT,
    csi_variance    REAL,
    total_visible   INTEGER,
    overall_density TEXT,
    spatial_issue   TEXT,
    chokepoints     TEXT,   -- JSON array
    clusters        TEXT,   -- JSON array
    raw_metadata    TEXT    -- JSON dump of the full observation (still NO images)
);
CREATE INDEX IF NOT EXISTS idx_observations_ts ON observations(timestamp);

CREATE TABLE IF NOT EXISTS occupancy_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT NOT NULL,
    level           TEXT,
    variance        REAL,
    variance_ratio  REAL,
    count_estimate  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_occ_ts ON occupancy_log(timestamp);

-- Governance / accountability: every AI judgment gets logged here so the
-- operator can review, accept, reject, or annotate. Public transparency
-- pages can read the redacted (summary + status) view of this same table.
CREATE TABLE IF NOT EXISTS ai_decisions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT NOT NULL,
    decision_type   TEXT NOT NULL,            -- 'spatial_analysis' | 'space_report' | 'chat'
    model           TEXT,                     -- e.g. 'claude-sonnet-4-5' or 'stub'
    summary         TEXT,                     -- one-line plain-language description
    raw_input       TEXT,                     -- JSON; redacted from public view
    raw_output      TEXT,                     -- JSON; redacted from public view
    operator_status TEXT NOT NULL DEFAULT 'pending',  -- pending|considered|accepted|rejected
    operator_notes  TEXT,
    operator_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_aidec_ts ON ai_decisions(created_at);

-- Anonymous community feedback. No identifiers, ever.
CREATE TABLE IF NOT EXISTS community_feedback (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT NOT NULL,
    sentiment       TEXT,    -- 'concern' | 'praise' | 'suggestion'
    message         TEXT NOT NULL,
    zone            TEXT
);
CREATE INDEX IF NOT EXISTS idx_cf_ts ON community_feedback(created_at);

-- Persistent visit log. The token_id is the per-user pseudonym chosen by the
-- client; the system does not know who that is. Used to power exposure
-- broadcasts ("everyone who visited Main between 12:00 and 13:00").
CREATE TABLE IF NOT EXISTS visits (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    token_id                 TEXT NOT NULL,
    rotating_id              TEXT,
    zone                     TEXT NOT NULL,
    visited_at               TEXT NOT NULL,
    self_reported_crowded    INTEGER DEFAULT 0,
    self_reported_sick       INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_visits_zone_time ON visits(zone, visited_at);
CREATE INDEX IF NOT EXISTS idx_visits_token     ON visits(token_id);

-- In-app notification inbox per token. Consumers fetch /api/consumer/notifications
-- with their own token_id to read; business broadcasts write here, then push
-- notifications fire (best-effort) over the existing pywebpush path.
CREATE TABLE IF NOT EXISTS notifications (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    token_id          TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    notification_type TEXT,        -- 'exposure' | 'crowding' | 'general'
    title             TEXT,
    body              TEXT,
    zone              TEXT,
    exposure_date     TEXT,        -- when the recipient was potentially exposed
    read_at           TEXT
);
CREATE INDEX IF NOT EXISTS idx_notif_token ON notifications(token_id, created_at);

-- Privacy invariant in the schema itself: there's nowhere to put an image.
-- And nowhere to put a name, email, IP, MAC, device fingerprint, or any
-- other identifier of the people whose space this is. This is intentional.
"""


class Store:
    def __init__(self, path: str = "echolocate.db"):
        self.path = path
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def add_observation(self, observation: dict) -> int:
        c = self._conn.cursor()
        c.execute(
            """INSERT INTO observations
               (timestamp, csi_level, csi_variance, total_visible, overall_density,
                spatial_issue, chokepoints, clusters, raw_metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                observation.get("timestamp", _utc_now_iso()),
                (observation.get("csi_occupancy") or {}).get("level"),
                (observation.get("csi_occupancy") or {}).get("variance"),
                observation.get("total_people_visible"),
                observation.get("overall_density"),
                observation.get("spatial_issue"),
                json.dumps(observation.get("chokepoints", [])),
                json.dumps(observation.get("clusters", [])),
                json.dumps(observation),
            ),
        )
        self._conn.commit()
        return c.lastrowid or 0

    def add_occupancy(self, occupancy: dict) -> None:
        c = self._conn.cursor()
        c.execute(
            """INSERT INTO occupancy_log
               (timestamp, level, variance, variance_ratio, count_estimate)
               VALUES (?, ?, ?, ?, ?)""",
            (
                _utc_now_iso(),
                occupancy.get("level"),
                occupancy.get("variance"),
                occupancy.get("variance_ratio"),
                occupancy.get("count_estimate"),
            ),
        )
        self._conn.commit()

    def list_observations(self, limit: int = 200) -> list[dict]:
        c = self._conn.cursor()
        c.execute(
            "SELECT raw_metadata FROM observations ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        rows = c.fetchall()
        return [json.loads(r[0]) for r in rows]

    # ---------- AI decision log ----------

    def add_ai_decision(self, *, decision_type: str, model: str, summary: str,
                        raw_input: dict | None = None,
                        raw_output: dict | None = None) -> int:
        c = self._conn.cursor()
        c.execute(
            """INSERT INTO ai_decisions
               (created_at, decision_type, model, summary, raw_input, raw_output)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                _utc_now_iso(),
                decision_type, model, summary,
                json.dumps(raw_input or {}),
                json.dumps(raw_output or {}),
            ),
        )
        self._conn.commit()
        return c.lastrowid or 0

    def list_ai_decisions(self, limit: int = 100, public: bool = False) -> list[dict]:
        """`public=True` strips raw_input/raw_output; only metadata + summary +
        operator decision are returned. This is what the transparency page sees."""
        c = self._conn.cursor()
        c.execute(
            """SELECT id, created_at, decision_type, model, summary,
                      raw_input, raw_output,
                      operator_status, operator_notes, operator_at
               FROM ai_decisions ORDER BY id DESC LIMIT ?""",
            (limit,),
        )
        rows = []
        for r in c.fetchall():
            row = {
                "id": r[0], "created_at": r[1], "decision_type": r[2],
                "model": r[3], "summary": r[4],
                "operator_status": r[7], "operator_notes": r[8],
                "operator_at": r[9],
            }
            if not public:
                row["raw_input"] = json.loads(r[5] or "{}")
                row["raw_output"] = json.loads(r[6] or "{}")
            rows.append(row)
        return rows

    def update_ai_decision(self, decision_id: int, *, status: str,
                           notes: str | None = None) -> bool:
        if status not in ("pending", "considered", "accepted", "rejected"):
            return False
        c = self._conn.cursor()
        c.execute(
            """UPDATE ai_decisions
               SET operator_status = ?, operator_notes = ?, operator_at = ?
               WHERE id = ?""",
            (status, notes,
             _utc_now_iso(),
             decision_id),
        )
        self._conn.commit()
        return c.rowcount > 0

    def ai_decision_stats(self) -> dict:
        c = self._conn.cursor()
        c.execute("SELECT operator_status, COUNT(*) FROM ai_decisions GROUP BY operator_status")
        by_status = {row[0]: row[1] for row in c.fetchall()}
        c.execute("SELECT decision_type, COUNT(*) FROM ai_decisions GROUP BY decision_type")
        by_type = {row[0]: row[1] for row in c.fetchall()}
        c.execute("SELECT COUNT(*) FROM ai_decisions")
        total = c.fetchone()[0]
        return {"total": total, "by_status": by_status, "by_type": by_type}

    # ---------- Community feedback ----------

    def add_community_feedback(self, *, sentiment: str, message: str,
                               zone: str | None = None) -> int:
        if sentiment not in ("concern", "praise", "suggestion"):
            sentiment = "concern"
        c = self._conn.cursor()
        c.execute(
            """INSERT INTO community_feedback (created_at, sentiment, message, zone)
               VALUES (?, ?, ?, ?)""",
            (_utc_now_iso(),
             sentiment, message[:1000], zone),
        )
        self._conn.commit()
        return c.lastrowid or 0

    def list_community_feedback(self, limit: int = 100) -> list[dict]:
        c = self._conn.cursor()
        c.execute(
            """SELECT id, created_at, sentiment, message, zone
               FROM community_feedback ORDER BY id DESC LIMIT ?""",
            (limit,),
        )
        return [
            {"id": r[0], "created_at": r[1], "sentiment": r[2],
             "message": r[3], "zone": r[4]}
            for r in c.fetchall()
        ]

    # ---------- Visits ----------

    def add_visit(self, *, token_id: str, zone: str, rotating_id: str | None = None,
                  visited_at: str | None = None,
                  self_reported_crowded: bool = False,
                  self_reported_sick: bool = False) -> int:
        c = self._conn.cursor()
        c.execute(
            """INSERT INTO visits
               (token_id, rotating_id, zone, visited_at,
                self_reported_crowded, self_reported_sick)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                token_id, rotating_id, zone,
                visited_at or _utc_now_iso(),
                1 if self_reported_crowded else 0,
                1 if self_reported_sick else 0,
            ),
        )
        self._conn.commit()
        return c.lastrowid or 0

    def visits_for_token(self, token_id: str, limit: int = 200) -> list[dict]:
        c = self._conn.cursor()
        c.execute(
            """SELECT id, zone, visited_at, self_reported_crowded, self_reported_sick
               FROM visits WHERE token_id = ?
               ORDER BY id DESC LIMIT ?""",
            (token_id, limit),
        )
        return [
            {"id": r[0], "zone": r[1], "visited_at": r[2],
             "crowded": bool(r[3]), "sick": bool(r[4])}
            for r in c.fetchall()
        ]

    def visits_in_window(self, *, zone: str, time_from: str,
                         time_to: str) -> list[dict]:
        """Used by the business broadcast endpoint: who was at zone X between
        `time_from` and `time_to`?"""
        c = self._conn.cursor()
        c.execute(
            """SELECT DISTINCT token_id FROM visits
               WHERE zone = ? AND visited_at BETWEEN ? AND ?""",
            (zone, time_from, time_to),
        )
        return [{"token_id": r[0]} for r in c.fetchall()]

    def visit_stats(self, since: str | None = None) -> dict:
        """Aggregate counts for the business dashboard."""
        c = self._conn.cursor()
        if since:
            c.execute("SELECT COUNT(*), COUNT(DISTINCT token_id) FROM visits WHERE visited_at >= ?", (since,))
        else:
            c.execute("SELECT COUNT(*), COUNT(DISTINCT token_id) FROM visits")
        total, unique = c.fetchone()

        if since:
            c.execute("SELECT COUNT(*) FROM visits WHERE self_reported_crowded = 1 AND visited_at >= ?", (since,))
        else:
            c.execute("SELECT COUNT(*) FROM visits WHERE self_reported_crowded = 1")
        crowded = c.fetchone()[0]

        if since:
            c.execute("SELECT COUNT(*) FROM visits WHERE self_reported_sick = 1 AND visited_at >= ?", (since,))
        else:
            c.execute("SELECT COUNT(*) FROM visits WHERE self_reported_sick = 1")
        sick = c.fetchone()[0]

        return {
            "total_visits": total or 0,
            "unique_tokens": unique or 0,
            "self_reported_crowded": crowded or 0,
            "self_reported_sick": sick or 0,
        }

    # ---------- Notifications ----------

    def add_notification(self, *, token_id: str, notification_type: str,
                         title: str, body: str, zone: str | None = None,
                         exposure_date: str | None = None) -> int:
        if notification_type not in ("exposure", "crowding", "general"):
            notification_type = "general"
        c = self._conn.cursor()
        c.execute(
            """INSERT INTO notifications
               (token_id, created_at, notification_type, title, body, zone, exposure_date)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (token_id, _utc_now_iso(),
             notification_type, title, body, zone, exposure_date),
        )
        self._conn.commit()
        return c.lastrowid or 0

    def notifications_for_token(self, token_id: str, limit: int = 50,
                                unread_only: bool = False) -> list[dict]:
        c = self._conn.cursor()
        if unread_only:
            c.execute(
                """SELECT id, created_at, notification_type, title, body,
                          zone, exposure_date, read_at
                   FROM notifications
                   WHERE token_id = ? AND read_at IS NULL
                   ORDER BY id DESC LIMIT ?""",
                (token_id, limit),
            )
        else:
            c.execute(
                """SELECT id, created_at, notification_type, title, body,
                          zone, exposure_date, read_at
                   FROM notifications WHERE token_id = ?
                   ORDER BY id DESC LIMIT ?""",
                (token_id, limit),
            )
        return [
            {"id": r[0], "created_at": r[1], "type": r[2], "title": r[3],
             "body": r[4], "zone": r[5], "exposure_date": r[6],
             "read": r[7] is not None}
            for r in c.fetchall()
        ]

    def mark_notification_read(self, notification_id: int, token_id: str) -> bool:
        c = self._conn.cursor()
        c.execute(
            """UPDATE notifications SET read_at = ?
               WHERE id = ? AND token_id = ? AND read_at IS NULL""",
            (_utc_now_iso(), notification_id, token_id),
        )
        self._conn.commit()
        return c.rowcount > 0

    def occupancy_history(self, limit: int = 720) -> list[dict]:
        """Last `limit` occupancy entries. 720 ≈ 1 hour at 1Hz logging."""
        c = self._conn.cursor()
        c.execute(
            """SELECT timestamp, level, variance, variance_ratio, count_estimate
               FROM occupancy_log ORDER BY id DESC LIMIT ?""",
            (limit,),
        )
        rows = c.fetchall()
        return [
            {
                "timestamp": r[0], "level": r[1], "variance": r[2],
                "variance_ratio": r[3], "count_estimate": r[4],
            }
            for r in rows
        ][::-1]

    def close(self) -> None:
        self._conn.close()
