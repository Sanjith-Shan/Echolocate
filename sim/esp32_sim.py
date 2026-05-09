"""
Echolocate ESP32 simulator.

Mimics the real firmware closely enough that the backend cannot tell them apart:

  1. Serves CSI lines over a TCP socket (default 127.0.0.1:3333) in the same
     CSV format the real firmware emits, at a configurable sample rate.
  2. Runs an HTTP server (default :8088) with the same /health, /stats,
     /csi/latest, and / endpoints, so the same test scripts work against
     simulator and real hardware.

The synthetic CSI is plausible-looking, not physically accurate — a
baseline amplitude curve across 64 subcarriers with controllable Gaussian
noise on top. The "occupancy" knob just scales the noise variance:

    empty     → variance ≈ 1
    low       → variance ≈ 8
    moderate  → variance ≈ 35
    high      → variance ≈ 120

This is exactly the kind of signal the CSI occupancy detector keys off,
so a backend tuned against the simulator behaves correctly against the
real firmware (the absolute thresholds may shift, but the regime
boundaries are similar).

Usage:

    # Run sim with default settings, occupancy starts "empty"
    python -m sim.esp32_sim

    # With a scenario script that ramps occupancy over time
    python -m sim.esp32_sim --scenario surge

    # Lock to a specific level for testing
    python -m sim.esp32_sim --level moderate

    # Use a different TCP port (must match SERIAL_PORT=tcp://127.0.0.1:PORT)
    python -m sim.esp32_sim --tcp-port 3333 --http-port 8088

While running, the sim accepts simple stdin commands:
    empty | low | moderate | high     — switch occupancy level
    quit                              — shutdown
"""

from __future__ import annotations

import argparse
import json
import math
import random
import socket
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional


# ---------- Tunables ----------

SUBCARRIERS = 64        # HT20 LLTF — what the spec assumes
SAMPLE_RATE_HZ = 20     # 20 samples/sec — matches CSIOccupancyDetector default
BASE_AMPLITUDE = 28.0   # mean per-subcarrier amplitude when empty

# Real CSI from a populated room shows two kinds of variation:
#   1. Per-subcarrier independent noise (multipath jitter)
#   2. Correlated, sample-wide amplitude shifts as bodies move through the path
# We model both. The independent component drives subcarrier-shape variance;
# the correlated component drives the variance of the *mean amplitude across
# subcarriers* — which is what the occupancy detector actually keys off.
LEVEL_NOISE = {
    #            independent_std,   correlated_std   (added to all subcarriers each sample)
    # Empty rooms still have ambient WiFi multipath — never a perfectly flat
    # signal — so we model a non-trivial baseline. Subsequent levels then
    # produce ~2x, ~5x, ~12x variance ratios over baseline, which lands
    # cleanly in the empty/low/moderate/high bands of the detector.
    "empty":    (1.5,                2.0),
    "low":      (1.7,                2.5),
    "moderate": (3.0,                4.5),
    "high":     (5.0,                7.5),
}
# Backward-compat name for tests that import it
LEVEL_NOISE_STD = {k: v[0] for k, v in LEVEL_NOISE.items()}


# ---------- Shared state ----------

@dataclass
class SimState:
    level: str = "empty"
    seq: int = 0
    last_amplitudes: list[float] = field(default_factory=list)
    last_rssi: int = -45
    last_sample_us: int = 0
    packet_count: int = 0
    boot_time: float = field(default_factory=time.time)

    # Rolling stats (Welford) on mean amplitude — same trick the real firmware uses
    welford_n: int = 0
    welford_mean: float = 0.0
    welford_m2: float = 0.0

    lock: threading.Lock = field(default_factory=threading.Lock)

    def update_welford(self, x: float) -> None:
        self.welford_n += 1
        delta = x - self.welford_mean
        self.welford_mean += delta / self.welford_n
        delta2 = x - self.welford_mean
        self.welford_m2 += delta * delta2

    @property
    def variance(self) -> float:
        return self.welford_m2 / self.welford_n if self.welford_n > 1 else 0.0


STATE = SimState()


# ---------- Synthetic CSI generation ----------

def baseline_curve() -> list[float]:
    """Plausible CSI amplitude shape across subcarriers — a soft hump in the
    middle, attenuated edges. Stable across calls."""
    return [
        BASE_AMPLITUDE * (0.6 + 0.4 * math.sin(math.pi * (i + 0.5) / SUBCARRIERS))
        for i in range(SUBCARRIERS)
    ]


_BASELINE = baseline_curve()


def synth_iq_pairs(level: str, rng: random.Random) -> list[int]:
    """Generate 2*SUBCARRIERS int8 I/Q values whose recovered amplitudes have
    the noise variance matching the given occupancy level. Models both
    independent per-subcarrier noise and a correlated whole-frame shift to
    mimic real bodies moving through the WiFi path."""
    indep_std, corr_std = LEVEL_NOISE.get(level, (1.0, 0.5))
    correlated_shift = rng.gauss(0.0, corr_std)
    out: list[int] = []
    for amp_target in _BASELINE:
        amp = amp_target + correlated_shift + rng.gauss(0.0, indep_std)
        if amp < 0.5:
            amp = 0.5
        # Random phase, then split into I/Q.
        phase = rng.uniform(0, 2 * math.pi)
        I = amp * math.cos(phase)
        Q = amp * math.sin(phase)
        out.append(int(max(-127, min(127, round(Q)))))  # Q first per esp-csi format
        out.append(int(max(-127, min(127, round(I)))))  # then I
    return out


def csi_csv_line(seq: int, iq: list[int], rssi: int) -> str:
    """Format one CSI sample to match the esp-csi `csi_recv_router` CSV layout."""
    mac = "1a:00:00:00:00:00"
    rate = 11
    noise_floor = -96
    fft_gain = 32
    agc_gain = 4
    channel = 11
    local_ts = int(time.time() * 1_000_000) & 0xFFFFFFFF
    sig_len = 47
    rx_state = 0
    data_len = len(iq)
    first_word = 0  # we don't simulate the first-word-invalid quirk
    data_field = "[" + ",".join(str(v) for v in iq) + "]"
    return (
        f'CSI_DATA,{seq},{mac},{rssi},{rate},{noise_floor},{fft_gain},{agc_gain},'
        f'{channel},{local_ts},{sig_len},{rx_state},{data_len},{first_word},'
        f'"{data_field}"'
    )


# ---------- TCP server: streams CSI lines to backend ----------

class CSITCPServer:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.clients: list[socket.socket] = []
        self.clients_lock = threading.Lock()
        self.rng = random.Random()

    def start(self) -> None:
        threading.Thread(target=self._accept_loop, daemon=True).start()
        threading.Thread(target=self._emit_loop, daemon=True).start()

    def _accept_loop(self) -> None:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, self.port))
        srv.listen(8)
        print(f"[sim] CSI TCP server listening on {self.host}:{self.port}", flush=True)
        while True:
            conn, addr = srv.accept()
            print(f"[sim] backend connected from {addr}", flush=True)
            # Send CSV header so a tail can read schema
            try:
                conn.sendall(
                    b"CSI_HEADER,type,seq,mac,rssi,rate,noise_floor,fft_gain,"
                    b"agc_gain,channel,local_timestamp,sig_len,rx_state,len,"
                    b"first_word,data\n"
                )
            except OSError:
                conn.close()
                continue
            with self.clients_lock:
                self.clients.append(conn)

    def _emit_loop(self) -> None:
        period = 1.0 / SAMPLE_RATE_HZ
        next_t = time.monotonic()
        while True:
            now = time.monotonic()
            if now < next_t:
                time.sleep(next_t - now)
            next_t += period

            iq = synth_iq_pairs(STATE.level, self.rng)
            rssi = -45 + self.rng.randint(-3, 3)

            with STATE.lock:
                STATE.seq += 1
                STATE.packet_count += 1
                STATE.last_rssi = rssi
                STATE.last_sample_us = int(time.time() * 1_000_000)
                # Recover amplitudes for HTTP /csi/latest
                amps = []
                for i in range(0, len(iq), 2):
                    Q, I = iq[i], iq[i + 1]
                    amps.append(math.sqrt(I * I + Q * Q))
                STATE.last_amplitudes = amps
                mean_amp = sum(amps) / len(amps)
                STATE.update_welford(mean_amp)
                seq = STATE.seq

            line = csi_csv_line(seq, iq, rssi) + "\n"
            blob = line.encode("ascii")

            with self.clients_lock:
                dead = []
                for c in self.clients:
                    try:
                        c.sendall(blob)
                    except OSError:
                        dead.append(c)
                for c in dead:
                    self.clients.remove(c)
                    try:
                        c.close()
                    except OSError:
                        pass


# ---------- HTTP server: same shape as real firmware ----------

class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # silence default access log
        return

    def _json(self, body: dict) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _html(self, body: str) -> None:
        payload = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:
        """Test-only control endpoint: POST /control {"level": "high"} to switch."""
        if self.path != "/control":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            return
        new_level = data.get("level")
        if new_level not in LEVEL_NOISE:
            self._json({"ok": False, "error": f"unknown level: {new_level}"})
            return
        with STATE.lock:
            STATE.level = new_level
        self._json({"ok": True, "level": new_level})

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]

        with STATE.lock:
            packets = STATE.packet_count
            level = STATE.level
            rssi = STATE.last_rssi
            mean = STATE.welford_mean
            var = STATE.variance
            n_amps = len(STATE.last_amplitudes)
            amps = list(STATE.last_amplitudes)
            sample_us = STATE.last_sample_us
            uptime = int(time.time() - STATE.boot_time)
            welford_n = STATE.welford_n

        if path == "/health":
            self._json({
                "ok": True,
                "firmware": "echolocate-csi-sim-1.1",
                "chip": "simulator",
                "ssid": "<sim>",
                "ip": "127.0.0.1",
                "rssi": rssi,
                "uptime_s": uptime,
                "free_heap": -1,
                "packets_received": packets,
                "ping_replies": packets,  # 1:1 with samples in sim — see firmware README
                "simulated_level": level,
            })
        elif path == "/stats":
            std = math.sqrt(var)
            if welford_n > 200:
                if   var < 5.0:    hint = "empty"
                elif var < 20.0:   hint = "low"
                elif var < 80.0:   hint = "moderate"
                else:              hint = "high"
            else:
                hint = "calibrating"
            self._json({
                "samples": welford_n,
                "subcarriers_per_sample": n_amps,
                "rolling_mean_amplitude": round(mean, 4),
                "rolling_variance": round(var, 4),
                "rolling_std": round(std, 4),
                "occupancy_hint": hint,
                "simulated_level": level,
            })
        elif path == "/csi/latest":
            self._json({
                "sample_us": sample_us,
                "rssi": rssi,
                "n": n_amps,
                "amplitudes": [round(a, 2) for a in amps],
                "simulated_level": level,
            })
        elif path == "/":
            self._html(f"""<!DOCTYPE html><html><head><meta charset=utf-8>
<title>Echolocate ESP32 (simulator)</title>
<meta http-equiv=refresh content=2>
<style>body{{font-family:system-ui;margin:2em;background:#0f172a;color:#e2e8f0}}
h1{{color:#a78bfa}}.tag{{background:#7c3aed;padding:.1em .5em;border-radius:3px;font-size:.7em;vertical-align:middle}}
code{{background:#1e293b;padding:.1em .3em;border-radius:3px}}
table{{border-collapse:collapse}}td{{padding:.3em 1em;border-bottom:1px solid #334155}}</style>
</head><body>
<h1>Echolocate CSI Sensor <span class=tag>SIMULATOR</span></h1>
<p>Sim is alive. Page auto-refreshes every 2s.</p>
<table>
<tr><td>Simulated occupancy level</td><td><code>{level}</code></td></tr>
<tr><td>RSSI (synthetic)</td><td>{rssi} dBm</td></tr>
<tr><td>Uptime</td><td>{uptime} s</td></tr>
<tr><td>CSI packets emitted</td><td>{packets}</td></tr>
<tr><td>Subcarriers / sample</td><td>{n_amps}</td></tr>
<tr><td>Rolling mean amplitude</td><td>{mean:.2f}</td></tr>
<tr><td>Rolling variance</td><td>{var:.2f}</td></tr>
</table>
<h3>Test endpoints</h3>
<ul>
<li><a style=color:#a78bfa href=/health>/health</a> · <a style=color:#a78bfa href=/stats>/stats</a> · <a style=color:#a78bfa href=/csi/latest>/csi/latest</a></li>
</ul>
</body></html>""")
        else:
            self.send_response(404)
            self.end_headers()


def start_http_server(host: str, port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"[sim] HTTP server listening on http://{host}:{port}", flush=True)
    return server


# ---------- Scenarios ----------

def scenario_thread(name: str) -> None:
    """Run a named scenario by mutating STATE.level over time."""
    if name == "surge":
        sequence = [("empty", 15), ("low", 15), ("moderate", 20), ("high", 20),
                    ("moderate", 15), ("low", 15), ("empty", 0)]
    elif name == "queue":
        sequence = [("empty", 10), ("low", 5), ("moderate", 30), ("high", 5),
                    ("moderate", 10), ("low", 10), ("empty", 0)]
    elif name == "ramp":
        sequence = [("empty", 30), ("low", 30), ("moderate", 30), ("high", 30),
                    ("moderate", 30), ("low", 30), ("empty", 0)]
    else:
        return
    print(f"[sim] starting scenario '{name}'", flush=True)
    for level, dur in sequence:
        with STATE.lock:
            STATE.level = level
        print(f"[sim] level → {level} (for {dur}s)", flush=True)
        if dur:
            time.sleep(dur)


# ---------- Main ----------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--tcp-host", default="127.0.0.1")
    p.add_argument("--tcp-port", type=int, default=3333)
    p.add_argument("--http-host", default="127.0.0.1")
    p.add_argument("--http-port", type=int, default=8088)
    p.add_argument("--level", default="empty",
                   choices=list(LEVEL_NOISE_STD.keys()),
                   help="Initial occupancy level")
    p.add_argument("--scenario", choices=["surge", "queue", "ramp"],
                   help="Run a predefined occupancy scenario")
    p.add_argument("--no-stdin", action="store_true",
                   help="Don't read stdin commands (useful for tests)")
    args = p.parse_args()

    STATE.level = args.level

    tcp = CSITCPServer(args.tcp_host, args.tcp_port)
    tcp.start()
    start_http_server(args.http_host, args.http_port)

    if args.scenario:
        threading.Thread(target=scenario_thread, args=(args.scenario,), daemon=True).start()

    print(f"[sim] ready. level={STATE.level}", flush=True)
    print("[sim] commands: empty | low | moderate | high | quit", flush=True)

    if args.no_stdin:
        # Just block forever
        while True:
            time.sleep(60)

    try:
        for line in sys.stdin:
            cmd = line.strip().lower()
            if cmd in LEVEL_NOISE_STD:
                with STATE.lock:
                    STATE.level = cmd
                print(f"[sim] level → {cmd}", flush=True)
            elif cmd == "quit" or cmd == "exit":
                return 0
            elif cmd:
                print(f"[sim] unknown command: {cmd}", flush=True)
    except (KeyboardInterrupt, EOFError):
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
