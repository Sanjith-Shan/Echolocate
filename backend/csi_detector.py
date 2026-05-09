"""
CSI parsing + occupancy detection.

Reads CSV lines from the ESP32 (real or simulated), recovers per-subcarrier
amplitudes from I/Q pairs, and classifies the room into one of:
calibrating / empty / low / moderate / high.

Threshold logic is *ratio-based* (current windowed variance / baseline
variance from calibration) so it's robust to environment-specific magnitudes.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Optional

import numpy as np

from .welford import WelfordStats


class CSIOccupancyDetector:
    """Estimates occupancy LEVEL (not exact count) from CSI variance."""

    # Match the simulator and real-firmware sample rate
    DEFAULT_SAMPLE_RATE = 20

    def __init__(
        self,
        window_seconds: int = 5,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        calibration_seconds: int = 10,
        min_baseline_variance: float = 0.1,
    ):
        self.sample_rate = sample_rate
        self.window_size = sample_rate * window_seconds
        self.amplitude_buffer: deque[float] = deque(maxlen=self.window_size)
        self.baseline_stats = WelfordStats()
        self.calibration_phase = True
        self.calibration_samples = 0
        self.CALIBRATION_DURATION = sample_rate * calibration_seconds
        self.min_baseline_variance = min_baseline_variance
        self.last_seq: Optional[int] = None
        self.dropped_packets = 0

    # ---------- Parsing ----------

    @staticmethod
    def parse_csi_line(csv_line: str) -> Optional[dict]:
        """Parse one CSV line. Returns dict with amplitudes, rssi, mean_amplitude,
        timestamp, seq — or None if the line isn't a CSI_DATA row.

        Format expected:
          CSI_DATA,seq,mac,rssi,rate,noise_floor,fft_gain,agc_gain,channel,
          local_timestamp,sig_len,rx_state,len,first_word,"[I,Q,I,Q,...]"
        """
        if not csv_line:
            return None
        line = csv_line.strip()
        if not line.startswith("CSI_DATA"):
            return None

        try:
            # The data array is the last field. Find the bracketed segment
            # between the last `"[` and `]"` to be tolerant of extra commas.
            br_start = line.rfind('"[')
            br_end = line.rfind(']"')
            if br_start < 0 or br_end < 0 or br_end <= br_start:
                return None
            data_str = line[br_start + 2 : br_end]
            # Header fields are everything before the data field
            header = line[: br_start].rstrip(",")
            fields = header.split(",")
            if len(fields) < 14:
                return None

            seq = int(fields[1])
            rssi = int(fields[3])
            first_word_invalid = int(fields[13]) if len(fields) > 13 else 0

            iq_values = [int(x) for x in data_str.split(",") if x.strip()]
            if len(iq_values) < 4 or len(iq_values) % 2 != 0:
                return None

            iq = np.asarray(iq_values, dtype=np.float32).reshape(-1, 2)
            amplitudes = np.sqrt(iq[:, 0] ** 2 + iq[:, 1] ** 2)

            # Skip first 2 subcarriers (= 4 bytes) if first_word_invalid quirk
            if first_word_invalid:
                amplitudes = amplitudes[2:]

            return {
                "seq": seq,
                "rssi": rssi,
                "amplitudes": amplitudes,
                "mean_amplitude": float(np.mean(amplitudes)),
                "n_subcarriers": int(len(amplitudes)),
                "timestamp": time.time(),
            }
        except (ValueError, IndexError):
            return None

    # ---------- Update ----------

    def recalibrate(self) -> None:
        """Reset baseline & calibration phase. Call on operator request, e.g.
        after the room is verified empty post-deployment."""
        self.baseline_stats = WelfordStats()
        self.calibration_phase = True
        self.calibration_samples = 0
        self.amplitude_buffer.clear()

    def update(self, csi_data: dict) -> None:
        mean_amp = csi_data["mean_amplitude"]
        seq = csi_data.get("seq")

        # Track packet drops (informational only)
        if self.last_seq is not None and seq is not None and seq > self.last_seq + 1:
            self.dropped_packets += seq - self.last_seq - 1
        self.last_seq = seq

        self.amplitude_buffer.append(mean_amp)

        if self.calibration_phase:
            self.baseline_stats.update(mean_amp)
            self.calibration_samples += 1
            if self.calibration_samples >= self.CALIBRATION_DURATION:
                self.calibration_phase = False

    # ---------- Classification ----------

    def get_occupancy(self) -> dict:
        """Estimate current occupancy level from windowed CSI variance."""
        # Need at least 2 seconds of samples to compute a meaningful variance
        if len(self.amplitude_buffer) < self.sample_rate * 2:
            return self._calibrating()
        if self.calibration_phase:
            return self._calibrating()

        # Variance over the most recent 3 seconds
        recent = np.asarray(list(self.amplitude_buffer)[-self.sample_rate * 3 :])
        variance = float(np.var(recent))

        baseline_var = max(self.baseline_stats.variance, self.min_baseline_variance)
        ratio = variance / baseline_var

        # Classification thresholds (ratios). Calibrated against the simulator
        # but expected to generalize; per-deployment recalibration is fine.
        if ratio < 1.5:
            level, count_est = "empty", 0
        elif ratio < 3.0:
            level, count_est = "low", 2
        elif ratio < 6.0:
            level, count_est = "moderate", 4
        else:
            level, count_est = "high", 7

        return {
            "level": level,
            "count_estimate": count_est,
            "variance": round(variance, 4),
            "variance_ratio": round(ratio, 2),
            "baseline_variance": round(baseline_var, 4),
            "confidence": min(1.0, len(self.amplitude_buffer) / self.window_size),
            "threshold_exceeded": level in ("moderate", "high"),
            "calibration_phase": False,
            "dropped_packets": self.dropped_packets,
        }

    def _calibrating(self) -> dict:
        progress = (
            min(1.0, self.calibration_samples / self.CALIBRATION_DURATION)
            if self.CALIBRATION_DURATION
            else 1.0
        )
        return {
            "level": "calibrating",
            "count_estimate": 0,
            "variance": 0.0,
            "variance_ratio": 0.0,
            "baseline_variance": 0.0,
            "confidence": 0.0,
            "threshold_exceeded": False,
            "calibration_phase": True,
            "calibration_progress": round(progress, 2),
            "dropped_packets": self.dropped_packets,
        }
