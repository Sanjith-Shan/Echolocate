"""SQLite metadata store. NEVER stores images. Only spatial metadata."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Optional


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
                observation.get("timestamp", time.strftime("%Y-%m-%dT%H:%M:%S")),
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
                time.strftime("%Y-%m-%dT%H:%M:%S"),
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
                time.strftime("%Y-%m-%dT%H:%M:%S"),
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
             time.strftime("%Y-%m-%dT%H:%M:%S"),
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
            (time.strftime("%Y-%m-%dT%H:%M:%S"),
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
