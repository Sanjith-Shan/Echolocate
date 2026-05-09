# Testing Echolocate without ESP32 hardware

Everything below runs entirely on your laptop. The simulator (`sim/esp32_sim.py`)
is byte-identical to the real firmware from the backend's perspective — when
you eventually plug in a real ESP32, you change one env var (`SERIAL_PORT`)
and nothing else.

---

## Tier 0 — One-command boot

```bash
./run-demo.sh
```

That's it. The script creates the venv if needed, installs deps, boots the
simulator + backend, and prints all URLs. Leave this terminal open. Every
test below assumes it's running.

You should see this banner:

```
============================================================
  Echolocate is running.

  PWA:           http://localhost:8000/app/
  Diagnostics:   http://localhost:8000/api/diagnostics
  REST status:   http://localhost:8000/api/status
  Simulator UI:  http://localhost:8088/

  Drive crowd levels from another terminal:
    curl -X POST http://localhost:8088/control \
      -H 'Content-Type: application/json' -d '{"level":"high"}'

  Press Ctrl-C to stop everything.
============================================================
```

---

## Tier 1 — 5-minute smoke test

### 1.1 Confirm the "device" is alive (the simulator stands in for the ESP32)

```bash
curl -s http://localhost:8088/health | python3 -m json.tool
```

Expect: `firmware: "echolocate-csi-sim-1.1"`, `packets_received` rising on
each call, `ping_replies` ~= packets_received.

This is **the same shape** the real ESP32 firmware exposes. When you have
hardware, you'll point at `http://<esp-ip>/health` and the answer will be
identical except `chip: "esp32s3"` and a real `ssid`.

### 1.2 Confirm the backend is reaching the "device"

```bash
curl -s http://localhost:8000/api/firmware-status | python3 -m json.tool
```

Expect: `"reachable": true` and `device.firmware: "echolocate-csi-sim-1.1"`.
This is the proxy from backend → device's HTTP server. Same call works
against real hardware.

### 1.3 Open the PWA

In a browser: <http://localhost:8000/app/>

You should see the connection pill in the header flip from `offline` (red)
to `live` (green) within a few seconds, and the gauge on the **Me** tab
fill in.

If the pill stays red, the WebSocket isn't connecting. Check the demo
terminal for backend errors.

### 1.4 Open the Diagnostics tab

Tap **Diag** at the bottom. You should see green LEDs for:

- ✅ CSI stream
- ✅ Baseline calibration (after ~10s)
- ✅ Device reachable over network

…and red LEDs for whatever API keys you haven't set up yet — the page tells
you exactly what to add to `.env` to fix each one.

---

## Tier 2 — Walk through each user story

### 2.1 Sarah / Marcus — the *watched*

Goal: confirm anyone can audit the system without enrolling.

1. **Tap the "Public" tab.** This is the page someone scans the QR code at
   the door to land on. No login.
2. Read the "Right now" card → should say `Calm right now`.
3. Scroll: "What IS collected" / "What is NEVER collected" lists,
   "Verify the device yourself" button.
4. **Click "Verify the device yourself"** → opens
   `http://localhost:8088/health` in a new tab. The watched can directly
   read what the operator reads. (On real hardware this opens the ESP32's
   own page.)
5. Submit anonymous feedback: tap **Me**, fill the form ("Felt cramped at
   noon"), Submit. Toast says "Submitted anonymously".
6. Tap **Public** again — your feedback is on the public page. No name
   attached.

### 2.2 Yvonne — the operator (the paying customer)

Goal: confirm the dashboard is usable without engineering background.

1. **Tap "Operator".** Top of the panel: `Display mode · ☑ Plain language`.
   Verify that "Stream health" and "System status" cards (with engineer-speak
   like *variance ratio*) are **hidden** by default.
2. Untick the toggle. The technical cards reappear. Re-tick.
3. **Trigger an AI judgment** — in another terminal:
   ```bash
   curl -X POST http://localhost:8088/control \
     -H 'Content-Type: application/json' -d '{"level":"high"}'
   ```
   Wait ~8 seconds. The "AI suggestions waiting on you" card should fill
   in with at least one decision card.
4. **Act on the AI suggestion.** Type a note in the textbox, click
   **Accept** (or **Considered** / **Reject**). The badge flips colour and
   your note is persisted.
5. **Tap "Public"** — the same decision now shows the new status badge and
   the note you wrote.

That's the full **AI suggests → operator decides → public sees** loop.

### 2.3 Recalibrate flow (when the room wasn't actually empty at boot)

1. **Operator tab → "Recalibrate baseline"**. Confirm the dialog. Toast:
   "Recalibrating — keep the space empty for 10s".
2. Wait 10s. The Live Occupancy gauge briefly shows `calibrating…` then
   flips back to a real level based on whatever the simulator is currently
   producing.

### 2.4 Anonymous push-notification enrollment

1. **Me tab → "Enable notifications"**. Browser asks for permission. Decline
   or accept — either is fine for the test.
2. Status flips to `✓ Enrolled` and the **"I tested positive"** button
   appears.
3. Click "I tested positive" → confirms → toast shows `Sent N anonymous
   alerts` (0 unless you registered multiple tokens). The flow runs
   through the same code path that real exposure notifications use.

### 2.5 Chat with the AI

1. **Chat tab.** Type "How crowded is it?" → Send.
2. You'll get a reply prefixed with `(Stub reply — set ANTHROPIC_API_KEY...)`.
   This proves the endpoint works; the actual Claude reasoning is gated
   behind the API key.
3. **Tap Operator → AI suggestions waiting on you** — your chat call also
   shows up in the AI Decision Log. You can mark it accepted/rejected
   like any other AI judgment. *Every* AI call is auditable.

### 2.6 Generate a Space Design Report

1. **Operator tab → "Generate report"**. With at least 1 observation
   (you triggered one in 2.2), you'll get a stub report.
2. The report itself also gets logged in the AI decisions table. Check by
   curling `/api/decisions` or by looking on the **Public** tab.

---

## Tier 3 — Privacy invariants you can verify by hand

These are the claims the product makes. Don't take my word — check yourself:

### 3.1 The schema literally cannot store an image

```bash
sqlite3 /tmp/test_final.db ".schema"
```

(Use whatever `ECHOLOCATE_DB` value the backend printed at boot — probably
`echolocate.db` if you ran without overriding.)

Expect: no column named `image`, `photo`, `frame`, `jpeg`, `snapshot_data`,
`face`, `name`, `email`, `ip`, `mac`, `device`, `token`. There's nowhere
to put one.

### 3.2 The public transparency page does NOT leak raw AI input/output

```bash
# Operator view — sees raw_input / raw_output
curl -s http://localhost:8000/api/decisions | python3 -m json.tool | grep raw_

# Public view — should NOT show those keys
curl -s http://localhost:8000/api/transparency | python3 -m json.tool | grep raw_
```

The first returns lines; the second returns nothing.

### 3.3 The community-feedback table has no identifier columns

```bash
curl -s http://localhost:8000/api/community-feedback | python3 -m json.tool
```

Each entry has only: `id`, `created_at`, `sentiment`, `message`, `zone`.
No token, no IP, no anything that could be used to track who submitted it.

---

## Tier 4 — Run the full test suite (~2 min)

```bash
# Stop the demo (Ctrl-C in the demo terminal) so ports are free
source .venv/bin/activate
python3 -m pytest tests/ --timeout=60 -q
```

Expect: **47 passed**. The suite includes:

- 5 CSI parsing tests (real CSV format, edge cases)
- 7 occupancy classification tests
- 6 anonymous-token tests (rotation, overlap detection)
- 3 storage tests (privacy invariants in the schema)
- 3 Welford statistics tests
- 3 e2e simulator+backend integration tests
- 4 diagnostics endpoint tests
- 6 frontend smoke tests (every static asset, all six tabs, references)
- 9 governance tests (decision log, transparency redaction, feedback loop)
- 1 .env autoload test

If any fail, that's a real regression worth pasting.

---

## Tier 5 — Edge cases you can poke at

### 5.1 Disconnect the "device" mid-demo

In the demo terminal, hit Ctrl-C to stop, then start *just* the backend
(not the simulator):

```bash
SERIAL_PORT=tcp://127.0.0.1:9999 \
  python3 -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

The PWA's connection pill flips to red, and Diagnostics shows clear red
LEDs with actionable blocker text ("No CSI lines parsed yet. Is the
simulator or ESP32 running?"). Restart the simulator and watch it recover.

### 5.2 API-key UX

Drop a fake key into `.env` at the project root:

```bash
echo "ANTHROPIC_API_KEY=sk-ant-fake-test-key" > .env
./run-demo.sh
```

The backend log shows `[backend] loaded env from /…/.env`. The Diagnostics
page now flips the "Anthropic API key" check from red to green (note: it
checks *presence*, not validity — the actual API call would fail with a
fake key, but the wiring is proven). Delete the file when done:

```bash
rm .env
```

### 5.3 Sim scenarios

```bash
./run-demo.sh --scenario surge       # auto-ramps empty → high → empty over ~2 min
./run-demo.sh --scenario queue       # spike at moderate, then back down
./run-demo.sh --level moderate       # lock to a specific level
```

Watch the operator sparkline chart. Each scenario produces a recognisable
shape; multiple AI decisions get logged as the system crosses thresholds.

### 5.4 WebSocket reconnect

In the PWA, in DevTools → Network → WS, kill the WebSocket. The pill
flips to `offline`. The poll fallback (every 5s) keeps the gauge fresh.
The WS auto-reconnects with exponential backoff up to 15s.

---

## What you genuinely CANNOT test without hardware

These will only validate when you flash the firmware on a real ESP32-S3:

1. **The actual CSI capture from real WiFi packets.** The simulator generates
   plausible synthetic data. Real CSI may have different absolute magnitudes,
   so the four-level thresholds (`empty / low / moderate / high`) might
   need recalibration. The *architecture* is sound; the *ratios* might shift.

2. **The ICMP ping fix on a real iPhone hotspot.** I switched from
   UDP-to-discard to `esp_ping` because the spec'd UDP approach often fails
   with iPhone APs. That's well-supported by the esp-csi project's choice
   of the same approach, but I haven't run it on your particular phone.

3. **iOS Safari push notifications.** They require HTTPS. Use ngrok on
   demo day:
   ```bash
   ngrok http 8000
   ```
   Open the resulting `https://*.ngrok-free.app/app/` and Add to Home
   Screen. Push only works from a home-screen-installed PWA on iOS 16.4+.

4. **The webcam capture path on Mac/Linux.** OpenCV may not be installed,
   in which case threshold breaches still log a `_no_camera` observation
   (which is the expected fallback — the test suite covers this path).

That's it. Everything else above can be validated right now on a stock
laptop with no special hardware.
