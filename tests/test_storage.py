"""SQLite storage — schema invariants and basic round-trips."""

import os
import tempfile

from backend.storage import Store


def test_observation_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "test.db")
        s = Store(path)
        try:
            obs = {
                "timestamp": "2026-05-09T12:15:00",
                "csi_occupancy": {"level": "moderate", "variance": 42.0},
                "total_people_visible": 4,
                "overall_density": "moderate",
                "spatial_issue": "Cluster near doorway",
                "chokepoints": ["doorway"],
                "clusters": [{"region": "left front", "count": 4}],
            }
            rid = s.add_observation(obs)
            assert rid > 0
            saved = s.list_observations()
            assert len(saved) == 1
            assert saved[0]["spatial_issue"] == "Cluster near doorway"
        finally:
            s.close()


def test_schema_has_no_image_column():
    """Privacy invariant: the schema must not have any column that could store
    an image — base64 or otherwise."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "test.db")
        s = Store(path)
        try:
            cur = s._conn.cursor()
            for table in ("observations", "occupancy_log"):
                cur.execute(f"PRAGMA table_info({table})")
                cols = [row[1].lower() for row in cur.fetchall()]
                for forbidden in ("image", "photo", "frame", "jpeg", "snapshot_data"):
                    assert forbidden not in cols, f"Forbidden column {forbidden!r} in {table}"
        finally:
            s.close()


def test_occupancy_history_orders_oldest_first():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "test.db")
        s = Store(path)
        try:
            for level in ("empty", "low", "moderate", "high"):
                s.add_occupancy({
                    "level": level, "variance": 1.0, "variance_ratio": 1.0, "count_estimate": 0
                })
            history = s.occupancy_history(limit=10)
            assert [h["level"] for h in history] == ["empty", "low", "moderate", "high"]
        finally:
            s.close()
