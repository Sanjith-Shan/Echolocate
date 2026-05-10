"""
Echolocate FastAPI server.

Reads CSI from a configurable source (real serial, TCP simulator, or file
replay), classifies occupancy, optionally captures an ephemeral webcam frame
on threshold breach, sends it through Claude Vision, accumulates the
returned spatial metadata, and serves the rest of the app via REST + WebSocket.

Environment variables (see .env.example):
  SERIAL_PORT             /dev/cu.usbmodem* | tcp://host:port | file:///path
  SERIAL_BAUD             default 921600 (only used for real serial)
  ANTHROPIC_API_KEY       optional — pipeline runs without it (stub responses)
  VAPID_PRIVATE_KEY       optional — push notifications no-op without it
  VAPID_PUBLIC_KEY        optional — needed for web push subscription
  CAMERA_DEVICE_INDEX     default 1 (Logitech Brio is usually index 1 on Mac)
  CROWDING_VARIANCE_RATIO default 3.0 (threshold = `moderate` or higher)
  SNAPSHOT_COOLDOWN       default 30 (seconds between snapshots)
  ECHOLOCATE_DB           default echolocate.db
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

# Auto-load .env from the project root or the backend dir so users can drop
# their ANTHROPIC_API_KEY / VAPID keys in a single file and the whole pipeline
# wakes up without re-exporting anything.
try:
    from dotenv import load_dotenv
    _here = os.path.dirname(os.path.abspath(__file__))
    _root = os.path.abspath(os.path.join(_here, ".."))
    for _candidate in (
        os.path.join(_root, ".env"),
        os.path.join(_here, ".env"),
    ):
        if os.path.isfile(_candidate):
            load_dotenv(_candidate, override=False)
            print(f"[backend] loaded env from {_candidate}")
except ImportError:
    pass  # python-dotenv is optional; explicit env vars still work

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import ai_provider
from . import camera as camera_mod
from . import chat as chat_mod
from . import demo_seed
from . import push_notifier
from . import report_generator
from . import spatial_analyzer
from . import storage
from . import token_manager
from .csi_detector import CSIOccupancyDetector
from .source import open_source

# ---------- Config ----------

SERIAL_PORT = os.getenv("SERIAL_PORT", "tcp://127.0.0.1:3333")
SERIAL_BAUD = int(os.getenv("SERIAL_BAUD", "921600"))
CAMERA_DEVICE_INDEX = int(os.getenv("CAMERA_DEVICE_INDEX", "1"))
SNAPSHOT_COOLDOWN = int(os.getenv("SNAPSHOT_COOLDOWN", "30"))
DB_PATH = os.getenv("ECHOLOCATE_DB", "echolocate.db")

# Where to find the device's HTTP server (real ESP32 IP, or sim's HTTP port).
# Default points at the simulator on localhost. For real hardware, set to e.g.
# "http://172.20.10.5" or "http://echolocate.local".
FIRMWARE_HTTP_URL = os.getenv("FIRMWARE_HTTP_URL", "http://127.0.0.1:8088")

# ---------- Global state ----------

csi_detector = CSIOccupancyDetector()
camera = camera_mod.EphemeralCamera(device_index=CAMERA_DEVICE_INDEX)
tokens = token_manager.AnonymousTokenManager()
pusher = push_notifier.PushNotifier()
store = storage.Store(DB_PATH)

spatial_observations: list[dict] = []
connected_clients: set[WebSocket] = set()
last_snapshot_time = 0.0
serial_lines_seen = 0
serial_lines_parsed = 0

# Set when the auto-seeder runs on startup (or via /api/_demo/seed). Lets the
# PWA fetch the demo phone's token without manual entry.
demo_anchor_token: Optional[str] = None

# Override with ECHOLOCATE_AUTOSEED=0 if you specifically want a fresh empty
# system (e.g. for a manual hardware-only demo).
AUTOSEED = os.getenv("ECHOLOCATE_AUTOSEED", "1") == "1"


# ---------- Background workers ----------

def serial_reader_thread(stop_event: threading.Event) -> None:
    """Drains the configured CSI source, parses lines, updates the detector."""
    global serial_lines_seen, serial_lines_parsed

    print(f"[backend] CSI source: {SERIAL_PORT}")
    src = open_source(SERIAL_PORT, SERIAL_BAUD)
    for line in src:
        if stop_event.is_set():
            return
        serial_lines_seen += 1
        parsed = csi_detector.parse_csi_line(line)
        if parsed:
            csi_detector.update(parsed)
            serial_lines_parsed += 1


async def monitoring_loop(stop_event: threading.Event) -> None:
    global last_snapshot_time

    while not stop_event.is_set():
        await asyncio.sleep(2)

        occupancy = csi_detector.get_occupancy()
        store.add_occupancy(occupancy)

        # Threshold breach → ephemeral snapshot + Claude Vision
        if (
            occupancy.get("threshold_exceeded")
            and time.time() - last_snapshot_time > SNAPSHOT_COOLDOWN
        ):
            last_snapshot_time = time.time()
            await _capture_and_analyze(occupancy)

        # Broadcast realtime update to all WS clients
        broadcast = {
            "type": "occupancy_update",
            "occupancy": occupancy,
            "latest_spatial": spatial_observations[-1] if spatial_observations else None,
            "total_observations": len(spatial_observations),
            "timestamp": datetime.now().isoformat(),
            "stream_health": {
                "lines_seen": serial_lines_seen,
                "lines_parsed": serial_lines_parsed,
                "source": SERIAL_PORT,
            },
        }
        dead = []
        for ws in list(connected_clients):
            try:
                await ws.send_json(broadcast)
            except Exception:
                dead.append(ws)
        for ws in dead:
            connected_clients.discard(ws)


def _summarize_spatial(spatial: dict) -> str:
    """One-line plain-language summary of a spatial analysis result."""
    if spatial.get("_no_camera"):
        return "Threshold breach detected. Camera not available — metadata-only record."
    n = spatial.get("total_people_visible", "?")
    density = spatial.get("overall_density", "?")
    chokes = spatial.get("chokepoints") or []
    if chokes:
        return f"~{n} people, {density} density. Chokepoint near: {', '.join(chokes[:2])}"
    return f"~{n} people, {density} density. No chokepoint flagged."


async def _capture_and_analyze(occupancy: dict) -> None:
    """Capture single frame → Claude Vision → accumulate metadata → DELETE image."""
    image_b64 = camera.capture_and_encode()
    if not image_b64:
        # No camera; record a metadata-only observation so the dashboard still
        # gets something on threshold breach. Even with no AI involved, it's a
        # decision the SYSTEM made (it fired the camera trigger), so we still
        # log it in the AI decision table for transparency.
        observation = {
            "timestamp": datetime.now().isoformat(),
            "csi_occupancy": occupancy,
            "_no_camera": True,
        }
        spatial_observations.append(observation)
        store.add_observation(observation)
        store.add_ai_decision(
            decision_type="spatial_analysis",
            model="none",
            summary=_summarize_spatial(observation),
            raw_input={"csi_occupancy": occupancy, "trigger": "threshold_breach"},
            raw_output={"_no_camera": True},
        )
        return

    spatial = await spatial_analyzer.analyze_snapshot(image_b64)

    # PRIVACY INVARIANT: drop the base64 immediately after the network call returns.
    del image_b64

    if not spatial:
        return

    spatial["timestamp"] = datetime.now().isoformat()
    spatial["csi_occupancy"] = occupancy
    spatial_observations.append(spatial)
    store.add_observation(spatial)

    # Governance: log every AI judgment so the operator can review / override.
    # We never write the image — only the structured analysis.
    summary = _summarize_spatial(spatial)
    store.add_ai_decision(
        decision_type="spatial_analysis",
        model=ai_provider.active_model(),
        summary=summary,
        raw_input={"csi_occupancy": occupancy, "trigger": "threshold_breach"},
        raw_output=spatial,
    )

    # Notify opted-in users that the space is crowded
    if occupancy.get("level") in ("moderate", "high"):
        for token_data in list(tokens.registered_tokens.values()):
            sub = token_data.get("push_subscription")
            if sub:
                pusher.send(
                    sub,
                    "Echolocate: Space alert",
                    f"Current occupancy is {occupancy['level']}. Consider spacing out or visiting later.",
                )


# ---------- App lifespan ----------

stop_event = threading.Event()
serial_thread: Optional[threading.Thread] = None
monitor_task: Optional[asyncio.Task] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global serial_thread, monitor_task, demo_anchor_token
    # Warm the in-memory observation list from anything previously persisted
    # (esp. demo-seeded data). Order matches insertion (oldest first).
    try:
        prior = list(reversed(store.list_observations(limit=200)))
        spatial_observations.extend(prior)
        if prior:
            print(f"[backend] warmed {len(prior)} prior observations from DB")
    except Exception as e:
        print(f"[backend] couldn't warm observations: {e}")

    # Auto-seed the demo story so the PWA is pre-populated. We RESET +
    # RESEED on every startup so each demo session begins from the same
    # known-good state. Set ECHOLOCATE_AUTOSEED=0 to opt out (e.g. for
    # the actual hardware demo or for production use).
    if AUTOSEED:
        try:
            demo_seed.reset_all(store, tokens, spatial_observations)
            summary = demo_seed.seed(store, tokens, spatial_observations)
            demo_anchor_token = summary["demo_token_anchor_a"]
            print(f"[backend] auto-seeded demo data "
                  f"({summary['visits']} visits, {summary['ai_decisions']} decisions, "
                  f"anchor={demo_anchor_token[:8]}…)")
        except Exception as e:
            print(f"[backend] auto-seed skipped: {e}")

    serial_thread = threading.Thread(
        target=serial_reader_thread, args=(stop_event,), daemon=True
    )
    serial_thread.start()
    monitor_task = asyncio.create_task(monitoring_loop(stop_event))
    print("[backend] startup complete")
    try:
        yield
    finally:
        stop_event.set()
        if monitor_task:
            monitor_task.cancel()
            try:
                await monitor_task
            except (asyncio.CancelledError, Exception):
                pass
        store.close()
        print("[backend] shutdown")


app = FastAPI(title="Echolocate Backend", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- Frontend mount (only if built) ----------

_FRONTEND_DIST = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
if os.path.isdir(_FRONTEND_DIST):
    app.mount("/app", StaticFiles(directory=_FRONTEND_DIST, html=True), name="frontend")


# ---------- Pydantic request models ----------

class RegisterBody(BaseModel):
    push_subscription: Optional[dict] = None


class CheckinBody(BaseModel):
    token_id: str
    zone: str = "main"


class ReportPositiveBody(BaseModel):
    token_id: str


class ChatBody(BaseModel):
    message: str


# ---------- REST endpoints ----------

@app.get("/")
async def root():
    return {
        "service": "echolocate",
        "status": "ok",
        "source": SERIAL_PORT,
        "occupancy": csi_detector.get_occupancy(),
        "observations": len(spatial_observations),
    }


@app.get("/api/status")
async def get_status():
    return {
        "occupancy": csi_detector.get_occupancy(),
        "total_observations": len(spatial_observations),
        "stream_health": {
            "lines_seen": serial_lines_seen,
            "lines_parsed": serial_lines_parsed,
            "parse_rate": (serial_lines_parsed / serial_lines_seen) if serial_lines_seen else 0,
            "source": SERIAL_PORT,
        },
        "camera_available": camera.available(),
        "push_configured": pusher.configured(),
        "anthropic_configured": bool(os.getenv("ANTHROPIC_API_KEY")),
    }


@app.get("/api/observations")
async def get_observations(limit: int = 100):
    return {"observations": spatial_observations[-limit:]}


@app.get("/api/occupancy-history")
async def get_occupancy_history(limit: int = 720):
    return {"history": store.occupancy_history(limit=limit)}


@app.post("/api/register")
async def register_anonymous(body: RegisterBody):
    token_id = tokens.register(body.push_subscription)
    return {"token_id": token_id}


@app.post("/api/checkin")
async def zone_checkin(body: CheckinBody):
    """Legacy alias for /api/consumer/check-in (kept for the existing tests)."""
    return await consumer_checkin(ConsumerCheckin(
        token_id=body.token_id, zone=body.zone, crowded=False,
    ))


@app.post("/api/report-positive")
async def report_positive(body: ReportPositiveBody):
    """Legacy alias for /api/consumer/report-sick."""
    return await consumer_report_sick(ConsumerReportSick(token_id=body.token_id))


# ---------- Consumer surface ----------

class ConsumerCheckin(BaseModel):
    token_id: str
    zone: str = "main"
    crowded: bool = False    # consumer's own subjective "this felt crowded"


class ConsumerReportSick(BaseModel):
    token_id: str


@app.post("/api/consumer/check-in")
async def consumer_checkin(body: ConsumerCheckin):
    """Log a visit. Optionally flag it as 'felt crowded' — that's a consumer
    signal that complements the sensor's variance-based classification."""
    if body.token_id not in tokens.registered_tokens:
        # Auto-register so the consumer flow stays one-tap.
        token_id = tokens.register(None)
        registered = True
    else:
        token_id = body.token_id
        registered = False

    tokens.checkin(token_id, body.zone)
    visit_id = store.add_visit(
        token_id=token_id,
        zone=body.zone,
        rotating_id=tokens.registered_tokens[token_id]["current_rotating_id"],
        self_reported_crowded=body.crowded,
    )
    return {
        "status": "ok",
        "token_id": token_id,
        "auto_registered": registered,
        "visit_id": visit_id,
    }


@app.post("/api/consumer/report-sick")
async def consumer_report_sick(body: ConsumerReportSick):
    """Consumer reports a positive test. Find every token that overlapped
    their visits and create exposure notifications for each. Push fires too
    if VAPID is configured; if not, the notifications are still readable
    via /api/consumer/notifications."""
    if body.token_id not in tokens.registered_tokens:
        return JSONResponse({"error": "Unknown token"}, status_code=400)

    # Mark this token's most recent visits as sick
    visits = store.visits_for_token(body.token_id, limit=50)
    for v in visits:
        # We can't UPDATE in storage abstractly — re-add with sick flag would
        # duplicate. Skip the per-row update; the notification flow doesn't
        # rely on the sick flag, only on the time/zone overlap.
        pass

    overlapping_ids = tokens.find_overlaps(body.token_id)

    # Also scan the persistent visits table for overlaps (in case the
    # in-memory token_manager lost state across a restart)
    seen = set(overlapping_ids)
    for v in visits:
        for other in store.visits_in_window(
            zone=v["zone"],
            time_from=_iso_minus(v["visited_at"], minutes=30),
            time_to=_iso_plus(v["visited_at"], minutes=30),
        ):
            tid = other["token_id"]
            if tid != body.token_id and tid not in seen:
                seen.add(tid)
                overlapping_ids.append(tid)

    notified_inapp = 0
    notified_push = 0
    for other_id in overlapping_ids:
        store.add_notification(
            token_id=other_id,
            notification_type="exposure",
            title="Possible exposure",
            body=(
                "You were in a space at the same time as someone who has "
                "reported a positive test. The system does not know who "
                "they are. Consider monitoring for symptoms."
            ),
            zone=visits[0]["zone"] if visits else None,
            exposure_date=visits[0]["visited_at"] if visits else None,
        )
        notified_inapp += 1
        sub = tokens.registered_tokens.get(other_id, {}).get("push_subscription")
        if sub and pusher.send(
            sub, "Echolocate: Exposure alert",
            "You may have been near someone who reported a positive test.",
        ):
            notified_push += 1

    return {
        "overlapping_tokens": len(overlapping_ids),
        "notifications_inapp": notified_inapp,
        "notifications_push": notified_push,
    }


@app.get("/api/consumer/notifications")
async def consumer_notifications(token_id: str, unread_only: bool = False):
    """Pull-based inbox so consumers see alerts even when push is unreachable
    (browser closed, no HTTPS, no push permission, etc.)."""
    if token_id not in tokens.registered_tokens:
        # We still return [] rather than 404 so a stale localStorage token
        # doesn't break the consumer's whole UI.
        return {"notifications": [], "warning": "Unknown token — re-register"}
    return {
        "notifications": store.notifications_for_token(token_id, unread_only=unread_only),
    }


@app.post("/api/consumer/notifications/{notification_id}/read")
async def consumer_mark_read(notification_id: int, token_id: str):
    if token_id not in tokens.registered_tokens:
        return JSONResponse({"error": "Unknown token"}, status_code=400)
    ok = store.mark_notification_read(notification_id, token_id)
    return {"status": "ok" if ok else "not_found_or_already_read"}


@app.get("/api/consumer/my-visits")
async def consumer_my_visits(token_id: str, limit: int = 100):
    """Return the visit history for this token. The consumer is the only
    party who knows the token, so this is effectively self-only."""
    if token_id not in tokens.registered_tokens:
        return {"visits": [], "warning": "Unknown token — re-register"}
    return {"visits": store.visits_for_token(token_id, limit=limit)}


# ---------- Business surface ----------

class BusinessBroadcast(BaseModel):
    zone: str = "main"
    time_from: str           # ISO-8601, inclusive
    time_to: str             # ISO-8601, inclusive
    title: str = "Echolocate alert"
    body: str
    notification_type: str = "general"   # 'exposure' | 'crowding' | 'general'


@app.post("/api/business/notify-visitors")
async def business_notify(body: BusinessBroadcast):
    """Notify every token that visited `zone` between `time_from` and
    `time_to`. Use case: business detects an outbreak (or a leak, or a
    safety issue) and wants to alert everyone who was there during the
    window — without ever knowing who any of those people are."""
    matches = store.visits_in_window(
        zone=body.zone, time_from=body.time_from, time_to=body.time_to,
    )
    notified_inapp = 0
    notified_push = 0
    for m in matches:
        tid = m["token_id"]
        store.add_notification(
            token_id=tid,
            notification_type=body.notification_type,
            title=body.title,
            body=body.body,
            zone=body.zone,
            exposure_date=body.time_from,
        )
        notified_inapp += 1
        sub = tokens.registered_tokens.get(tid, {}).get("push_subscription")
        if sub and pusher.send(sub, body.title, body.body):
            notified_push += 1
    return {
        "matched_tokens": len(matches),
        "notifications_inapp": notified_inapp,
        "notifications_push": notified_push,
    }


@app.get("/api/business/visits")
async def business_visits(since: Optional[str] = None):
    """Aggregate visit stats for the business dashboard. No identifiers
    leave the building — only counts."""
    return store.visit_stats(since=since)


def _iso_minus(iso: str, minutes: int) -> str:
    from datetime import datetime, timedelta
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    return (dt - timedelta(minutes=minutes)).isoformat()


def _iso_plus(iso: str, minutes: int) -> str:
    from datetime import datetime, timedelta
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    return (dt + timedelta(minutes=minutes)).isoformat()


@app.post("/api/chat")
async def chat_endpoint(body: ChatBody):
    occupancy = csi_detector.get_occupancy()
    text = await chat_mod.chat(body.message, occupancy, spatial_observations)
    store.add_ai_decision(
        decision_type="chat",
        model=ai_provider.active_model(),
        summary=f"Q: {body.message[:80]}",
        raw_input={"message": body.message, "occupancy": occupancy},
        raw_output={"response": text},
    )
    return {"response": text}


@app.post("/api/generate-report")
async def generate_report():
    """Substantive Space Design Report. Feeds the AI every signal we have
    (live occupancy, visits, feedback, observations, prior decisions,
    occupancy history) and returns BOTH a structured JSON report and a
    rendered Markdown version. The structured form is what the UI uses
    to render proper sections + colour-coded severities."""
    occupancy_now = csi_detector.get_occupancy()
    visit_stats = store.visit_stats()
    feedback = store.list_community_feedback(limit=50)
    decisions = store.list_ai_decisions(limit=20, public=True)
    occ_history = store.occupancy_history(limit=300)

    # Need *something* to reason about. We allow generation when there's
    # any data — observations, visits, or feedback — not just observations.
    if (len(spatial_observations) == 0 and visit_stats["total_visits"] == 0
            and len(feedback) == 0):
        return JSONResponse(
            {"error": "No data yet. Have someone check in, submit feedback, "
             "or trigger a threshold event first."},
            status_code=400,
        )

    result = await report_generator.generate_space_report(
        spatial_observations,
        occupancy_now=occupancy_now,
        visit_stats=visit_stats,
        community_feedback=feedback,
        recent_decisions=decisions,
        occupancy_history=occ_history,
    )

    structured = result["structured"]
    summary_for_log = (
        structured.get("executive_summary")
        or f"Space Design Report from {len(spatial_observations)} observations"
    )[:200]

    store.add_ai_decision(
        decision_type="space_report",
        model=result["model"],
        summary=summary_for_log,
        raw_input={
            "observation_count": len(spatial_observations),
            "visit_stats": visit_stats,
            "feedback_count": len(feedback),
        },
        raw_output={"structured": structured},
    )

    return {
        "report": result["markdown"],     # legacy field — keeps existing tests/clients working
        "report_markdown": result["markdown"],
        "report_structured": structured,
        "model": result["model"],
        "context": {
            "observation_count": len(spatial_observations),
            "visit_count": visit_stats["total_visits"],
            "feedback_count": len(feedback),
        },
        # Old field name kept for backwards compatibility with existing tests
        "observation_count": len(spatial_observations),
    }


# ---------- Governance endpoints: AI decision log + community feedback + transparency ----------

class DecisionUpdate(BaseModel):
    status: str   # 'pending' | 'considered' | 'accepted' | 'rejected'
    notes: Optional[str] = None


class FeedbackBody(BaseModel):
    sentiment: str = "concern"   # 'concern' | 'praise' | 'suggestion'
    message: str
    zone: Optional[str] = "main"


@app.get("/api/decisions")
async def list_decisions(limit: int = 100):
    """Operator view of every AI judgment + their decisions on each."""
    return {
        "decisions": store.list_ai_decisions(limit=limit, public=False),
        "stats": store.ai_decision_stats(),
    }


@app.post("/api/decisions/{decision_id}")
async def update_decision(decision_id: int, body: DecisionUpdate):
    ok = store.update_ai_decision(decision_id, status=body.status, notes=body.notes)
    if not ok:
        return JSONResponse({"error": "Unknown decision_id or invalid status"}, status_code=400)
    return {"status": "ok"}


@app.post("/api/community-feedback")
async def submit_feedback(body: FeedbackBody):
    """Anonymous community feedback — no identifiers, ever."""
    msg = (body.message or "").strip()
    if not msg:
        return JSONResponse({"error": "Empty message"}, status_code=400)
    fid = store.add_community_feedback(
        sentiment=body.sentiment, message=msg, zone=body.zone
    )
    return {"id": fid, "status": "ok"}


@app.get("/api/community-feedback")
async def list_feedback(limit: int = 50):
    return {"feedback": store.list_community_feedback(limit=limit)}


@app.get("/api/transparency")
async def transparency():
    """Public, no-auth endpoint. What the watched can see about the watcher.

    The shape of THIS endpoint is itself a privacy claim — it's the full set
    of facts the system is willing to expose. Anything not here, the system
    doesn't have. Use it as the contract surface."""
    occupancy = csi_detector.get_occupancy()
    plain_level = {
        "calibrating": "warming up", "empty": "calm", "low": "calm",
        "moderate": "busy", "high": "crowded",
    }.get(occupancy.get("level"), "unknown")

    decisions_today = store.list_ai_decisions(limit=20, public=True)
    feedback = store.list_community_feedback(limit=10)

    return {
        "right_now": {
            "plain_status": plain_level,
            "raw_level": occupancy.get("level"),
            "estimate": occupancy.get("count_estimate"),
        },
        "ai_activity": {
            "total_decisions": store.ai_decision_stats()["total"],
            "recent": decisions_today,
        },
        "community_feedback_recent": feedback,
        "privacy_invariants": {
            "what_is_collected": [
                "Aggregate occupancy levels (calm / busy / crowded)",
                "Anonymous rotating tokens (15-min rotation, opt-in only)",
                "Zone+time pairs you visited (only if you opted in)",
                "Spatial metadata from snapshot-triggered AI analysis",
                "Anonymous community feedback (no identifiers)",
            ],
            "what_is_NEVER_collected": [
                "Photos, video, or audio",
                "Faces or biometric features",
                "Names, email addresses, phone numbers",
                "Device fingerprints, IP addresses, MAC addresses",
                "Browsing or location history outside this space",
            ],
            "schema_proof": (
                "Proof: the SQLite schema literally has no column for any of "
                "the above. See backend/storage.py — verifiable by code review."
            ),
        },
        "device": {
            "url": FIRMWARE_HTTP_URL,
            "verify_yourself": f"{FIRMWARE_HTTP_URL.rstrip('/')}/health",
        },
    }


@app.post("/api/recalibrate")
async def recalibrate():
    """Reset the baseline. Call after verifying the room is empty."""
    csi_detector.recalibrate()
    return {"status": "ok", "message": "Calibration reset. Keep the space empty for ~10 seconds."}


@app.get("/api/firmware-status")
async def firmware_status():
    """Proxy the device's /health endpoint so the PWA can confirm it's
    actually reaching the ESP32 (or simulator) over the network."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(f"{FIRMWARE_HTTP_URL.rstrip('/')}/health")
            r.raise_for_status()
            return {
                "reachable": True,
                "url": FIRMWARE_HTTP_URL,
                "device": r.json(),
            }
    except Exception as e:
        return {
            "reachable": False,
            "url": FIRMWARE_HTTP_URL,
            "error": str(e),
            "hint": (
                "Set FIRMWARE_HTTP_URL=http://<esp32-ip> in your .env "
                "(or http://echolocate.local) once the firmware is flashed."
            ),
        }


@app.get("/api/diagnostics")
async def diagnostics():
    """One-stop system health check. Returns red/green status for every
    subsystem so the operator dashboard can render a clear "is everything
    actually working?" view."""
    occupancy = csi_detector.get_occupancy()
    parse_rate = (serial_lines_parsed / serial_lines_seen) if serial_lines_seen else 0.0
    csi_ok = serial_lines_parsed > 0 and parse_rate > 0.5

    fw = await firmware_status()

    checks = [
        {
            "id": "csi_stream",
            "label": "CSI stream",
            "ok": csi_ok,
            "detail": (
                f"{serial_lines_parsed}/{serial_lines_seen} lines parsed "
                f"({parse_rate:.0%})"
            ),
            "blocker": (
                None if csi_ok else
                "No CSI lines parsed yet. Is the simulator or ESP32 running?"
            ),
        },
        {
            "id": "calibration",
            "label": "Baseline calibration",
            "ok": not occupancy.get("calibration_phase", True),
            "detail": (
                "Calibrated"
                if not occupancy.get("calibration_phase", True)
                else f"In progress ({int((occupancy.get('calibration_progress') or 0) * 100)}%)"
            ),
            "blocker": (
                None if not occupancy.get("calibration_phase", True)
                else "Keep the space empty for ~10 seconds."
            ),
        },
        {
            "id": "firmware_reachable",
            "label": "Device reachable over network",
            "ok": bool(fw.get("reachable")),
            "detail": (
                f"{fw.get('url')} — {fw.get('device', {}).get('firmware', '?')}"
                if fw.get("reachable") else
                f"Unreachable at {fw.get('url')}"
            ),
            "blocker": fw.get("hint") if not fw.get("reachable") else None,
        },
        {
            "id": "ai_provider",
            "label": "AI provider",
            "ok": ai_provider.active_provider() != "stub",
            "detail": (
                f"{ai_provider.active_provider()} ({ai_provider.active_model()})"
                if ai_provider.active_provider() != "stub"
                else "No key set — system runs in stub mode"
            ),
            "blocker": (
                None if ai_provider.active_provider() != "stub" else
                "Drop ANTHROPIC_API_KEY=sk-ant-... or OPENAI_API_KEY=sk-... "
                "into a .env file at the project root."
            ),
        },
        {
            "id": "vapid",
            "label": "Web Push (VAPID)",
            "ok": pusher.configured(),
            "detail": (
                "Configured" if pusher.configured() else
                "Not set — push notifications disabled"
            ),
            "blocker": (
                None if pusher.configured() else
                "Generate VAPID keys and add VAPID_PRIVATE_KEY/VAPID_PUBLIC_KEY to .env."
            ),
        },
        {
            "id": "camera",
            "label": "Camera",
            "ok": camera.available(),
            "detail": (
                "OpenCV available — capture path active"
                if camera.available() else
                "OpenCV not installed — observations are metadata-only"
            ),
            "blocker": None,  # camera is optional; metadata-only path still works
        },
    ]

    return {
        "ok": all(c["ok"] for c in checks if c["id"] in ("csi_stream", "calibration")),
        "checks": checks,
        "occupancy": occupancy,
        "config": {
            "serial_port": SERIAL_PORT,
            "firmware_http_url": FIRMWARE_HTTP_URL,
            "snapshot_cooldown_s": SNAPSHOT_COOLDOWN,
            "db_path": DB_PATH,
        },
    }


@app.post("/api/_demo/seed")
async def demo_seed_endpoint(reset: bool = True):
    """Populate a coherent bakery-at-lunch story for demos. If reset=true
    (default), wipes existing data first so the result is reproducible."""
    global demo_anchor_token
    if reset:
        demo_seed.reset_all(store, tokens, spatial_observations)
    summary = demo_seed.seed(store, tokens, spatial_observations)
    demo_anchor_token = summary["demo_token_anchor_a"]
    return {"status": "ok", "summary": {
        k: v for k, v in summary.items() if k != "personas"
    }, "demo_token_anchor_a": demo_anchor_token}


@app.post("/api/_demo/reset")
async def demo_reset_endpoint():
    global demo_anchor_token
    demo_seed.reset_all(store, tokens, spatial_observations)
    demo_anchor_token = None
    return {"status": "ok"}


@app.get("/api/_demo/anchor-token")
async def demo_anchor_token_endpoint():
    """Returns the seeded demo phone's token, so the PWA can adopt it on
    first load. Returns null when AUTOSEED is off or no seed has run."""
    return {"token_id": demo_anchor_token}


@app.get("/api/vapid-public-key")
async def get_vapid_key():
    return {"publicKey": os.getenv("VAPID_PUBLIC_KEY", "")}


# ---------- WebSocket ----------

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected_clients.add(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        connected_clients.discard(websocket)
    except Exception:
        connected_clients.discard(websocket)
