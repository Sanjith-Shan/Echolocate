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

The PWA now has **three tabs**: **Home / Consumer / Business**.

### 2.1 Sarah / Marcus — the *watched* (Home + Consumer tab)

Goal: confirm anyone can audit the system without enrolling.

1. **Land on Home** (the default tab). This is the page someone scans the
   QR code at the door to land on. No login.
2. Read the "Right now" card → should say `Calm right now`.
3. Scroll: "What IS / NEVER collected" lists, "Verify the device yourself"
   link → opens `http://localhost:8088/health` in a new tab.
4. Tap **"I'm a Consumer"** — switches to the Consumer tab.
5. **Check in:** type "main" in the zone field, tick "Felt crowded",
   click the big "I'm here" button. Toast: "Checked in to main".
6. The "My visits" card shows the visit you just logged with the
   "felt crowded" badge.
7. Scroll to "Speak up — anonymously" and submit a concern. No name
   attached.

### 2.2 Consumer ↔ Consumer exposure flow (two browsers)

This is the core privacy claim: when one consumer reports sick, anyone who
overlapped them gets notified — without learning *who* reported it.

1. In **Chrome** (browser 1): Consumer tab → "I'm here" with zone "main".
2. In **Firefox or Safari** (browser 2): Consumer tab → "I'm here" with
   zone "main". Each browser has its own anonymous token in localStorage.
3. In browser 2: tap **"Report positive test"**, confirm. Toast says
   "Sent N anonymous alerts".
4. Switch to browser 1 and look at the **Notifications inbox** — there's
   a new exposure alert. The red badge on the Consumer tab shows the
   unread count. **The alert body never names browser 2.**
5. Click "Mark read" — the notification dims and the badge decrements.

If you only have one browser, run the equivalent over curl:

```bash
T1=$(curl -s -X POST http://localhost:8000/api/register -H 'Content-Type: application/json' -d '{}' \
     | python3 -c "import json,sys;print(json.load(sys.stdin)['token_id'])")
T2=$(curl -s -X POST http://localhost:8000/api/register -H 'Content-Type: application/json' -d '{}' \
     | python3 -c "import json,sys;print(json.load(sys.stdin)['token_id'])")

curl -s -X POST http://localhost:8000/api/consumer/check-in -H 'Content-Type: application/json' \
     -d "{\"token_id\":\"$T1\",\"zone\":\"main\"}"
curl -s -X POST http://localhost:8000/api/consumer/check-in -H 'Content-Type: application/json' \
     -d "{\"token_id\":\"$T2\",\"zone\":\"main\"}"
curl -s -X POST http://localhost:8000/api/consumer/report-sick -H 'Content-Type: application/json' \
     -d "{\"token_id\":\"$T2\"}"

curl -s "http://localhost:8000/api/consumer/notifications?token_id=$T1" | python3 -m json.tool
# Should show 1 exposure notification.
```

### 2.3 Yvonne — the business broadcasts to its visitors

Goal: business detects an issue (air filter offline, scheduled closure,
follow-up) and notifies everyone who was there in a window — anonymously.

1. **Switch to the Business tab.**
2. Scroll to "Notify visitors". The **From** / **To** fields are
   pre-filled with the past hour (UTC).
3. Pick `Type: General`, leave `Zone: main`, edit the body
   ("Air filtration was offline for 30 minutes during your visit. We're
   following up. No action needed.").
4. Click **Send broadcast**. The status text shows
   `Sent to N visitor(s)` — that N is exactly the number of consumers who
   checked into "main" in the past hour.
5. Switch back to the Consumer tab → Notifications inbox. The general
   broadcast is there alongside the exposure alert.

### 2.4 AI suggestion → operator decision loop

1. Trigger crowding from another terminal:
   ```bash
   curl -X POST http://localhost:8088/control \
     -H 'Content-Type: application/json' -d '{"level":"high"}'
   ```
2. Wait ~8 seconds. **Business tab → "AI suggestions waiting on you"**
   shows a new card.
3. Type a note ("Will rearrange tables Monday"), click **Accept**. The
   badge flips green.

### 2.5 Recalibrate, generate report, ask the AI

These tools live in **Business → System diagnostics** and **Business →
Ask Echolocate**, both collapsed by default. Click "Expand" on either.

- "Recalibrate baseline" → resets the variance baseline (use after the
  room is verified empty).
- "Generate report" → runs the Space Design Report (stub if no API key).
- Chat → ask "How busy is it?" — every response is logged as an AI
  decision in the same /api/decisions table.

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

Expect: **59 passed**. The suite includes:

- 5 CSI parsing tests (real CSV format, edge cases)
- 7 occupancy classification tests
- 6 anonymous-token tests (rotation, overlap detection)
- 3 storage tests (privacy invariants in the schema)
- 3 Welford statistics tests
- 3 e2e simulator+backend integration tests
- 4 diagnostics endpoint tests
- 6 frontend smoke tests (every static asset, all six tabs, references)
- 9 governance tests (decision log, transparency redaction, feedback loop)
- 12 consumer/business tests (check-in, exposure flow, broadcast,
  privacy-invariant assertions)
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
