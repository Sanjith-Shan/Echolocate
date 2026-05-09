"""CSI line parsing — happy path and edge cases."""

import random

from backend.csi_detector import CSIOccupancyDetector
from sim.esp32_sim import csi_csv_line, synth_iq_pairs


def test_round_trip_simulator_to_parser():
    rng = random.Random(0)
    iq = synth_iq_pairs("moderate", rng)
    line = csi_csv_line(seq=42, iq=iq, rssi=-40)

    parsed = CSIOccupancyDetector.parse_csi_line(line)
    assert parsed is not None
    assert parsed["seq"] == 42
    assert parsed["rssi"] == -40
    assert parsed["n_subcarriers"] == 64
    assert parsed["amplitudes"].shape == (64,)
    # mean amplitude should be in the realistic 15..50 range from our baseline
    assert 10 < parsed["mean_amplitude"] < 60


def test_csi_header_line_is_skipped():
    header = (
        "CSI_HEADER,type,seq,mac,rssi,rate,noise_floor,fft_gain,agc_gain,"
        "channel,local_timestamp,sig_len,rx_state,len,first_word,data"
    )
    assert CSIOccupancyDetector.parse_csi_line(header) is None


def test_unrelated_lines_return_none():
    for line in ["", "\n", "I (1234) wifi: connected", "garbage,without,brackets"]:
        assert CSIOccupancyDetector.parse_csi_line(line) is None


def test_first_word_invalid_skips_two_subcarriers():
    """When first_word_invalid=1, the parser must drop the first 2 subcarriers
    (= 4 bytes) per the ESP32-S3 hardware quirk."""
    rng = random.Random(7)
    iq = synth_iq_pairs("low", rng)
    # Build a header with first_word=1 (the 14th field after CSI_DATA tag)
    mac = "1a:00:00:00:00:00"
    data_field = "[" + ",".join(str(v) for v in iq) + "]"
    line = (
        f'CSI_DATA,1,{mac},-50,11,-96,32,4,11,1234567,47,0,{len(iq)},1,'
        f'"{data_field}"'
    )
    parsed = CSIOccupancyDetector.parse_csi_line(line)
    assert parsed is not None
    # Without skip we'd have 64 amplitudes; with skip, 62.
    assert parsed["n_subcarriers"] == 62


def test_parser_tolerates_trailing_whitespace_and_crlf():
    rng = random.Random(1)
    iq = synth_iq_pairs("empty", rng)
    line = csi_csv_line(seq=99, iq=iq, rssi=-60) + "  \r\n"
    parsed = CSIOccupancyDetector.parse_csi_line(line)
    assert parsed is not None
    assert parsed["seq"] == 99
