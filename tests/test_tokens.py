"""Anonymous token manager — registration, rotation, overlap detection."""

import time
from datetime import datetime, timedelta
from unittest.mock import patch

from backend.token_manager import AnonymousTokenManager


def test_register_returns_distinct_uuids():
    m = AnonymousTokenManager()
    a = m.register({"endpoint": "https://x"})
    b = m.register({"endpoint": "https://y"})
    assert a != b
    assert a in m.registered_tokens
    assert b in m.registered_tokens


def test_rotating_id_changes_with_time_window():
    m = AnonymousTokenManager()
    tok = m.register({})
    initial = m.registered_tokens[tok]["current_rotating_id"]

    # Force last_rotation into the past so _maybe_rotate triggers
    m.registered_tokens[tok]["last_rotation"] = time.time() - 2 * m.ROTATION_SECONDS
    with patch("backend.token_manager.time.time",
               return_value=time.time() + 2 * m.ROTATION_SECONDS):
        m.checkin(tok, "zone-A")

    rotated = m.registered_tokens[tok]["current_rotating_id"]
    assert rotated != initial


def test_overlap_detected_in_same_zone_within_window():
    m = AnonymousTokenManager()
    a = m.register({})
    b = m.register({})

    now = datetime.now()
    m.registered_tokens[a]["zone_history"] = [
        {"zone": "lobby", "time": now.isoformat(), "rotating_id": "A1"}
    ]
    m.registered_tokens[b]["zone_history"] = [
        {"zone": "lobby", "time": (now + timedelta(minutes=5)).isoformat(), "rotating_id": "B1"}
    ]
    overlaps = m.find_overlaps(a)
    assert b in overlaps


def test_no_overlap_in_different_zones():
    m = AnonymousTokenManager()
    a = m.register({})
    b = m.register({})

    now = datetime.now()
    m.registered_tokens[a]["zone_history"] = [
        {"zone": "lobby", "time": now.isoformat(), "rotating_id": "A1"}
    ]
    m.registered_tokens[b]["zone_history"] = [
        {"zone": "kitchen", "time": now.isoformat(), "rotating_id": "B1"}
    ]
    assert m.find_overlaps(a) == []


def test_no_overlap_outside_time_window():
    m = AnonymousTokenManager()
    a = m.register({})
    b = m.register({})

    now = datetime.now()
    m.registered_tokens[a]["zone_history"] = [
        {"zone": "lobby", "time": now.isoformat(), "rotating_id": "A1"}
    ]
    # 90 min later — outside the 30 min window
    m.registered_tokens[b]["zone_history"] = [
        {"zone": "lobby", "time": (now + timedelta(minutes=90)).isoformat(), "rotating_id": "B1"}
    ]
    assert m.find_overlaps(a) == []


def test_unknown_token_overlap_returns_empty():
    m = AnonymousTokenManager()
    assert m.find_overlaps("not-a-real-token") == []
