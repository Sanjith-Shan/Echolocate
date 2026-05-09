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

-- Privacy invariant in the schema itself: there's nowhere to put an image.
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
