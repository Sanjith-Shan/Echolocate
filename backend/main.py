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

from . import camera as camera_mod
from . import chat as chat_mod
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


async def _capture_and_analyze(occupancy: dict) -> None:
    """Capture single frame → Claude Vision → accumulate metadata → DELETE image."""
    image_b64 = camera.capture_and_encode()
    if not image_b64:
        # No camera; record a metadata-only observation so the dashboard still gets
        # something on threshold breach. Spatial fields are unknown.
        observation = {
            "timestamp": datetime.now().isoformat(),
            "csi_occupancy": occupancy,
            "_no_camera": True,
        }
        spatial_observations.append(observation)
        store.add_observation(observation)
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
    global serial_thread, monitor_task
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
    ok = tokens.checkin(body.token_id, body.zone)
    return {"status": "ok" if ok else "unknown_token"}


@app.post("/api/report-positive")
async def report_positive(body: ReportPositiveBody):
    overlapping = tokens.find_overlaps(body.token_id)
    notified = 0
    for other_id in overlapping:
        sub = tokens.registered_tokens[other_id].get("push_subscription")
        if sub and pusher.send(
            sub,
            "Echolocate: Exposure alert",
            "You were in proximity with someone who has reported a positive test. Consider monitoring for symptoms.",
        ):
            notified += 1
    return {"overlapping_tokens": len(overlapping), "notifications_sent": notified}


@app.post("/api/chat")
async def chat_endpoint(body: ChatBody):
    occupancy = csi_detector.get_occupancy()
    text = await chat_mod.chat(body.message, occupancy, spatial_observations)
    return {"response": text}


@app.post("/api/generate-report")
async def generate_report():
    if len(spatial_observations) < 1:
        return JSONResponse(
            {"error": "No spatial observations yet. Trigger at least one threshold event first."},
            status_code=400,
        )
    text = await report_generator.generate_space_report(spatial_observations)
    return {"report": text, "observation_count": len(spatial_observations)}


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
            "id": "anthropic",
            "label": "Anthropic API key",
            "ok": bool(os.getenv("ANTHROPIC_API_KEY")),
            "detail": (
                "Configured (Claude Vision + reports + chat enabled)"
                if os.getenv("ANTHROPIC_API_KEY")
                else "Not set — system runs in stub mode"
            ),
            "blocker": (
                None if os.getenv("ANTHROPIC_API_KEY") else
                "Drop ANTHROPIC_API_KEY=sk-ant-... into a .env file at the project root."
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
