# Echolocate

Privacy-first pandemic space intelligence using WiFi Channel State Information.

A bare ESP32-S3 acts as a passive radio sensor, detecting occupancy and crowd
density from the way bodies disturb WiFi signal patterns. When crowding
crosses a threshold, a webcam takes a single ephemeral snapshot, sends it to
Claude Vision for *spatial-only* analysis, then deletes the image. What
survives is metadata: "4 people clustered near entrance at 12:15 PM."

> No persistent visual data. No identity tracking. No images stored.
> The schema literally has nowhere to put one.

For the full project context — system architecture, hardware list,
ethics framing — see [`ECHOLOCATE_V2_CLAUDE.md`](ECHOLOCATE_V2_CLAUDE.md).
For the running build log + design decisions, see
[`PROGRESS_LOG.md`](PROGRESS_LOG.md).

## Repository layout

```
echolocate/
├── firmware/        ESP-IDF C source for the ESP32-S3 sensor
├── sim/             Python ESP32 simulator (TCP + HTTP, no hardware needed)
├── backend/         FastAPI server (CSI parsing, Claude integration, REST + WS)
├── tests/           pytest suite (27 tests, includes e2e fixtures)
├── frontend/dist/   Vanilla HTML/JS PWA (served by FastAPI at /app/)
├── docs/            Ethics writeup + video notes
├── ECHOLOCATE_V2_CLAUDE.md   Original spec (Claude Code project context)
└── PROGRESS_LOG.md           Build log + decisions
```

## How to run *right now* (no ESP32 needed)

```bash
# 1. Install deps
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
pip install pytest pytest-timeout httpx websockets

# 2. Start the simulator (publishes synthetic CSI on tcp://127.0.0.1:3333)
python3 -m sim.esp32_sim --no-stdin --tcp-port 3333 --http-port 8088 &

# 3. Start the backend pointed at the sim
SERIAL_PORT=tcp://127.0.0.1:3333 \
  python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 8000

# 4. Open the PWA at http://localhost:8000/app/
# 5. Optional: drive the simulator from another terminal:
curl -X POST http://127.0.0.1:8088/control \
  -H 'Content-Type: application/json' -d '{"level":"high"}'
```

Or run the test suite to validate end-to-end:

```bash
python3 -m pytest tests/ --timeout=60
# 27 passed in ~38s
```

## How to test the **real ESP32 firmware over WiFi**

The firmware is independently testable — once flashed, the WiFi side
exposes an HTTP server you can hit directly without the Python backend.

```bash
cd firmware
idf.py set-target esp32s3
idf.py menuconfig          # set Echolocate Configuration → SSID/Password
idf.py build flash monitor # flashes & opens serial monitor

# Boot log prints something like:
#   I (5273) echolocate: WiFi connected. IP: 172.20.10.5
#   I (5273) echolocate:   curl http://172.20.10.5/health

# From any laptop on the same hotspot:
curl http://172.20.10.5/health
# {"ok":true,"firmware":"echolocate-csi-1.0",...,"packets_received":1247}

curl http://172.20.10.5/stats
# {"samples":1247,"rolling_variance":12.4,"occupancy_hint":"low",...}

# Or open a browser:
open http://172.20.10.5/        # auto-refreshing HTML status page
open http://echolocate.local/   # mDNS works too
```

If `packets_received` is rising and `rssi` ≥ -75 dBm, the firmware is healthy.
This is the proof that the sensor's WiFi side actually works — totally
independent of whether the backend is running. See
[`firmware/README.md`](firmware/README.md) for full troubleshooting.

## Pipeline data flow

```
Phone hotspot (2.4 GHz)            ESP32-S3
        ↑                              │
        │ 100ms UDP "pings" ───────────┘
        │
        │ WiFi packets pass through people
        │
        ▼
   ESP32 captures CSI from each rx packet
        │
        ├── prints CSV line to UART  ──── ─── ─── ┐
        └── HTTP /stats, /csi/latest, ...        │
                                                  │
                                                  ▼
   Backend (Python)            connects via SERIAL_PORT={serial,tcp,file}
        │
        ├─ csi_detector: parse → mean amplitude → windowed variance
        │                ↓
        │           classify (calibrating | empty | low | moderate | high)
        │                ↓
        │   threshold breach → camera.capture_and_encode (ephemeral)
        │                          → spatial_analyzer (Claude Vision)
        │                          → DEL image_b64 ← privacy invariant
        │                          → store metadata in SQLite
        │
        ├─ token_manager: anonymous rotating IDs (15 min), zone overlap detection
        ├─ push_notifier: Web Push via VAPID
        └─ FastAPI: REST + WebSocket
                ↓
   Frontend PWA (vanilla HTML/JS, served at /app/)
        ├─ Individual view: gauge + push enrollment + report-positive
        ├─ Operator view:   live chart + observations + report generator
        ├─ Chat:            ask the AI anything
        └─ Privacy:         what is/isn't collected
```

## Privacy invariants — verified in code

These are tested in `tests/test_storage.py`:

```python
def test_schema_has_no_image_column():
    """SQLite has nowhere to store an image. Privacy enforced at the
    schema level, not just at the policy level."""
```

And in `backend/main.py::_capture_and_analyze`:

```python
spatial = await spatial_analyzer.analyze_snapshot(image_b64)
# PRIVACY INVARIANT: drop the base64 immediately after the network call returns.
del image_b64
```

The camera handle is opened, one frame is read, the handle is released —
then the cv2 capture goes out of scope. The base64 string lives only as
long as the Vision request takes. The image never touches disk.

## Status (2026-05-09)

- [x] ESP32-S3 firmware (ESP-IDF, with WiFi HTTP test server)
- [x] Python simulator (TCP + HTTP, no hardware needed)
- [x] Backend (CSI parser, FastAPI, all stub-friendly when API keys missing)
- [x] Anonymous contact tracing + Web Push wiring
- [x] PWA (operator dashboard, individual view, chat, privacy, report)
- [x] **27 passing tests** including e2e simulator + backend integration

Open before hackathon day:

- Flash the firmware on real hardware and confirm the boot banner appears
- Calibrate variance thresholds against the real room (Lodge @ Sixth)
- Provision VAPID keys + ANTHROPIC_API_KEY for full demo
- Set up `ngrok` so the PWA is reachable over HTTPS for iOS push
