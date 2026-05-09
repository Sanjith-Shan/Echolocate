"""End-to-end CSI → classification: drive synthetic data and assert behaviour."""

import random

from backend.csi_detector import CSIOccupancyDetector
from sim.esp32_sim import csi_csv_line, synth_iq_pairs


def _drive(detector: CSIOccupancyDetector, level: str, n_samples: int, rng: random.Random) -> None:
    for i in range(n_samples):
        iq = synth_iq_pairs(level, rng)
        line = csi_csv_line(seq=i, iq=iq, rssi=-45)
        parsed = CSIOccupancyDetector.parse_csi_line(line)
        assert parsed is not None
        detector.update(parsed)


def test_calibration_period_returns_calibrating():
    rng = random.Random(0)
    d = CSIOccupancyDetector(window_seconds=5, sample_rate=20, calibration_seconds=10)
    _drive(d, "empty", 50, rng)  # only 2.5s — well under calibration
    occ = d.get_occupancy()
    assert occ["level"] == "calibrating"
    assert occ["calibration_phase"] is True


def test_empty_baseline_then_moderate_triggers_threshold():
    """Calibrate on empty, then switch to moderate — detector must escalate."""
    rng = random.Random(1)
    d = CSIOccupancyDetector(window_seconds=5, sample_rate=20, calibration_seconds=10)

    # 10s of empty calibration
    _drive(d, "empty", 200, rng)
    assert not d.calibration_phase

    # Now feed 5s of moderate
    _drive(d, "moderate", 100, rng)
    occ = d.get_occupancy()
    assert occ["level"] in ("moderate", "high"), f"expected moderate/high, got {occ}"
    assert occ["threshold_exceeded"] is True


def test_empty_baseline_then_high_classifies_high():
    rng = random.Random(2)
    d = CSIOccupancyDetector(window_seconds=5, sample_rate=20, calibration_seconds=10)
    _drive(d, "empty", 200, rng)
    _drive(d, "high", 100, rng)
    occ = d.get_occupancy()
    assert occ["level"] == "high", f"expected high, got {occ}"


def test_empty_baseline_stays_empty():
    rng = random.Random(3)
    d = CSIOccupancyDetector(window_seconds=5, sample_rate=20, calibration_seconds=10)
    _drive(d, "empty", 200, rng)
    _drive(d, "empty", 100, rng)
    occ = d.get_occupancy()
    assert occ["level"] == "empty", f"expected empty, got {occ}"
    assert occ["threshold_exceeded"] is False


def test_low_does_not_trigger_threshold_on_empty_baseline():
    """`low` is below the moderate threshold; it should classify as low or
    empty but never as `threshold_exceeded` (which only fires for moderate+)."""
    rng = random.Random(4)
    d = CSIOccupancyDetector(window_seconds=5, sample_rate=20, calibration_seconds=10)
    _drive(d, "empty", 200, rng)
    _drive(d, "low", 100, rng)
    occ = d.get_occupancy()
    assert occ["level"] in ("empty", "low"), f"expected empty/low, got {occ}"
    assert occ["threshold_exceeded"] is False


def test_recalibrate_resets_baseline():
    """After recalibrate(), the detector goes back to calibrating phase and
    a fresh baseline is built from subsequent samples."""
    rng = random.Random(8)
    d = CSIOccupancyDetector(window_seconds=5, sample_rate=20, calibration_seconds=10)
    _drive(d, "empty", 200, rng)
    assert not d.calibration_phase

    d.recalibrate()
    assert d.calibration_phase is True
    occ = d.get_occupancy()
    assert occ["level"] == "calibrating"

    # Now with high baseline (we feed moderate during calibration)
    _drive(d, "moderate", 200, rng)
    assert not d.calibration_phase
    # Subsequent moderate readings shouldn't trip threshold because moderate is now baseline
    _drive(d, "moderate", 100, rng)
    occ = d.get_occupancy()
    assert occ["level"] == "empty", f"after recalibrating on moderate, moderate should look like baseline, got {occ}"


def test_dropped_packets_counted():
    rng = random.Random(5)
    d = CSIOccupancyDetector(sample_rate=20, calibration_seconds=1)
    # Drive seq=1, then seq=4 (skip 2 and 3)
    for seq in (1, 4):
        iq = synth_iq_pairs("empty", rng)
        line = csi_csv_line(seq=seq, iq=iq, rssi=-45)
        parsed = CSIOccupancyDetector.parse_csi_line(line)
        d.update(parsed)
    occ = d.get_occupancy()
    assert occ["dropped_packets"] == 2
