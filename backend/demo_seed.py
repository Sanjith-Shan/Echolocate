"""
Demo-day mock data.

This is one coherent story — a small neighbourhood bakery at lunch peak —
not random sample data. Every record reinforces the others so the whole UI
lights up with consistent, believable signals:

  • 11:50 — store opens for lunch, calm
  • 12:15 — line forms; cream/sugar station blocks the doorway
  • 12:32 — counter cluster + queue spillover; threshold breach
  • 12:45 — peak crowd, customer reports feeling cramped
  • 13:00 — lunch winds down
  • 13:30 — one customer reports a positive test → exposure alerts fire

Eight anonymous tokens, ~18 visits, 4 community feedback entries (concern,
suggestion, praise), 4 spatial observations, 6 AI decisions (a mix of
pending / accepted / considered / rejected with operator notes), and the
exposure-notification flow run through the real overlap logic.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoid circular import at module load
    from .storage import Store
    from .token_manager import AnonymousTokenManager


# ---------- The story ----------

# Anchor everything to "today's lunch peak" so timestamps look fresh
def _today_at(hour: int, minute: int) -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _utc(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# Eight personas, all anonymous, only used to drive overlap math
PERSONAS = [
    "anchor-A", "anchor-B", "lunch-rush-1", "lunch-rush-2",
    "lunch-rush-3", "lunch-rush-4", "afternoon-1", "afternoon-2",
]


def _visits_script(persona_to_token: dict[str, str]):
    """Returns a list of (token_id, zone, visited_at, crowded) tuples."""
    out = []
    add = lambda p, z, t, c=False: out.append(
        (persona_to_token[p], z, _utc(t), c))

    # 11:50 — calm pre-rush
    add("anchor-A",     "counter",  _today_at(11, 50))
    add("anchor-B",     "seating",  _today_at(11, 55))

    # 12:15 — the line begins; first "felt crowded" reports
    add("lunch-rush-1", "entrance", _today_at(12, 15), True)
    add("lunch-rush-2", "entrance", _today_at(12, 18), True)

    # 12:30-12:45 — peak crowd
    add("lunch-rush-3", "counter",  _today_at(12, 32), True)
    add("lunch-rush-4", "counter",  _today_at(12, 38))
    add("lunch-rush-1", "counter",  _today_at(12, 42))    # same person in another zone
    add("lunch-rush-2", "seating",  _today_at(12, 45), True)
    add("lunch-rush-3", "seating",  _today_at(12, 48))

    # 13:00 — winding down
    add("anchor-A",     "seating",  _today_at(13,  2))
    add("lunch-rush-4", "entrance", _today_at(13,  5))

    # 13:30 — afternoon trickle
    add("afternoon-1",  "seating",  _today_at(13, 30))
    add("afternoon-2",  "counter",  _today_at(13, 45))

    return out


def _feedback_script():
    return [
        (
            "concern",
            "Doorway feels cramped at lunch — line wraps near the cream/sugar station.",
            "entrance",
            _today_at(12, 22),
        ),
        (
            "suggestion",
            "Could you space out the seating tables a bit more during peak hours?",
            "seating",
            _today_at(12, 50),
        ),
        (
            "concern",
            "Air feels stuffy in the back-left corner — is the AC on that side?",
            "seating",
            _today_at(13, 10),
        ),
        (
            "praise",
            "Loved the new bread display — much easier to grab without bumping into people.",
            "counter",
            _today_at(13, 35),
        ),
    ]


def _observation_script():
    """Spatial-analysis records (as if Claude Vision had analysed snapshots)."""
    return [
        {
            "timestamp": _utc(_today_at(12, 18)),
            "csi_occupancy": {"level": "moderate", "variance": 14.2,
                               "variance_ratio": 3.7, "count_estimate": 4,
                               "threshold_exceeded": True,
                               "calibration_phase": False},
            "total_people_visible": 4,
            "overall_density": "tight",
            "spatial_issue": "Queue forming from counter to entrance, "
                              "cream/sugar station bisecting flow.",
            "chokepoints": ["entrance doorway", "cream/sugar station"],
            "clusters": [
                {"region": "front-left",  "near_feature": "doorway",
                 "count": 3, "density": "tight",    "pattern": "queue"},
                {"region": "center",      "near_feature": "cream_station",
                 "count": 1, "density": "spread",   "pattern": "passing_through"},
            ],
            "_seeded": True,
        },
        {
            "timestamp": _utc(_today_at(12, 32)),
            "csi_occupancy": {"level": "high", "variance": 41.0,
                               "variance_ratio": 8.4, "count_estimate": 7,
                               "threshold_exceeded": True,
                               "calibration_phase": False},
            "total_people_visible": 7,
            "overall_density": "tight",
            "spatial_issue": "Counter cluster + queue spillover into entrance.",
            "chokepoints": ["counter", "entrance doorway"],
            "clusters": [
                {"region": "center",      "near_feature": "counter",
                 "count": 4, "density": "tight", "pattern": "stationary_cluster"},
                {"region": "front-left",  "near_feature": "doorway",
                 "count": 3, "density": "tight", "pattern": "queue"},
            ],
            "_seeded": True,
        },
        {
            "timestamp": _utc(_today_at(12, 45)),
            "csi_occupancy": {"level": "high", "variance": 52.1,
                               "variance_ratio": 10.6, "count_estimate": 8,
                               "threshold_exceeded": True,
                               "calibration_phase": False},
            "total_people_visible": 8,
            "overall_density": "tight",
            "spatial_issue": "Peak crowd; seating area saturated, no flow path "
                              "to the back tables.",
            "chokepoints": ["entrance doorway", "narrow path to back seating"],
            "clusters": [
                {"region": "front",       "near_feature": "doorway",
                 "count": 4, "density": "tight", "pattern": "queue"},
                {"region": "center-right", "near_feature": "tables",
                 "count": 4, "density": "tight", "pattern": "seated"},
            ],
            "_seeded": True,
        },
        {
            "timestamp": _utc(_today_at(13, 12)),
            "csi_occupancy": {"level": "moderate", "variance": 16.8,
                               "variance_ratio": 4.2, "count_estimate": 4,
                               "threshold_exceeded": True,
                               "calibration_phase": False},
            "total_people_visible": 4,
            "overall_density": "moderate",
            "spatial_issue": "Lingering cluster near windows after lunch wind-down.",
            "chokepoints": ["window seating"],
            "clusters": [
                {"region": "right-back", "near_feature": "windows",
                 "count": 4, "density": "moderate", "pattern": "seated"},
            ],
            "_seeded": True,
        },
    ]


def _ai_decisions_script(observations: list[dict]):
    """A curated set of operator-facing AI judgments. Mix of statuses with
    realistic operator notes — so the demo card shows a believable workflow,
    not 20 identical entries."""
    # Pair the first three observations with already-acted-on suggestions
    return [
        {
            "decision_type": "spatial_analysis",
            "model": "gpt-4o-mini",
            "summary": "Cream/sugar station bisects the entry queue. "
                       "Move it ~1m east to clear the doorway.",
            "raw_input": {"observation": observations[0]},
            "raw_output": {"recommendation": "Move cream station 1m east"},
            "operator_status": "accepted",
            "operator_notes": "Scheduled with landlord for next week. "
                              "Will measure spillover before/after.",
        },
        {
            "decision_type": "spatial_analysis",
            "model": "gpt-4o-mini",
            "summary": "Counter cluster of 4 + 3-deep queue at peak. "
                       "Add a second pickup point to split flowing/stationary traffic.",
            "raw_input": {"observation": observations[1]},
            "raw_output": {"recommendation": "Add a second pickup point"},
            "operator_status": "considered",
            "operator_notes": "Talking to staff Wednesday — may need extra hire.",
        },
        {
            "decision_type": "spatial_analysis",
            "model": "gpt-4o-mini",
            "summary": "Peak occupancy 8 in a space that maintains "
                       "1.5m spacing for ~6. Consider a soft cap during 12:15-12:45.",
            "raw_input": {"observation": observations[2]},
            "raw_output": {"recommendation": "Soft capacity cap at lunch peak"},
            "operator_status": "pending",
            "operator_notes": None,
        },
        {
            "decision_type": "space_report",
            "model": "gpt-4o-mini",
            "summary": "Generated weekly Space Design Report from 4 "
                       "observations + 4 community feedback items.",
            "raw_input": {"observation_count": 4, "feedback_count": 4},
            "raw_output": {"report_section_count": 9},
            "operator_status": "accepted",
            "operator_notes": "Sharing with landlord at next meeting.",
        },
        {
            "decision_type": "chat",
            "model": "gpt-4o-mini",
            "summary": "Q: Is the back-left air conditioning a real issue?",
            "raw_input": {"message": "Is the back-left AC a real problem?"},
            "raw_output": {"response": "Two community concerns mention "
                            "stuffiness near the back-left corner; the spatial "
                            "data also shows lingering clusters there post-lunch."},
            "operator_status": "considered",
            "operator_notes": "Will check vent dampers Thursday.",
        },
        {
            "decision_type": "spatial_analysis",
            "model": "gpt-4o-mini",
            "summary": "Window-side cluster lingers after lunch — possible "
                       "draft or social hub. Investigate seating alignment.",
            "raw_input": {"observation": observations[3]},
            "raw_output": {"recommendation": "Investigate seating alignment"},
            "operator_status": "rejected",
            "operator_notes": "Customers like the window — keeping as-is.",
        },
    ]


# ---------- Public API ----------

def reset_all(store: "Store", token_manager: "AnonymousTokenManager",
              spatial_observations: list[dict]) -> None:
    """Wipe every demo-relevant table and in-memory store."""
    c = store._conn.cursor()
    for table in ("observations", "occupancy_log", "ai_decisions",
                  "community_feedback", "visits", "notifications"):
        c.execute(f"DELETE FROM {table}")
        c.execute(f"DELETE FROM sqlite_sequence WHERE name=?", (table,))
    store._conn.commit()
    token_manager.registered_tokens.clear()
    spatial_observations.clear()


def seed(store: "Store", token_manager: "AnonymousTokenManager",
         spatial_observations: list[dict]) -> dict:
    """Populate one coherent bakery-at-lunch story.

    Returns a summary dict with the counts created. Idempotent in spirit —
    callers should usually `reset_all()` first if re-seeding."""

    # 1. Anonymous tokens (one per persona)
    persona_to_token = {p: token_manager.register({}) for p in PERSONAS}

    # 2. Visits
    visits = _visits_script(persona_to_token)
    for token, zone, visited_at, crowded in visits:
        store.add_visit(
            token_id=token, zone=zone, visited_at=visited_at,
            self_reported_crowded=crowded,
            rotating_id=token_manager.registered_tokens[token]["current_rotating_id"],
        )
        # Also reflect in the in-memory zone_history so /api/consumer/my-visits
        # works for a demo phone holding that persona's token.
        token_manager.registered_tokens[token]["zone_history"].append({
            "zone": zone, "time": visited_at,
            "rotating_id": token_manager.registered_tokens[token]["current_rotating_id"],
        })

    # 3. Community feedback
    fb_count = 0
    for sentiment, msg, zone, when in _feedback_script():
        # Backdate by passing the timestamp through a direct INSERT
        c = store._conn.cursor()
        c.execute(
            """INSERT INTO community_feedback (created_at, sentiment, message, zone)
               VALUES (?, ?, ?, ?)""",
            (_utc(when), sentiment, msg, zone),
        )
        fb_count += 1
    store._conn.commit()

    # 4. Spatial observations
    obs_records = _observation_script()
    for o in obs_records:
        spatial_observations.append(o)
        store.add_observation(o)

    # 5. AI decisions — backdate so the timestamps look natural
    decision_count = 0
    decisions = _ai_decisions_script(obs_records)
    c = store._conn.cursor()
    for i, d in enumerate(decisions):
        # Spread decision timestamps over the same ~2-hour window
        when = _today_at(12, 20) + timedelta(minutes=8 * i)
        operator_at = (when + timedelta(minutes=15)) if d["operator_status"] != "pending" else None
        c.execute(
            """INSERT INTO ai_decisions
               (created_at, decision_type, model, summary, raw_input, raw_output,
                operator_status, operator_notes, operator_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (_utc(when), d["decision_type"], d["model"], d["summary"],
             json.dumps(d["raw_input"]), json.dumps(d["raw_output"]),
             d["operator_status"], d["operator_notes"],
             _utc(operator_at) if operator_at else None),
        )
        decision_count += 1
    store._conn.commit()

    # 6. Notification inbox: a few exposures + one operator broadcast
    # The "anchor-A" persona is our demo phone — make sure their inbox is rich.
    anchor_a = persona_to_token["anchor-A"]
    notif_count = 0

    # Operator broadcast (general) — looks like a recent ventilation notice
    store.add_notification(
        token_id=anchor_a,
        notification_type="general",
        title="Ventilation maintenance",
        body=(
            "Our HVAC was inspected during your visit window and one vent "
            "was running at reduced flow. We've fixed it. No action needed."
        ),
        zone="seating",
        exposure_date=_utc(_today_at(13, 0)),
    )
    notif_count += 1

    # Crowding alert (for the lunch peak)
    store.add_notification(
        token_id=anchor_a,
        notification_type="crowding",
        title="Space alert: crowded",
        body="The bakery is currently busy. Consider visiting later in the afternoon for shorter waits.",
        zone="counter",
        exposure_date=None,
    )
    notif_count += 1

    # 7. Sick exposure flow — lunch-rush-1 reports positive; everyone who
    # overlapped them in counter/entrance gets an exposure notification.
    sick_token = persona_to_token["lunch-rush-1"]
    overlapping = token_manager.find_overlaps(sick_token)
    # ALSO seed an exposure for anchor-A even if find_overlaps misses them in
    # this fixture window — anchor-A overlaps the busy zones in the visits script
    seen = set(overlapping)
    if anchor_a not in seen:
        overlapping = list(overlapping) + [anchor_a]
    for tid in overlapping:
        if tid == sick_token: continue
        store.add_notification(
            token_id=tid,
            notification_type="exposure",
            title="Possible exposure",
            body=(
                "You shared a space with someone who has reported a positive "
                "test. The system does not know who they are. Consider "
                "monitoring for symptoms."
            ),
            zone="counter",
            exposure_date=_utc(_today_at(12, 38)),
        )
        notif_count += 1

    return {
        "tokens_registered": len(PERSONAS),
        "visits": len(visits),
        "community_feedback": fb_count,
        "spatial_observations": len(obs_records),
        "ai_decisions": decision_count,
        "notifications": notif_count,
        "demo_token_anchor_a": anchor_a,
        "personas": persona_to_token,
    }
