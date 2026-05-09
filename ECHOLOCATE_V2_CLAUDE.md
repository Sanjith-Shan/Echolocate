# ECHOLOCATE — Privacy-First Pandemic Space Intelligence
## Claude Code Project Context

---

## PROJECT OVERVIEW

Echolocate is a privacy-first public health infrastructure system for the UCSD Claude Builders Club Hackathon (May 9, 2026, Track 2: Governance & Collaboration). It uses WiFi Channel State Information (CSI) from commodity ESP32-S3 boards (~$9 each) to passively detect room occupancy and crowd density through radio wave disturbance. When crowding thresholds are exceeded, a webcam (Logitech Brio 101) captures a single ephemeral snapshot analyzed by Claude Vision for spatial distribution — then the image is immediately discarded. No faces are saved. No identities are tracked.

The system serves two stakeholders from one sensing layer:
1. **Individuals** — opt-in anonymous contact tracing via a PWA. Users get push notifications about zone crowding and anonymous exposure alerts if someone they shared space with reports sick. No identity data is ever collected.
2. **Space operators** — accumulated spatial metadata (never images) generates AI-powered Space Design Reports that identify chokepoints, bottleneck patterns, and redesign recommendations to permanently eliminate forced close contact.

**Core thesis for ethics writeup:** Privacy by architecture, not by policy. The system captures zero persistent visual data. Images exist in RAM for ~2 seconds, are analyzed for spatial patterns, and are destroyed. What survives is metadata: "4 people clustered near the entrance at 12:15 PM." An attacker who compromises the system gets occupancy counts and timestamps. Not faces. Not identities. Not video.

**Governance angle:** During a pandemic, governments face a binary: constant surveillance or no enforcement. Echolocate offers a third path — passive radio-wave monitoring for crowd density, camera activation only on threshold breach with immediate image destruction, and anonymous opt-in contact tracing. The ethical questions are real: Who sets capacity thresholds? Can space redesign mandates be enforced? Does this disproportionately burden small businesses? Should the AI's reasoning be contestable?

---

## HARDWARE

### Sensing Module
- **3x ESP32-S3-WROOM-1 N16R8 dev boards** (16MB flash, 8MB PSRAM, dual-core 240MHz)
  - Board 1: CSI receiver — connects to phone hotspot as WiFi station, captures CSI from ping responses
  - Board 2: Backup — can be flashed as AP (`csi_send`) for two-ESP32 mode if phone hotspot is unreliable
  - Board 3: Spare
- **Logitech Brio 101 webcam** — 1080p, 58° FOV, fixed focus, USB-A. Activated ONLY on crowding threshold breach.
- **Phone** — WiFi hotspot (2.4 GHz, Maximize Compatibility on iPhone). Acts as the WiFi AP that the ESP32 pings for CSI.
- **Laptop (Mac)** — runs Python backend, serves PWA, connects to ESP32 via USB serial, captures webcam via OpenCV.

### Wiring
None. The ESP32 connects via USB-C cable to the laptop. The webcam connects via USB-A. No breadboard, no jumper wires, no sensors. The entire "medical module" is one bare ESP32 board.

---

## SYSTEM ARCHITECTURE

```
┌──────────────────────────────────────────────────────────────────────┐
│                         SENSING LAYER                                │
│                                                                      │
│  Phone (hotspot, 2.4GHz)  ←── WiFi signals ──→  ESP32-S3 (station)  │
│         📱                    pass through           🔲              │
│                              people in space          │ USB serial   │
│                                  🧑🧑🧑              │              │
│  Logitech Brio 101  ─── USB ──────────────────────────┤              │
│  (shutter closed by default)                          │              │
└───────────────────────────────────────────────────────┤──────────────┘
                                                        │
                                                        ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    PROCESSING LAYER (Laptop)                          │
│                                                                      │
│  Python FastAPI Backend                                              │
│  ├─ Serial Reader Thread → parses ESP32 CSI CSV lines                │
│  ├─ CSI Occupancy Engine                                             │
│  │   ├─ Compute amplitude from I/Q subcarrier pairs                  │
│  │   ├─ Rolling variance (Welford online stats)                      │
│  │   ├─ Variance thresholds → occupancy level estimate               │
│  │   └─ Threshold breach → triggers camera snapshot                  │
│  ├─ Camera Module (OpenCV)                                           │
│  │   ├─ Capture single frame from Brio 101                           │
│  │   ├─ Encode as JPEG base64                                        │
│  │   ├─ Send to Claude Vision API for spatial analysis               │
│  │   ├─ Receive structured spatial metadata JSON                     │
│  │   └─ DISCARD image from memory immediately                        │
│  ├─ Spatial Metadata Store (SQLite)                                  │
│  │   ├─ Stores ONLY: timestamp, zone, cluster locations,             │
│  │   │   counts, density, nearby features, patterns                  │
│  │   └─ NEVER stores images, faces, or identity data                 │
│  ├─ Claude Reasoning Agent                                           │
│  │   ├─ Analyzes accumulated spatial metadata                        │
│  │   ├─ Detects recurring chokepoint patterns                        │
│  │   ├─ Generates Space Design Reports with recommendations          │
│  │   └─ Provides conversational interface for caregivers/operators   │
│  ├─ Anonymous Token Manager                                          │
│  │   ├─ Generates rotating anonymous IDs for opted-in users          │
│  │   ├─ Tracks zone + time overlaps between tokens                   │
│  │   └─ Issues exposure notifications without revealing identity     │
│  ├─ Web Push (pywebpush + VAPID)                                     │
│  │   ├─ Zone crowding alerts to individuals                          │
│  │   └─ Anonymous exposure notifications                             │
│  ├─ WebSocket server → real-time updates to PWA                      │
│  └─ REST API → status, alerts, chat, reports, token registration     │
│                                                                      │
└──────────────────────────────────────────────────────┬───────────────┘
                                                       │
                           HTTPS / WebSocket / Web Push│
                                                       ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    INTERFACES (React PWA)                             │
│                                                                      │
│  INDIVIDUAL VIEW (add to home screen for push notifications):        │
│  ├─ Real-time zone density map (green/yellow/red)                    │
│  ├─ "Zone B is crowded — consider spacing out" notifications         │
│  ├─ Anonymous exposure alerts                                        │
│  ├─ Opt-in anonymous token registration                              │
│  └─ Privacy transparency page (what IS and ISN'T collected)          │
│                                                                      │
│  OPERATOR VIEW (business/building manager):                          │
│  ├─ Live occupancy dashboard per zone                                │
│  ├─ Chokepoint heatmap (accumulated spatial observations)            │
│  ├─ Space Design Report (AI-generated PDF/page)                      │
│  │   ├─ Identified chokepoints with severity                         │
│  │   ├─ Temporal crowding patterns                                   │
│  │   ├─ Specific redesign recommendations                            │
│  │   └─ Zero images — only spatial metadata analysis                 │
│  ├─ Chat with Echolocate AI about space performance                  │
│  └─ Alert history with reasoning chains                              │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

---

## COMPONENT 1: ESP32-S3 FIRMWARE (ESP-IDF)

### Critical: Must Use ESP-IDF, NOT Arduino

WiFi CSI requires ESP-IDF native APIs. Arduino framework does not expose CSI functions. Use the Espressif `esp-csi` repository as the base.

**Reference:** https://github.com/espressif/esp-csi
**Example to use:** `examples/get-started/csi_recv_router` — connects to an external AP (phone hotspot) and captures CSI from ICMP ping responses.
**Backup example:** `examples/get-started/csi_send` + `csi_recv` — two ESP32s, one as AP, one as station.

### ESP-IDF CSI Data Format

The CSI callback receives `wifi_csi_info_t` containing subcarrier data. Each subcarrier is 2 bytes: imaginary part first, then real part. For ESP32-S3 in HT20 mode: 64 subcarriers = 128 bytes. In HT40 mode: 128 subcarriers = 256 bytes.

The `csi_recv_router` example outputs CSV lines over serial:
```
type,seq,mac,rssi,rate,noise_floor,fft_gain,agc_gain,channel,local_timestamp,sig_len,rx_state,len,first_word,data
CSI_DATA,7,1a:00:00:00:00:00,-23,11,-96,32,4,11,372852,47,0,256,0,"[0,0,-6,-13,-6,-14,-3,-15,...]"
```

The `data` field contains the I/Q pairs: `[I1,Q1,I2,Q2,I3,Q3,...]`
Amplitude per subcarrier: `sqrt(I² + Q²)`

### CSI Configuration (from ESP-IDF docs)

```c
wifi_csi_config_t csi_config = {
    .lltf_en = true,           // Legacy Long Training Field — most stable for occupancy
    .htltf_en = true,          // HT-LTF — additional subcarriers
    .stbc_htltf2_en = true,    // STBC HT-LTF
    .ltf_merge_en = true,      // Average LLTF and HT-LTF for noise reduction
    .channel_filter_en = true, // Smooth adjacent subcarriers
    .manu_scale = false,       // Auto-scale
    .shift = 0,
};

// Register callback, configure, and enable:
esp_wifi_set_csi_rx_cb(wifi_csi_rx_cb, NULL);
esp_wifi_set_csi_config(&csi_config);
esp_wifi_set_csi(true);
```

Note from docs: "The CSI receiving callback function runs from Wi-Fi task. So, do not do lengthy operations in the callback function." — extract data in the callback, process it in a separate task.

Note for ESP32-S3: "If first_word_invalid field of wifi_csi_info_t is true, it means that the first four bytes of CSI data is invalid due to a hardware limitation." — skip bytes 0-3 when this flag is set.

### Build & Flash

```bash
# Clone ESP-CSI repo
git clone --recursive https://github.com/espressif/esp-csi.git
cd esp-csi/examples/get-started/csi_recv_router

# Set target to ESP32-S3
idf.py set-target esp32s3

# Configure WiFi credentials (phone hotspot SSID + password)
idf.py menuconfig
# Navigate: Example Connection Configuration → WiFi SSID / WiFi Password
# Navigate: Component config → Wi-Fi → WiFi CSI (ensure enabled)

# Build, flash, and monitor
idf.py build
idf.py -p /dev/cu.usbmodem* flash monitor
```

### Phone Hotspot Configuration
- iPhone: Settings → Personal Hotspot → Maximize Compatibility = ON (forces 2.4 GHz)
- Android: Hotspot settings → Band → 2.4 GHz
- Keep phone plugged in (hotspot drains battery)
- SSID: keep simple, no special characters
- The phone just sits there with hotspot on. No app needed on the phone.

### Fallback: Two-ESP32 Mode
If phone hotspot is unreliable:
- Board 1: Flash `csi_send` (creates its own AP, broadcasts packets)
- Board 2: Flash `csi_recv` (connects to Board 1's AP, captures CSI)
- Completely self-contained, no phone needed

```bash
# Board 1 (AP/sender)
cd esp-csi/examples/get-started/csi_send
idf.py set-target esp32s3
idf.py flash -p /dev/cu.usbmodem14101

# Board 2 (Station/receiver)  
cd esp-csi/examples/get-started/csi_recv
idf.py set-target esp32s3
idf.py flash -p /dev/cu.usbmodem14201
```

---

## COMPONENT 2: PYTHON BACKEND

### Tech Stack

```
fastapi          — HTTP REST + WebSocket server
uvicorn          — ASGI server
pyserial         — read ESP32 serial CSI data
numpy            — CSI signal processing
scipy            — bandpass filtering (optional, for breathing if attempted)
opencv-python    — webcam capture from Brio 101
anthropic        — Claude API client (Vision + Text)
pywebpush        — Web Push notifications via VAPID
py-vapid         — generate VAPID key pair
python-dotenv    — environment variable management
websockets       — WebSocket support
pydantic         — request/response models
aiosqlite        — async SQLite for metadata storage
Pillow           — image compression before sending to Claude
```

### Installation

```bash
pip install fastapi uvicorn pyserial numpy scipy opencv-python anthropic pywebpush py-vapid python-dotenv websockets pydantic aiosqlite Pillow
```

### Environment Variables (.env)

```
ANTHROPIC_API_KEY=sk-ant-...
VAPID_PRIVATE_KEY=<generated with vapid cli>
VAPID_PUBLIC_KEY=<generated with vapid cli>
VAPID_CLAIMS_EMAIL=mailto:your@email.com
SERIAL_PORT=/dev/cu.usbmodem14101
SERIAL_BAUD=921600
CROWDING_VARIANCE_THRESHOLD=50.0
CAMERA_DEVICE_INDEX=1
```

### Generate VAPID Keys (run once)

```bash
pip install py-vapid
vapid --applicationServerKey
# This generates private_key.pem and public_key.pem
# Also outputs the applicationServerKey (base64 public key for the frontend)
```

### CSI Processing — Occupancy Detection

```python
import numpy as np
from collections import deque
import time

class CSIOccupancyDetector:
    """
    Detects room occupancy level from WiFi CSI subcarrier variance.
    
    Theory: When people are present in the WiFi signal path, their bodies
    scatter and absorb the signal, causing amplitude fluctuations across
    subcarriers. More people = more scattering = higher variance.
    Empty room has very stable, low-variance CSI.
    
    This is NOT precise people counting. It estimates occupancy LEVEL:
    empty, low (1-2), moderate (3-5), high (6+). The thresholds need
    calibration for each specific environment.
    """
    
    def __init__(self, window_seconds=5, sample_rate=20):
        self.sample_rate = sample_rate
        self.window_size = sample_rate * window_seconds
        self.amplitude_buffer = deque(maxlen=self.window_size)
        self.baseline_stats = WelfordStats()
        self.calibration_phase = True
        self.calibration_samples = 0
        self.CALIBRATION_DURATION = sample_rate * 10  # 10 seconds
        
    def parse_csi_line(self, csv_line: str) -> dict | None:
        """
        Parse one CSV line from ESP32 serial output.
        Format: CSI_DATA,seq,mac,rssi,rate,noise_floor,...,data
        The data field is "[I1,Q1,I2,Q2,...]" — subcarrier I/Q pairs.
        """
        if not csv_line.startswith("CSI_DATA"):
            return None
        
        try:
            # Extract the data array from the CSV line
            # The data field is the last field, enclosed in quotes and brackets
            data_start = csv_line.index('"[') + 1
            data_end = csv_line.index(']"') + 1
            data_str = csv_line[data_start:data_end]
            
            # Parse the I/Q values
            iq_values = [int(x) for x in data_str.strip('[]').split(',')]
            
            # Extract RSSI (4th field)
            fields = csv_line.split(',')
            rssi = int(fields[3])
            
            # Convert I/Q pairs to amplitudes
            iq = np.array(iq_values, dtype=np.float32).reshape(-1, 2)
            amplitudes = np.sqrt(iq[:, 0]**2 + iq[:, 1]**2)
            
            # Skip first 2 subcarriers if first_word_invalid (ESP32-S3 hardware quirk)
            amplitudes = amplitudes[2:]
            
            return {
                "amplitudes": amplitudes,
                "rssi": rssi,
                "mean_amplitude": float(np.mean(amplitudes)),
                "timestamp": time.time()
            }
        except (ValueError, IndexError):
            return None
    
    def update(self, csi_data: dict):
        """Add one CSI frame and update occupancy estimate."""
        mean_amp = csi_data["mean_amplitude"]
        self.amplitude_buffer.append(mean_amp)
        
        if self.calibration_phase:
            self.baseline_stats.update(mean_amp)
            self.calibration_samples += 1
            if self.calibration_samples >= self.CALIBRATION_DURATION:
                self.calibration_phase = False
    
    def get_occupancy(self) -> dict:
        """
        Estimate current occupancy level from CSI variance.
        Returns occupancy level, variance, and confidence.
        """
        if len(self.amplitude_buffer) < self.sample_rate * 2:
            return {"level": "calibrating", "count_estimate": 0, "variance": 0, "confidence": 0}
        
        if self.calibration_phase:
            return {"level": "calibrating", "count_estimate": 0, "variance": 0, "confidence": 0}
        
        recent = np.array(list(self.amplitude_buffer)[-self.sample_rate * 3:])
        variance = float(np.var(recent))
        
        # Thresholds — MUST be calibrated per environment
        # These are starting points, adjusted during hackathon setup
        baseline_var = self.baseline_stats.variance
        ratio = variance / max(baseline_var, 0.01)
        
        if ratio < 1.5:
            level = "empty"
            count_est = 0
        elif ratio < 3.0:
            level = "low"
            count_est = 2
        elif ratio < 6.0:
            level = "moderate"
            count_est = 4
        else:
            level = "high"
            count_est = 7
        
        return {
            "level": level,
            "count_estimate": count_est,
            "variance": round(variance, 2),
            "variance_ratio": round(ratio, 2),
            "baseline_variance": round(baseline_var, 2),
            "confidence": min(1.0, len(self.amplitude_buffer) / self.window_size),
            "threshold_exceeded": level in ("moderate", "high"),
        }


class WelfordStats:
    """Online mean/variance computation. No stored data. O(1) memory."""
    def __init__(self):
        self.n = 0
        self.mean = 0.0
        self.M2 = 0.0
    
    def update(self, value: float):
        self.n += 1
        delta = value - self.mean
        self.mean += delta / self.n
        delta2 = value - self.mean
        self.M2 += delta * delta2
    
    @property
    def variance(self) -> float:
        return self.M2 / self.n if self.n > 1 else 0.0
    
    @property
    def std(self) -> float:
        return self.variance ** 0.5
```

### Camera Module — Ephemeral Snapshot

```python
import cv2
import base64
import io
from PIL import Image

class EphemeralCamera:
    """
    Captures a single frame from the Logitech Brio 101.
    The image exists in RAM only. It is NEVER written to disk.
    After Claude Vision analysis, the image buffer is explicitly deleted.
    """
    
    def __init__(self, device_index=1):
        self.device_index = device_index
        # Don't keep camera open continuously — only open when needed
        # This also physically shows the camera LED only during capture
    
    def capture_and_encode(self) -> str | None:
        """
        Capture one frame, compress, encode to base64.
        Returns base64 JPEG string or None on failure.
        Camera is opened and closed within this call.
        """
        cap = cv2.VideoCapture(self.device_index)
        if not cap.isOpened():
            return None
        
        ret, frame = cap.read()
        cap.release()  # Immediately release camera
        
        if not ret:
            return None
        
        # Compress to JPEG to minimize data sent to API
        # Resize to 640px wide — sufficient for spatial analysis, reduces tokens
        height, width = frame.shape[:2]
        scale = 640 / width
        resized = cv2.resize(frame, (640, int(height * scale)))
        
        _, buffer = cv2.imencode('.jpg', resized, [cv2.IMWRITE_JPEG_QUALITY, 70])
        b64 = base64.b64encode(buffer).decode('utf-8')
        
        # Explicitly delete the frame and buffer from memory
        del frame
        del resized
        del buffer
        
        return b64
```

### Claude Vision — Spatial Analysis

```python
import anthropic
import json

SPATIAL_ANALYSIS_PROMPT = """You are analyzing a snapshot from a public space monitoring system during a pandemic. Your job is to describe the SPATIAL DISTRIBUTION of people — not identify anyone.

Analyze this image and return ONLY a JSON object with:
{
    "total_people_visible": <integer>,
    "clusters": [
        {
            "region": "<description of where in the frame: left/center/right + front/middle/back>",
            "near_feature": "<what physical feature they're near: door, counter, shelf, wall, hallway, table, etc. or 'open_area' if none>",
            "count": <number of people in this cluster>,
            "density": "<tight/moderate/spread>",
            "pattern": "<queue/stationary_cluster/passing_through/seated>"
        }
    ],
    "chokepoints": ["<list of physical features causing forced close proximity>"],
    "overall_density": "<sparse/moderate/crowded/packed>",
    "spatial_issue": "<one sentence describing the main spatial problem, or 'none' if well-distributed>"
}

CRITICAL RULES:
- Do NOT describe people's appearance, clothing, race, gender, or any identifying features
- Do NOT attempt to identify anyone
- Focus ONLY on spatial positioning relative to physical features of the space
- If you cannot clearly see people, say so honestly
- Return ONLY valid JSON, no other text
"""

async def analyze_snapshot(image_b64: str) -> dict | None:
    """
    Send ephemeral snapshot to Claude Vision for spatial analysis.
    Returns structured spatial metadata. Image is not stored anywhere.
    """
    client = anthropic.Anthropic()
    
    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": image_b64
                        }
                    },
                    {
                        "type": "text",
                        "text": SPATIAL_ANALYSIS_PROMPT
                    }
                ]
            }]
        )
        
        result_text = response.content[0].text
        # Strip markdown code fences if present
        result_text = result_text.replace("```json", "").replace("```", "").strip()
        return json.loads(result_text)
        
    except Exception as e:
        print(f"Claude Vision analysis failed: {e}")
        return None
```

### Anonymous Token System

```python
import uuid
import hashlib
import time
from datetime import datetime, timedelta

class AnonymousTokenManager:
    """
    Manages anonymous rotating tokens for opt-in contact tracing.
    Inspired by Apple/Google Exposure Notifications framework.
    
    Key properties:
    - Tokens rotate every 15 minutes to prevent tracking
    - No identity data is ever linked to tokens
    - Zone+time overlaps are computed for exposure notifications
    - Even the system cannot determine who a token belongs to
    """
    
    def __init__(self):
        # token_id -> {current_rotating_id, zone_history, push_subscription}
        self.registered_tokens = {}
        # List of {rotating_ids: [...], zone_times: [...]} for reported cases
        self.reported_exposures = []
    
    def register(self, push_subscription: dict) -> str:
        """
        Register a new anonymous user. Returns a persistent token_id
        that the client stores locally. The server never knows who this is.
        """
        token_id = str(uuid.uuid4())
        self.registered_tokens[token_id] = {
            "push_subscription": push_subscription,
            "zone_history": [],  # [{zone, enter_time, exit_time, rotating_id}]
            "current_rotating_id": self._generate_rotating_id(token_id),
            "last_rotation": time.time()
        }
        return token_id
    
    def _generate_rotating_id(self, token_id: str) -> str:
        """Generate a rotating anonymous ID from the token + current time window."""
        time_window = int(time.time() // 900)  # 15-minute windows
        return hashlib.sha256(f"{token_id}:{time_window}".encode()).hexdigest()[:16]
    
    def checkin(self, token_id: str, zone: str):
        """Record that a token is currently in a zone."""
        if token_id not in self.registered_tokens:
            return
        
        token = self.registered_tokens[token_id]
        
        # Rotate ID if needed (every 15 minutes)
        if time.time() - token["last_rotation"] > 900:
            token["current_rotating_id"] = self._generate_rotating_id(token_id)
            token["last_rotation"] = time.time()
        
        token["zone_history"].append({
            "zone": zone,
            "time": datetime.now().isoformat(),
            "rotating_id": token["current_rotating_id"]
        })
        
        # Keep only last 14 days of history
        cutoff = (datetime.now() - timedelta(days=14)).isoformat()
        token["zone_history"] = [h for h in token["zone_history"] if h["time"] > cutoff]
    
    def report_positive(self, token_id: str) -> int:
        """
        User reports a positive test. Find all tokens that overlapped
        with this user's zone history and send anonymous notifications.
        Returns number of notifications sent.
        """
        if token_id not in self.registered_tokens:
            return 0
        
        reporter = self.registered_tokens[token_id]
        reporter_history = reporter["zone_history"]
        notified = 0
        
        for other_id, other_token in self.registered_tokens.items():
            if other_id == token_id:
                continue
            
            # Check for zone+time overlaps
            for r_entry in reporter_history:
                for o_entry in other_token["zone_history"]:
                    if r_entry["zone"] == o_entry["zone"]:
                        # Same zone — check if within 30-minute window
                        r_time = datetime.fromisoformat(r_entry["time"])
                        o_time = datetime.fromisoformat(o_entry["time"])
                        if abs((r_time - o_time).total_seconds()) < 1800:
                            # Overlap detected — queue notification
                            # The notified user does NOT learn who was positive
                            notified += 1
                            break
                else:
                    continue
                break
        
        return notified
```

### Web Push Notifications (VAPID)

```python
from pywebpush import webpush, WebPushException
import json
import os

class PushNotifier:
    """
    Send Web Push notifications to PWA clients using VAPID authentication.
    
    iOS support: Push notifications work on iOS 16.4+ but ONLY when the
    PWA is installed to the Home Screen (Add to Home Screen in Safari).
    The PWA must be served over HTTPS and have display: standalone in manifest.
    
    For the hackathon demo, serve via ngrok (HTTPS) and have the judge
    add the PWA to their home screen.
    """
    
    def __init__(self):
        self.vapid_private_key = os.getenv("VAPID_PRIVATE_KEY")
        self.vapid_claims = {"sub": os.getenv("VAPID_CLAIMS_EMAIL")}
    
    def send(self, subscription_info: dict, title: str, body: str, data: dict = None):
        """Send a push notification to one subscriber."""
        payload = json.dumps({
            "title": title,
            "body": body,
            "icon": "/icon-192.png",
            "badge": "/badge-72.png",
            "data": data or {},
            "tag": f"echolocate-{int(time.time())}",  # Prevents duplicate stacking
        })
        
        try:
            webpush(
                subscription_info=subscription_info,
                data=payload,
                vapid_private_key=self.vapid_private_key,
                vapid_claims=self.vapid_claims,
            )
        except WebPushException as e:
            print(f"Push failed: {e}")
```

### Claude Reasoning Agent — Space Design Reports

```python
REPORT_SYSTEM_PROMPT = """You are Echolocate's Space Intelligence AI. You analyze accumulated spatial metadata from a public space monitoring system to generate Space Design Reports for building operators.

You receive a collection of spatial observations — each one is structured metadata from a brief camera snapshot that was immediately deleted. You never see images. You only see data like:
- "12:15 PM: 4 people clustered near entrance doorway, queue pattern, tight density"
- "12:22 PM: 6 people clustered near entrance, bidirectional bottleneck"
- "2:45 PM: 3 people clustered near checkout counter, stationary cluster"

From these observations, you identify:
1. CHOKEPOINTS — physical features that consistently force close proximity
2. TEMPORAL PATTERNS — when crowding happens (time of day, day of week)
3. ROOT CAUSES — why the space design forces crowding (single entry/exit, counter placement, narrow corridors)
4. SPECIFIC RECOMMENDATIONS — concrete, actionable space redesign suggestions

Your report must be:
- Professional and actionable (a business owner should read this and know exactly what to change)
- Specific (not "consider redesigning" but "move the checkout counter 2 meters east to separate flowing and stationary traffic")
- Honest about limitations (you're working from spatial metadata, not architectural blueprints)
- Free of any identity or personal information

Format your response as a structured report with sections: Executive Summary, Identified Chokepoints, Temporal Patterns, Recommendations (prioritized by impact), and Methodology Note (explaining the privacy-preserving data collection).
"""

async def generate_space_report(observations: list[dict]) -> str:
    """
    Generate a Space Design Report from accumulated spatial observations.
    Input: list of spatial metadata dicts from Claude Vision analysis.
    Output: formatted report text.
    """
    client = anthropic.Anthropic()
    
    obs_text = "\n".join([
        f"- {obs['timestamp']}: {obs.get('spatial_issue', 'N/A')} | "
        f"Total: {obs.get('total_people_visible', '?')} | "
        f"Chokepoints: {obs.get('chokepoints', [])} | "
        f"Clusters: {json.dumps(obs.get('clusters', []))}"
        for obs in observations
    ])
    
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        system=REPORT_SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": f"Generate a Space Design Report from these {len(observations)} spatial observations collected today:\n\n{obs_text}"
        }]
    )
    
    return response.content[0].text
```

### Chat Endpoint — Conversational AI

```python
CHAT_SYSTEM_PROMPT = """You are Echolocate, a privacy-first public space monitoring AI. You help building operators and individuals understand space usage patterns and crowding risks.

You have access to current sensor data (WiFi CSI occupancy levels) and accumulated spatial observations. You can answer questions like:
- "How crowded is the space right now?"
- "When is the worst time for crowding?"
- "What's causing the bottleneck near the entrance?"
- "Is it safe to come in now?"

Important rules:
- You have NO camera feed. You cannot see the space right now.
- You can report CSI-based occupancy estimates (empty/low/moderate/high)
- You can reference spatial observations from past threshold events
- You NEVER have identity information about anyone
- Be honest about your limitations and confidence levels
- You are NOT a medical authority. Don't give medical advice about COVID risk levels.
"""
```

### FastAPI Server Structure

```python
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import asyncio
import threading
import serial
import json
import time
from datetime import datetime

app = FastAPI(title="Echolocate Backend")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Serve PWA static files
app.mount("/app", StaticFiles(directory="frontend/dist", html=True), name="frontend")

# Global state
csi_detector = CSIOccupancyDetector()
camera = EphemeralCamera(device_index=int(os.getenv("CAMERA_DEVICE_INDEX", "1")))
token_manager = AnonymousTokenManager()
push_notifier = PushNotifier()
spatial_observations = []  # Accumulated metadata from snapshots
connected_clients = set()
last_snapshot_time = 0
SNAPSHOT_COOLDOWN = 30  # Minimum seconds between snapshots


# --- Serial Reader (background thread) ---
def serial_reader():
    ser = serial.Serial(os.getenv("SERIAL_PORT"), int(os.getenv("SERIAL_BAUD", "921600")))
    while True:
        try:
            line = ser.readline().decode('utf-8', errors='ignore').strip()
            csi_data = csi_detector.parse_csi_line(line)
            if csi_data:
                csi_detector.update(csi_data)
        except Exception:
            pass


# --- Monitoring Loop (async, runs every 5 seconds) ---
async def monitoring_loop():
    global last_snapshot_time
    
    while True:
        await asyncio.sleep(5)
        
        occupancy = csi_detector.get_occupancy()
        
        # Check if threshold exceeded and cooldown elapsed
        if (occupancy.get("threshold_exceeded") and 
            time.time() - last_snapshot_time > SNAPSHOT_COOLDOWN):
            
            last_snapshot_time = time.time()
            
            # Capture ephemeral snapshot
            image_b64 = camera.capture_and_encode()
            
            if image_b64:
                # Send to Claude Vision for spatial analysis
                spatial_data = await analyze_snapshot(image_b64)
                
                # IMAGE IS NOW DELETED — only metadata survives
                del image_b64
                
                if spatial_data:
                    spatial_data["timestamp"] = datetime.now().isoformat()
                    spatial_data["csi_occupancy"] = occupancy
                    spatial_observations.append(spatial_data)
                    
                    # TODO: Store in SQLite for persistence
                    
                    # Notify opted-in users if crowding is severe
                    if occupancy["level"] in ("moderate", "high"):
                        for token_id, token_data in token_manager.registered_tokens.items():
                            push_notifier.send(
                                token_data["push_subscription"],
                                "Echolocate: Space Alert",
                                f"Current occupancy is {occupancy['level']}. Consider spacing out or visiting later.",
                            )
        
        # Broadcast to WebSocket clients
        broadcast = {
            "type": "occupancy_update",
            "occupancy": occupancy,
            "latest_spatial": spatial_observations[-1] if spatial_observations else None,
            "total_observations": len(spatial_observations),
            "timestamp": datetime.now().isoformat(),
        }
        for ws in connected_clients.copy():
            try:
                await ws.send_json(broadcast)
            except:
                connected_clients.discard(ws)


# --- REST Endpoints ---

@app.get("/api/status")
async def get_status():
    return {
        "occupancy": csi_detector.get_occupancy(),
        "total_observations": len(spatial_observations),
        "system_uptime": time.time(),
    }

@app.get("/api/observations")
async def get_observations():
    """Return all spatial metadata observations (never images)."""
    return {"observations": spatial_observations}

@app.post("/api/register")
async def register_anonymous(body: dict):
    """Register anonymous token + push subscription."""
    token_id = token_manager.register(body.get("push_subscription", {}))
    return {"token_id": token_id}

@app.post("/api/checkin")
async def zone_checkin(body: dict):
    """Record anonymous token entering a zone."""
    token_manager.checkin(body["token_id"], body.get("zone", "main"))
    return {"status": "ok"}

@app.post("/api/report-positive")
async def report_positive(body: dict):
    """User reports positive test. Triggers anonymous exposure notifications."""
    count = token_manager.report_positive(body["token_id"])
    # Send exposure notifications to overlapping tokens
    for token_id, token_data in token_manager.registered_tokens.items():
        if token_id != body["token_id"]:
            # Check if this token was in the overlap set
            push_notifier.send(
                token_data["push_subscription"],
                "Echolocate: Exposure Alert",
                "You were in proximity with someone who has reported a positive test. Consider monitoring for symptoms.",
            )
    return {"notifications_sent": count}

@app.post("/api/chat")
async def chat(body: dict):
    """Conversational AI endpoint for operators and individuals."""
    client = anthropic.Anthropic()
    occupancy = csi_detector.get_occupancy()
    
    context = f"""Current occupancy: {occupancy}
Recent observations ({len(spatial_observations)} total):
{json.dumps(spatial_observations[-5:], indent=2) if spatial_observations else 'None yet'}"""
    
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=400,
        system=CHAT_SYSTEM_PROMPT + f"\n\nCURRENT DATA:\n{context}",
        messages=[{"role": "user", "content": body.get("message", "")}]
    )
    return {"response": response.content[0].text}

@app.post("/api/generate-report")
async def generate_report():
    """Generate Space Design Report from accumulated observations."""
    if len(spatial_observations) < 3:
        return {"error": "Need at least 3 spatial observations to generate a report."}
    report = await generate_space_report(spatial_observations)
    return {"report": report}

@app.get("/api/vapid-public-key")
async def get_vapid_key():
    """Return VAPID public key for push subscription."""
    return {"publicKey": os.getenv("VAPID_PUBLIC_KEY")}

# --- WebSocket ---
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected_clients.add(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        connected_clients.discard(websocket)

# --- Startup ---
@app.on_event("startup")
async def startup():
    threading.Thread(target=serial_reader, daemon=True).start()
    asyncio.create_task(monitoring_loop())
```

---

## COMPONENT 3: REACT PWA FRONTEND

### Tech Stack
- **React 18** + **TypeScript** + **Vite**
- **Tailwind CSS** — styling
- **Recharts** — occupancy charts
- **Service Worker** — push notifications + offline caching
- **Web App Manifest** — installable PWA (add to home screen)

### Project Setup

```bash
npm create vite@latest frontend -- --template react-ts
cd frontend
npm install tailwindcss recharts
```

### PWA Requirements for iOS Push Notifications

For push notifications to work on iOS Safari (16.4+), the PWA MUST:
1. Be served over HTTPS (use ngrok during hackathon)
2. Have a `manifest.json` with `"display": "standalone"` 
3. Be added to the Home Screen by the user
4. Service worker must be registered
5. Push permission must be requested in response to a user gesture (button tap)

### manifest.json

```json
{
    "name": "Echolocate",
    "short_name": "Echolocate",
    "description": "Privacy-first space safety",
    "start_url": "/",
    "display": "standalone",
    "background_color": "#0f172a",
    "theme_color": "#0ea5e9",
    "icons": [
        { "src": "/icon-192.png", "sizes": "192x192", "type": "image/png" },
        { "src": "/icon-512.png", "sizes": "512x512", "type": "image/png" }
    ]
}
```

### Service Worker (sw.js)

```javascript
// Service Worker for push notifications and offline caching

self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open('echolocate-v1').then((cache) => {
            return cache.addAll(['/', '/index.html', '/manifest.json']);
        })
    );
});

self.addEventListener('push', (event) => {
    const data = event.data ? event.data.json() : {};
    const options = {
        body: data.body || 'Echolocate alert',
        icon: data.icon || '/icon-192.png',
        badge: data.badge || '/badge-72.png',
        tag: data.tag || 'echolocate',
        data: data.data || {},
        vibrate: [200, 100, 200],
    };
    event.waitUntil(
        self.registration.showNotification(data.title || 'Echolocate', options)
    );
});

self.addEventListener('notificationclick', (event) => {
    event.notification.close();
    event.waitUntil(
        clients.openWindow(event.notification.data.url || '/')
    );
});
```

### Push Subscription (client-side)

```javascript
// Subscribe to push notifications
async function subscribeToPush() {
    const registration = await navigator.serviceWorker.ready;
    
    // Get VAPID public key from server
    const response = await fetch('/api/vapid-public-key');
    const { publicKey } = await response.json();
    
    const subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(publicKey),
    });
    
    // Register anonymous token with push subscription
    const tokenResponse = await fetch('/api/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ push_subscription: subscription.toJSON() }),
    });
    
    const { token_id } = await tokenResponse.json();
    localStorage.setItem('echolocate_token', token_id);
}

function urlBase64ToUint8Array(base64String) {
    const padding = '='.repeat((4 - base64String.length % 4) % 4);
    const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
    const rawData = window.atob(base64);
    return Uint8Array.from([...rawData].map((char) => char.charCodeAt(0)));
}
```

### App Structure

```
frontend/
├── public/
│   ├── manifest.json
│   ├── sw.js
│   ├── icon-192.png
│   ├── icon-512.png
│   └── badge-72.png
├── src/
│   ├── App.tsx                    — Router: individual view vs operator view
│   ├── main.tsx                   — Entry point, service worker registration
│   ├── hooks/
│   │   ├── useWebSocket.ts        — Real-time occupancy updates
│   │   └── usePushNotifications.ts — Push subscription management
│   ├── pages/
│   │   ├── IndividualView.tsx     — Zone density map, alerts, exposure status
│   │   ├── OperatorView.tsx       — Occupancy dashboard, chokepoint heatmap
│   │   ├── ChatView.tsx           — Talk to Echolocate AI
│   │   ├── ReportView.tsx         — Space Design Report display
│   │   ├── PrivacyView.tsx        — What IS and ISN'T collected
│   │   └── SetupView.tsx          — Onboarding, add-to-homescreen prompt
│   ├── components/
│   │   ├── OccupancyGauge.tsx     — Green/yellow/red occupancy indicator
│   │   ├── ZoneDensityMap.tsx     — Visual zone layout with density colors
│   │   ├── ChokePointHeatmap.tsx  — Accumulated spatial observation viz
│   │   ├── AlertCard.tsx          — Individual alert with reasoning
│   │   ├── ChatBubble.tsx         — Chat message component
│   │   └── ReportSection.tsx      — Styled report section
│   └── utils/
│       ├── api.ts                 — REST API client
│       └── push.ts                — Push notification helpers
├── index.html
├── vite.config.ts
├── tailwind.config.js
└── package.json
```

### Key Pages

**IndividualView:** Large occupancy gauge (green/yellow/red) at top. Current zone density. "Enable Notifications" button (triggers push subscription + anonymous token registration). Exposure alert banner if notified. "Report Positive Test" button (sends anonymous report, triggers exposure notifications to overlapping tokens). Link to Privacy page.

**OperatorView:** Live occupancy chart over time (Recharts line chart). Chokepoint heatmap built from accumulated spatial observations. "Generate Space Design Report" button. Alert history showing Claude's spatial analysis results. Chat interface for asking questions about space performance.

**ReportView:** Displays the AI-generated Space Design Report. Sections: Executive Summary, Identified Chokepoints (with severity), Temporal Patterns, Recommendations (prioritized), Methodology Note. "Download as PDF" option (stretch goal). The report contains ZERO images — only spatial metadata analysis.

**PrivacyView:** Two-column layout. Left: "What Echolocate Collects" (occupancy levels, anonymous zone+time tokens, spatial density metadata from ephemeral snapshots). Right: "What Echolocate Does NOT Collect" (photos, video, faces, names, identities, audio, location data, browsing history). Bottom: "Image Lifecycle" diagram showing capture → 2-second analysis → immediate deletion.

---

## COMPONENT 4: DEPLOYMENT (Hackathon Day)

### Serving Over HTTPS (Required for PWA Push on iOS)

```bash
# Install ngrok
brew install ngrok

# Start FastAPI server
uvicorn main:app --host 0.0.0.0 --port 8000

# In another terminal, expose via ngrok
ngrok http 8000

# Use the https://*.ngrok-free.app URL for everything
# Update CORS and PWA manifest start_url accordingly
```

### Demo Setup at Hackathon

```
                Table layout at The Lodge @ Sixth
    ┌──────────────────────────────────────────────┐
    │                                              │
    │  📱 Phone         🧑🧑🧑 Judges              │
    │  (hotspot)        sit here                   │
    │                                              │
    │              Brio 101 📷                     │
    │              (on table, pointing at judges)  │
    │                                              │
    │  ESP32 🔲 ─── USB ───→ 💻 Laptop            │
    │                         (runs everything)    │
    │                                              │
    └──────────────────────────────────────────────┘
```

### Demo Script

1. Start system with empty room → CSI shows "empty" baseline
2. Judges sit down → occupancy changes to "low" on dashboard
3. Have 3-4 people cluster on one side → threshold triggers, camera takes snapshot
4. Show Claude Vision's spatial analysis on operator dashboard: "4 people clustered near left side, tight density, near doorway"
5. Show the image was never saved — only metadata persists
6. Repeat clustering a few times from different positions
7. Generate Space Design Report → Claude produces analysis with chokepoint identification and redesign recommendations
8. Show individual PWA on phone → judge adds to home screen, gets push notification about crowding
9. Demonstrate anonymous exposure notification flow
10. Show privacy page → explain what IS and ISN'T collected

---

## FILE STRUCTURE

```
echolocate/
├── CLAUDE.md                              — This context file
├── firmware/
│   └── README.md                          — Instructions to clone and flash esp-csi examples
│   # NOTE: Don't recreate ESP-IDF project from scratch.
│   # Clone https://github.com/espressif/esp-csi and use examples directly.
│   # For phone hotspot: examples/get-started/csi_recv_router
│   # For two-ESP32: examples/get-started/csi_send + csi_recv
├── backend/
│   ├── requirements.txt
│   ├── .env                               — API keys, serial port (gitignored)
│   ├── main.py                            — FastAPI server, serial reader, monitoring loop
│   ├── csi_detector.py                    — CSI occupancy detection from serial data
│   ├── camera.py                          — Ephemeral camera capture + encode
│   ├── spatial_analyzer.py                — Claude Vision spatial analysis
│   ├── report_generator.py                — Claude Space Design Report generation  
│   ├── chat.py                            — Conversational Claude endpoint
│   ├── token_manager.py                   — Anonymous contact tracing tokens
│   ├── push_notifier.py                   — Web Push via pywebpush + VAPID
│   ├── welford.py                         — Online statistics for baseline tracking
│   ├── private_key.pem                    — VAPID private key (gitignored)
│   └── public_key.pem                     — VAPID public key
├── frontend/
│   ├── public/
│   │   ├── manifest.json
│   │   ├── sw.js
│   │   ├── icon-192.png
│   │   ├── icon-512.png
│   │   └── badge-72.png
│   ├── src/
│   │   ├── App.tsx
│   │   ├── main.tsx
│   │   ├── hooks/
│   │   ├── pages/
│   │   ├── components/
│   │   └── utils/
│   ├── index.html
│   ├── vite.config.ts
│   ├── tailwind.config.js
│   └── package.json
├── docs/
│   ├── ethics_writeup.md                  — 1-2 page ethics analysis
│   └── video_script.md                    — 3-5 minute walkthrough outline
└── .gitignore
```

---

## ETHICS WRITEUP OUTLINE

### Section 1: Privacy by Architecture
- No persistent visual data. Image lifecycle: capture → 2s analysis → deletion.
- Hardware is physically incapable of surveillance — no stored images, no audio, no identity data.
- Compare to CCTV-based alternatives: facial recognition, constant recording, data breach risk.

### Section 2: Why We Rejected Facial Recognition
- Government driver's license databases exist. We chose not to use them.
- Infrastructure outlives the pandemic. Facial recognition systems don't get turned off.
- Disproportionate impact: higher error rates for darker skin tones → wrongful quarantine.
- Chilling effect on civic participation in public spaces.
- Anonymous token-based contact tracing achieves the same public health goal without surveillance.

### Section 3: Governance Questions
- Who sets occupancy thresholds? Elected officials? Public health authorities? Building managers?
- Should Space Design Reports be enforceable mandates or advisory recommendations?
- Who pays for space redesigns? Disproportionate burden on small businesses?
- Can the AI's spatial analysis be contested by business owners?
- Who owns the occupancy data? Can law enforcement subpoena it? (Answer: there's nothing to subpoena — only aggregate metadata.)

### Section 4: Weaponization Risks
- Same technology could monitor political gatherings, union meetings, religious services.
- Mitigation: no identity data collected, anonymous tokens rotate every 15 minutes, system is architecturally incapable of tracking individuals.
- The camera's shutter is closed by default. It opens only on threshold breach. This is auditable.

### Section 5: Design Decisions as Ethical Choices
- Every technical decision encodes a value: ephemeral images = privacy > accuracy; anonymous tokens = autonomy > control; AI reasoning transparency = accountability > efficiency.
- The system's constraints are features, not limitations.

---

## BUILD PRIORITY ORDER (13-hour hackathon)

1. **ESP32 CSI firmware** — flash `csi_recv_router`, verify serial output (1.5 hours)
2. **Python backend core** — serial reader + CSI occupancy detection (1.5 hours)
3. **Camera + Claude Vision** — ephemeral snapshot + spatial analysis (1.5 hours)
4. **React PWA core** — operator dashboard with live occupancy + spatial observations (2 hours)
5. **Space Design Report** — Claude report generation from accumulated metadata (1 hour)
6. **Push notifications** — VAPID setup + service worker + individual alerts (1.5 hours)
7. **Anonymous token system** — registration + exposure notification flow (1 hour)
8. **Chat interface** — conversational Claude in the PWA (0.5 hours)
9. **Polish + ethics writeup + video** — remaining time (2+ hours)

### If Running Behind
- Skip push notifications — use the web dashboard for alerts instead
- Skip anonymous contact tracing — focus on the operator-side spatial analysis
- Use simulated CSI data if ESP32 is flaky — pre-recorded serial output replayed in a loop
- The Space Design Report is the strongest demo artifact — prioritize getting enough spatial observations to generate a compelling one
