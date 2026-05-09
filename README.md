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
./run-demo.sh                         # boots sim + backend, prints URLs
# Open http://localhost:8000/app/ — that's the PWA.
# Open http://localhost:8000/app/#diagnostics — green-light system check.

# Drive crowd levels from another terminal:
curl -X POST http://127.0.0.1:8088/control \
  -H 'Content-Type: application/json' -d '{"level":"high"}'

# Other modes:
./run-demo.sh --scenario surge        # auto-ramp empty→high→empty
./run-demo.sh --hardware /dev/cu.usbmodem14101 http://172.20.10.5
```

If you'd rather wire it up by hand:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
python3 -m sim.esp32_sim --no-stdin --tcp-port 3333 --http-port 8088 &
SERIAL_PORT=tcp://127.0.0.1:3333 FIRMWARE_HTTP_URL=http://127.0.0.1:8088 \
  python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

### API keys

Drop a `.env` file at the project root and the backend auto-loads it:

```env
ANTHROPIC_API_KEY=sk-ant-...
VAPID_PRIVATE_KEY=...
VAPID_PUBLIC_KEY=...
VAPID_CLAIMS_EMAIL=mailto:you@example.com
```

The pipeline runs in stub mode if any of these are missing — the
`/app/#diagnostics` page tells you exactly which ones aren't set
and what to add to fix them.

Or run the test suite to validate end-to-end:

```bash
python3 -m pytest tests/ --timeout=60
# 36 passed in ~55s
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

- [x] ESP32-S3 firmware (ESP-IDF, ICMP-driven CSI, WiFi HTTP test server)
- [x] Python simulator (TCP + HTTP, no hardware needed, `/control` for tests)
- [x] Backend (CSI parser, FastAPI, all stub-friendly when API keys missing)
- [x] `/api/diagnostics` + `/api/firmware-status` — green/red system health
- [x] Auto-loaded `.env` from project root — drop the key, it works
- [x] **Governance surface:** AI decision log, operator accept/reject loop,
      public transparency page, anonymous community feedback
- [x] PWA (Me / Operator / **Public** / Chat / Diag / Privacy)
- [x] Plain-language toggle (defaults on; non-technical operators can use
      the dashboard without seeing "variance ratio")
- [x] One-command demo launcher (`./run-demo.sh`)
- [x] **47 passing tests** — privacy invariants enforced in test code,
      not just docs (e.g. public transparency must not leak raw I/O)

Open before hackathon day:

- Flash firmware on real hardware; confirm `curl http://<ip>/health` shows
  `ping_replies` rising
- Calibrate variance thresholds against the real room (Lodge @ Sixth)
- Provision VAPID keys + ANTHROPIC_API_KEY in `.env` (auto-loaded)
- Set up `ngrok` so the PWA is reachable over HTTPS for iOS push
