"""End-to-end: boot the simulator + backend together, drive level transitions,
assert the REST API reports correct state.

Skipped automatically if `httpx` isn't available.
"""

import os
import socket
import subprocess
import sys
import tempfile
import time

import pytest

httpx = pytest.importorskip("httpx")


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _wait_for_http(url: str, timeout: float = 15.0) -> None:
    end = time.time() + timeout
    last_err = None
    while time.time() < end:
        try:
            r = httpx.get(url, timeout=1.0)
            if r.status_code < 500:
                return
        except Exception as e:
            last_err = e
        time.sleep(0.2)
    raise RuntimeError(f"timed out waiting for {url}: {last_err}")


@pytest.fixture
def sim_and_backend(tmp_path):
    sim_tcp = _free_port()
    sim_http = _free_port()
    api_port = _free_port()

    db = tmp_path / "test.db"

    sim = subprocess.Popen(
        [sys.executable, "-m", "sim.esp32_sim",
         "--no-stdin",
         "--tcp-port", str(sim_tcp),
         "--http-port", str(sim_http),
         "--level", "empty"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    env = os.environ.copy()
    # Tests must never burn real API tokens. Force stub mode by
    # blanking any AI keys the parent shell or .env may have set.
    env["ANTHROPIC_API_KEY"] = ""
    env["OPENAI_API_KEY"] = ""
    env["SERIAL_PORT"] = f"tcp://127.0.0.1:{sim_tcp}"
    env["ECHOLOCATE_DB"] = str(db)
    env["SNAPSHOT_COOLDOWN"] = "5"

    backend = subprocess.Popen(
        [sys.executable, "-m", "uvicorn",
         "backend.main:app",
         "--host", "127.0.0.1",
         "--port", str(api_port),
         "--log-level", "warning"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    try:
        _wait_for_http(f"http://127.0.0.1:{sim_http}/health")
        _wait_for_http(f"http://127.0.0.1:{api_port}/")
        yield {
            "sim_tcp": sim_tcp,
            "sim_http": sim_http,
            "api_port": api_port,
            "sim_proc": sim,
            "backend_proc": backend,
        }
    finally:
        for p in (backend, sim):
            try:
                p.terminate()
                p.wait(timeout=5)
            except Exception:
                p.kill()


def test_e2e_calibration_then_status(sim_and_backend):
    api = f"http://127.0.0.1:{sim_and_backend['api_port']}"

    # Initial status: should be calibrating
    r = httpx.get(f"{api}/api/status")
    assert r.status_code == 200
    data = r.json()
    assert "occupancy" in data
    assert data["stream_health"]["source"].startswith("tcp://")

    # After ~12s of empty data, calibration should be done and level=empty
    time.sleep(13)
    r = httpx.get(f"{api}/api/status")
    occ = r.json()["occupancy"]
    assert occ["calibration_phase"] is False, occ
    assert occ["level"] == "empty", occ
    assert r.json()["stream_health"]["lines_parsed"] > 100


def test_e2e_level_transition_triggers_observation(sim_and_backend):
    """Calibrate empty, then ramp simulator to high — backend must escalate
    occupancy and (since SNAPSHOT_COOLDOWN=5) record at least one observation
    via the stub Vision path."""
    api = f"http://127.0.0.1:{sim_and_backend['api_port']}"
    sim_http = f"http://127.0.0.1:{sim_and_backend['sim_http']}"

    # 1. Wait for calibration to complete on empty. The synthetic noise
    # sometimes drifts into "low" right at calibration end — both are
    # legitimately below threshold, so accept either.
    time.sleep(13)
    r = httpx.get(f"{api}/api/status")
    assert r.json()["occupancy"]["level"] in ("empty", "low")
    assert r.json()["occupancy"]["threshold_exceeded"] is False

    # 2. Ramp simulator to high
    r = httpx.post(f"{sim_http}/control", json={"level": "high"})
    assert r.status_code == 200 and r.json()["ok"] is True

    # 3. Wait for the detector window (5s) + monitoring loop (2s) +
    # snapshot cooldown to elapse
    time.sleep(10)

    r = httpx.get(f"{api}/api/status")
    occ = r.json()["occupancy"]
    assert occ["level"] in ("moderate", "high"), f"expected moderate/high, got {occ}"
    assert occ["threshold_exceeded"] is True

    # 4. At least one spatial observation should have been recorded.
    # Camera isn't available in CI, so this exercises the no-camera path,
    # which still creates a metadata-only record.
    r = httpx.get(f"{api}/api/observations")
    obs = r.json()["observations"]
    assert len(obs) >= 1, "expected at least one observation"


def test_e2e_register_and_chat(sim_and_backend):
    api = f"http://127.0.0.1:{sim_and_backend['api_port']}"

    # Anonymous registration
    r = httpx.post(f"{api}/api/register", json={"push_subscription": {}})
    assert r.status_code == 200
    token = r.json()["token_id"]
    assert token

    # Check-in
    r = httpx.post(f"{api}/api/checkin", json={"token_id": token, "zone": "main"})
    assert r.status_code == 200

    # Chat (stub mode is fine)
    r = httpx.post(f"{api}/api/chat", json={"message": "How crowded is it?"})
    assert r.status_code == 200
    body = r.json()
    assert "response" in body
    assert isinstance(body["response"], str)
    assert len(body["response"]) > 0
