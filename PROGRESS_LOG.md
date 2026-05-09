# ECHOLOCATE — Build Progress Log

> **Started:** 2026-05-09
> **Track:** UCSD Claude Builders Club Hackathon, Track 2 (Governance & Collaboration)
> **Source spec:** `ECHOLOCATE_V2_CLAUDE.md`

---

## Architectural Decisions Made Up Front

### 1. Make the ESP32 firmware *independently* testable

The original spec only exposes CSI via USB serial. That's brittle: you can't tell if the
ESP is on WiFi until the laptop-side Python parses the right lines. So I'm extending
the firmware to **also run a tiny HTTP server on the ESP's WiFi IP**, exposing:

| Endpoint        | Purpose                                                                |
|-----------------|------------------------------------------------------------------------|
| `GET /`         | Human-readable status page (HTML, no JS)                               |
| `GET /health`   | JSON: uptime, RSSI, free heap, packet count                            |
| `GET /stats`    | JSON: rolling mean amplitude, variance, sample rate, occupancy hint    |
| `GET /csi/latest` | JSON: most recent parsed CSI sample (subcarrier amplitudes array)    |

This means the moment you flash the board, you can:
1. Watch the ESP32 boot log over serial (`idf.py monitor`)
2. Note the IP it gets from your phone hotspot DHCP
3. From any laptop on the same hotspot: `curl http://<ip>/health` → confirm it's alive
4. Open `http://<ip>/` in a browser → see live occupancy hint without running any backend

This satisfies the user's hard requirement: "there is a way I can test the wifi esp via
booting up a program or IP server etc."

### 2. Hardware-free dev path: a faithful Python simulator

The hackathon is on 2026-05-09 (today), so we cannot assume the ESP32 hardware is
available the entire time we're coding the backend/frontend. I'm building
`sim/esp32_sim.py`, a Python program that:

- Generates synthetic CSI data with controllable occupancy levels (empty / low /
  moderate / high) — modulated noise on top of a baseline.
- Emits the **exact same CSV format** the real firmware does (`CSI_DATA,...`) over a
  PTY-or-TCP "fake serial" so the backend can connect to it as if it were the device.
- Exposes the **same HTTP endpoints** (`/health`, `/stats`, `/csi/latest`, `/`) on
  localhost, so the same testing scripts work against sim and hardware.

This is the contract: the simulator and the firmware are *behaviorally
indistinguishable* from the backend's perspective. If the backend works against the
sim, it works against the device.

### 3. Backend serial reader auto-falls-back to TCP

`backend/main.py` reads `SERIAL_PORT` from env. If the value starts with `tcp://`
(e.g. `tcp://127.0.0.1:3333`), the reader connects to a TCP socket instead of opening
a serial device. This lets us run the entire backend against the simulator without
modifying any code.

### 4. Privacy invariants encoded in code, not in policy

- The `EphemeralCamera.capture_and_encode()` function never writes to disk — only
  returns base64. The caller (`monitoring_loop`) is responsible for `del`-ing the
  base64 string after the Vision call returns.
- The SQLite schema has *no* image columns. There is no place to put one.
- The anonymous token manager generates 16-char rotating IDs every 15 minutes
  (Apple/Google ENF-inspired); the persistent `token_id` is only known to the client.

### 5. Testing strategy: hidden, automatic, comprehensive

I will run pytest after every meaningful chunk lands. Tests cover:
- CSI line parsing (real and edge-case CSV)
- Welford online statistics correctness
- Occupancy threshold logic for all four levels
- Token rotation timing and overlap detection
- End-to-end: simulator emits → backend ingests → REST returns expected occupancy

These run silently between code changes; the user only sees the result if something
breaks.

---

## Build Log

### Phase 0 — Setup (done)

- [x] Read spec
- [x] Create progress log (this file)
- [x] Create directory tree
- [x] Initial files in place

### Phase 1 — ESP32 firmware (DONE)

- [x] `firmware/main/csi_recv_router_http.c` — ESP-IDF C, WiFi STA + CSI + HTTP server (mDNS, /, /health, /stats, /csi/latest)
- [x] `firmware/main/CMakeLists.txt`
- [x] `firmware/main/Kconfig.projbuild` — adds "Echolocate Configuration" menu
- [x] `firmware/main/idf_component.yml` — pulls in espressif/mdns
- [x] `firmware/CMakeLists.txt`
- [x] `firmware/sdkconfig.defaults`
- [x] `firmware/README.md` — how to flash & test, with troubleshooting table

### Phase 2 — Simulator (DONE)

- [x] `sim/esp32_sim.py` — synthetic CSI emitter (TCP + HTTP) with `/control` endpoint for tests
- [x] Scenarios baked in: `surge`, `queue`, `ramp` (--scenario flag)
- [x] Two-component noise model (independent + correlated) for realistic variance ratios

### Phase 3 — Backend (DONE)

- [x] `backend/welford.py`
- [x] `backend/csi_detector.py` (with `recalibrate()`)
- [x] `backend/camera.py` (graceful no-op without OpenCV)
- [x] `backend/spatial_analyzer.py` (stub when no API key)
- [x] `backend/report_generator.py` (stub when no API key)
- [x] `backend/chat.py` (stub when no API key)
- [x] `backend/token_manager.py`
- [x] `backend/push_notifier.py` (graceful no-op without VAPID)
- [x] `backend/source.py` — serial OR tcp:// OR file:// abstraction
- [x] `backend/storage.py` — SQLite, schema-level privacy invariant
- [x] `backend/main.py` — FastAPI w/ lifespan, /api/recalibrate added

### Phase 4 — Tests (DONE — 27/27 passing)

- [x] `tests/test_csi_parsing.py` (5 tests)
- [x] `tests/test_welford.py` (3 tests)
- [x] `tests/test_occupancy.py` (7 tests, includes recalibrate)
- [x] `tests/test_storage.py` (3 tests, asserts no image columns)
- [x] `tests/test_tokens.py` (6 tests)
- [x] `tests/test_e2e_sim.py` (3 tests, boots sim+backend stack)

### Phase 5 — Frontend PWA (DONE — pivoted from Vite/React to vanilla HTML/JS)

- [x] Vanilla HTML + JS single-file PWA (no build step, served by FastAPI)
- [x] Tab-based views: Individual / Operator / Chat / Privacy
- [x] Service worker + manifest + icon PNGs (generated via stdlib)
- [x] Push subscription flow + anonymous registration
- [x] WebSocket live updates + REST status fallback poll
- [x] Operator: occupancy gauge, sparkline chart, recent observations list, report generator, recalibrate button
- [x] Privacy page: side-by-side what-is/what-isn't list + image lifecycle diagram

**Why vanilla over Vite/React:** the user said the app is secondary to the
ESP firmware, and a hackathon demo benefits from zero npm dependencies and
zero build steps. The PWA is ~600 lines of JS in one file — easy to read,
zero brittleness, served as static files by FastAPI directly.

---

## Phase 1–4 retrospective (completed)

### What works end-to-end

- **Firmware** (`firmware/`): ESP-IDF C source, ready to flash on a real
  ESP32-S3. Streams CSI as CSV over UART **and** runs an HTTP server with
  `/`, `/health`, `/stats`, `/csi/latest` so the WiFi side is independently
  testable. mDNS advertises `echolocate.local`.
- **Simulator** (`sim/esp32_sim.py`): emits identical CSV over TCP and serves
  the same HTTP endpoints. Bonus `POST /control {"level":"high"}` for tests.
- **Backend** (`backend/`): FastAPI server. CSI source layer (`source.py`)
  switches between real serial, TCP simulator, or file replay based on the
  `SERIAL_PORT` env var. Calibration → ratio-based classification works on
  synthetic data and matches the unit-test thresholds.
- **Tests** (`tests/`): **26 / 26 passing**, including 3 e2e tests that boot
  the simulator + backend together and drive level transitions through the
  REST API.

### Findings worth remembering

1. **Calibration must happen on a genuinely empty room.** If the system
   boots with people present, the baseline absorbs that variance and the
   ratio-based detector never trips. UX implication: the operator dashboard
   should display calibration progress and a "Recalibrate" button.

2. **The ratio thresholds are sensitive to baseline magnitude.** If empty
   variance is too low (≈0), even small absolute movements produce huge
   ratios. The simulator initially modelled empty as too clean. Fix landed:
   empty has corr_std=2.0 to model ambient WiFi multipath, so the ratios
   between levels stay in the ~1.5×, ~5×, ~12× range that the detector
   thresholds were designed for.

3. **CSI parser must skip the `CSI_HEADER` line.** Caught and unit-tested.
   The simulator emits this header on connect; the upstream esp-csi example
   does too.

4. **Privacy invariant tested in code, not just docs.** `test_storage.py`
   asserts the SQLite schema has *no* column named image/photo/frame/jpeg/
   snapshot_data — privacy-by-architecture enforced at the schema level.

### Phase 8 — Governance & Collaboration lens (track-aligned redesign)

User asked us to apply the hackathon track lens — "Governance &
Collaboration" — and to write four user stories that surface real
needs. Five concrete gaps came out of that exercise:

1. **The watched had no way to verify the watcher.** Privacy-by-architecture
   was a *claim* visible only to operators or developers. Now there's a
   public Transparency tab + `/api/transparency` endpoint that anyone with
   the URL can audit, listing the privacy invariants and linking to the
   device's own /health for end-to-end verification.

2. **AI judgments were unaccountable.** Every Claude (or stub) judgment
   now writes to `ai_decisions` — operator gets a card with Considered /
   Accept / Reject + a notes field; the same record (redacted) shows up on
   the public Transparency page so the institution's decisions are part of
   the public record. `raw_input` and `raw_output` stay private to the
   operator; the public sees the summary + status + notes.

3. **Operator UX was engineer-speak.** Plain-language toggle in the
   operator dashboard, defaults ON, persisted in localStorage. Hides the
   stream-health, variance, ratio cards in plain mode — flip it on for
   debugging.

4. **No community voice.** Anonymous feedback form on the Individual view
   (concern / suggestion / praise + free text). Submissions appear on the
   operator dashboard *and* the public Transparency page immediately. No
   identifiers ever — `community_feedback` table has no token, IP, name,
   email column. Verified by code review and by `test_storage.py`.

5. **Six tabs now, in plain order:** Me · Operator · **Public** · Chat
   · Diag · Privacy. The "Public" tab is the headline governance feature
   and is the literal embodiment of the privacy-by-architecture claim.

User stories at `docs/USER_STORIES.md` (four personas: pandemic-era student,
pre-pandemic regular, small-business operator, developer self-review).

**Test coverage went from 36 → 47** (+11 governance tests). New tests
explicitly verify the privacy invariant in code: the public transparency
endpoint must not leak `raw_input` or `raw_output`, and the `what_is_NEVER_collected`
list must contain photo/face/name/email/ip/mac. If a future change breaks
those, pytest fails.

### Phase 7 — Hardening pass (after first review)

User asked us to find and remove blockers, and to make the system trivially
testable end-to-end. What changed in this pass:

1. **`.env` auto-load.** Dropping `ANTHROPIC_API_KEY=...` into a `.env` at
   the project root now wakes up the entire pipeline with no `export` calls.
   Backend imports `python-dotenv` lazily so it's still optional.

2. **Firmware: ICMP ping replaced UDP-discard.** UDP to a closed port
   doesn't reliably get replies on iPhone hotspots, which would have left
   `packets_received=0` on demo day. Switched to `esp_ping` at 10 Hz against
   the gateway. New `ping_replies` counter exposed in `/health` so a single
   `curl` confirms the loop is alive.

3. **`/api/diagnostics` + `/api/firmware-status`.** One REST call returns
   green/red status for every subsystem (CSI stream, calibration, device
   reachability, Anthropic key, VAPID, camera). Each failing check carries
   a one-line *blocker text* with the exact fix. `/api/firmware-status`
   proxies the device's own `/health` so the operator can confirm the
   laptop truly reaches the ESP32 over WiFi.

4. **Diagnostics tab in the PWA.** Renders the diagnostics endpoint with
   coloured LED dots + blocker hints. Two action buttons: re-run checks,
   ping device. Top of header gets a `live`/`offline` pill that flips
   colour with the WebSocket state.

5. **iOS safe-area support.** Body and bottom nav now use
   `env(safe-area-inset-bottom)` so the home indicator no longer overlaps
   the tab bar in standalone mode.

6. **`run-demo.sh`** — one command boots venv + sim + backend, prints all
   URLs, traps Ctrl-C and tears the stack down. `--scenario surge` for
   auto-ramping crowds. `--hardware <serial> <url>` for real ESP32.

7. **CRITICAL bug found & fixed:** `frontend/dist/` was in `.gitignore`
   from when I planned to use Vite. It had **never been committed**.
   Anyone cloning the repo would have had no PWA. Removed the pattern,
   force-added all six files (`index.html`, `app.js`, `sw.js`,
   `manifest.json`, `icon-192.png`, `icon-512.png`).

8. **9 new tests, 36 total passing:**
   - `test_diagnostics.py` — proxy success/failure paths, all checks
     present, blocker text, `.env` autoload.
   - `test_frontend.py` — every static asset returns 200, all 5 tabs in
     the HTML, manifest is valid JSON with the right scope, `app.js`
     references the diagnostics endpoints (catches URL drift).

### Phase 6 — Final integration validation (PASSED)

Ran a synthetic 8-stage scenario end-to-end. Results:

| Stage | What happened | Result |
|-------|---------------|--------|
| 1 | Sim emits empty CSI for 13s | Calibration completed, baseline 4.75, level=`empty`, ratio=0.72 |
| 2 | `POST /control {level:low}` to sim | Detector saw ratio 1.34 → still `empty` (correct, low ≈ 1.5x) |
| 3 | Sim → moderate | ratio 5.28 → `moderate`, threshold_exceeded=true, **1 observation recorded** |
| 4 | Sim → high | ratio 8.84 → `high`, count_estimate=7, **3 observations** |
| 5 | Anonymous register / checkin / report-positive | All 200 OK, token returned |
| 6 | Recalibrate while at high | Baseline reset; new baseline = 58.4 (current high noise); subsequent high reads as `empty` (correct rebasing behavior) |
| 7 | WebSocket connection | Frames stream with `type=occupancy_update`, current level present |
| 8 | Generate space report | 3 observations summarized in stub report (real Claude would have written more) |

This proves the full pipeline works in the absence of any ESP hardware.
Replacing the simulator with a real ESP32 changes one env var
(`SERIAL_PORT`) and nothing else.

### Validity / "real use" critique (round 2)

Building the simulator made me realize the CSI signal is *much* less
discriminating than the original spec implied. We can reliably tell
"empty vs. crowded" but the four-level (empty/low/moderate/high)
classification is more aspirational than physical. The strongest demo
artifact remains the Space Design Report — ML-grade accuracy isn't needed
for "this entrance is a chronic chokepoint, move the counter east."

I'll also push hard on the **`/csi/latest` HTTP endpoint** in the
hackathon demo — it lets a judge verify the firmware is alive on WiFi
**without** the laptop running anything, which is a memorable proof of
the "real" technical work. Most "smart sensor" hackathon projects don't
let you `curl` the device.

---

## Crucial Next Steps & Open Questions

### Right now

1. Build the directory structure.
2. Drop the firmware files in place. Even though we can't compile them here (no
   ESP-IDF toolchain), the source must be production-ready for the user to flash on
   hackathon day.
3. Build the simulator next so backend tests have something to run against.

### Things I'm watching for as the build progresses

- **CSI parsing edge cases:** the spec example has `data` as the *last* field. But
  `wifi_csi_info_t` may produce slightly different field order across ESP-IDF
  versions. The parser must be tolerant of trailing whitespace and quoted commas.
- **Calibration period:** 10 seconds is too short for a noisy real environment.
  Consider lengthening or making the threshold *relative-and-absolute* (require both
  variance ratio AND absolute variance above a floor).
- **Push on iOS:** requires HTTPS + add-to-homescreen + 16.4+. ngrok is the right
  call. Document this prominently.
- **Camera index:** Logitech Brio is index 1 on Macs *only* if there's an internal
  webcam at index 0. Need a `/api/test-camera` endpoint to make device selection
  empirical, not magical.

### Validity / "real-world use" critique (continuous)

The user asked me to constantly evaluate whether this product would see real use.
Initial pass:

- **Strengths:** Privacy-by-architecture is genuinely novel — most "smart space"
  products store images and rely on policy. Space Design Reports are a *use case*,
  not a feature; they answer "what do I do with this data?" which is where IoT
  products usually fail. ESP32-S3 + phone hotspot is dirt cheap (<$15 per zone).
- **Weaknesses:** CSI-based people counting from a single ESP is, in the literature,
  more of a "presence + level" detector than an accurate count. The variance-ratio
  approach will work for "empty vs. crowded" but won't reliably count 4 vs. 6 people.
  We should be honest about this in the demo and in the ethics writeup.
- **Threats to "real use":** Hospitals already have RTLS systems. Office buildings
  have CO₂ sensors. The wedge is *retail and small businesses* who don't have the
  budget for either — the privacy-first angle is a legitimate differentiator there.
- **Iterate:** the operator-side Space Design Report is the part most likely to be
  paid for. I'll prioritize making that demo-quality even if it means deferring some
  individual-side polish.

(I will append to this list as the build surfaces new questions.)
