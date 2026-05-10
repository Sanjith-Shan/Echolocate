"""Diagnostics + firmware-status endpoints — boots the full stack."""

import os
import socket
import subprocess
import sys
import time

import pytest

httpx = pytest.importorskip("httpx")

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _free_port() -> int:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def _wait_for(url: str, timeout: float = 15.0) -> None:
    end = time.time() + timeout
    while time.time() < end:
        try:
            r = httpx.get(url, timeout=1.0)
            if r.status_code < 500:
                return
        except Exception:
            pass
        time.sleep(0.2)
    raise RuntimeError(f"timed out waiting for {url}")


@pytest.fixture
def stack(tmp_path):
    sim_tcp = _free_port(); sim_http = _free_port(); api = _free_port()
    db = tmp_path / "diag.db"

    sim = subprocess.Popen(
        [sys.executable, "-m", "sim.esp32_sim",
         "--no-stdin", "--tcp-port", str(sim_tcp),
         "--http-port", str(sim_http), "--level", "empty"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    env = os.environ.copy()
    # Tests must never burn real API tokens. Force stub mode by
    # blanking any AI keys the parent shell or .env may have set.
    env["ANTHROPIC_API_KEY"] = ""
    env["OPENAI_API_KEY"] = ""
    env["ECHOLOCATE_AUTOSEED"] = "0"
    env["SERIAL_PORT"] = f"tcp://127.0.0.1:{sim_tcp}"
    env["FIRMWARE_HTTP_URL"] = f"http://127.0.0.1:{sim_http}"
    env["ECHOLOCATE_DB"] = str(db)
    env["SNAPSHOT_COOLDOWN"] = "5"

    backend = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.main:app",
         "--host", "127.0.0.1", "--port", str(api), "--log-level", "warning"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for(f"http://127.0.0.1:{sim_http}/health")
        _wait_for(f"http://127.0.0.1:{api}/")
        yield {"api": api, "sim_http": sim_http, "sim_proc": sim, "backend_proc": backend}
    finally:
        for p in (backend, sim):
            try: p.terminate(); p.wait(timeout=5)
            except Exception: p.kill()


def test_firmware_status_proxies_to_device(stack):
    api = f"http://127.0.0.1:{stack['api']}"
    r = httpx.get(f"{api}/api/firmware-status")
    assert r.status_code == 200
    data = r.json()
    assert data["reachable"] is True
    assert "device" in data
    # Sim mirrors the firmware schema
    assert data["device"]["chip"] in ("simulator", "esp32s3")
    assert "ping_replies" in data["device"]


def test_firmware_status_reports_unreachable_cleanly(stack):
    """Kill the sim — backend should still answer the proxy call gracefully."""
    api = f"http://127.0.0.1:{stack['api']}"
    stack["sim_proc"].terminate()
    stack["sim_proc"].wait(timeout=5)
    time.sleep(0.5)

    r = httpx.get(f"{api}/api/firmware-status")
    assert r.status_code == 200, "must not 5xx — frontend depends on graceful failure"
    data = r.json()
    assert data["reachable"] is False
    assert "hint" in data and "FIRMWARE_HTTP_URL" in data["hint"]


def test_diagnostics_returns_all_expected_checks(stack):
    api = f"http://127.0.0.1:{stack['api']}"
    # Wait for calibration so calibration check goes green
    time.sleep(13)
    r = httpx.get(f"{api}/api/diagnostics")
    assert r.status_code == 200
    data = r.json()
    ids = [c["id"] for c in data["checks"]]
    for required in ("csi_stream", "calibration", "firmware_reachable",
                     "ai_provider", "vapid", "camera"):
        assert required in ids, f"missing check {required}"

    # The two structural checks must be green when the stack is healthy
    by_id = {c["id"]: c for c in data["checks"]}
    assert by_id["csi_stream"]["ok"] is True
    assert by_id["calibration"]["ok"] is True
    assert by_id["firmware_reachable"]["ok"] is True

    # Top-level `ok` only depends on structural checks (csi_stream + calibration)
    assert data["ok"] is True

    # Failing checks must include actionable blocker text
    for c in data["checks"]:
        if not c["ok"] and c["id"] in ("ai_provider", "vapid"):
            assert c["blocker"], f"{c['id']} should suggest a fix when not ok"


def test_dotenv_autoload_path():
    """Importing backend.main with a .env at project root populates os.environ."""
    import importlib, sys
    sentinel = "ECHOLOCATE_PYTEST_SENTINEL_42"
    env_path = os.path.join(ROOT, ".env")
    # Don't clobber a real .env if one exists
    if os.path.exists(env_path):
        pytest.skip(".env already exists at project root — skipping autoload test")
    try:
        with open(env_path, "w") as f:
            f.write(f"{sentinel}=loaded\n")
        os.environ.pop(sentinel, None)
        # Force a fresh import
        for mod in list(sys.modules):
            if mod == "backend.main" or mod.startswith("backend.main."):
                del sys.modules[mod]
        sys.path.insert(0, ROOT)
        import backend.main  # noqa: F401  — triggers load_dotenv
        assert os.environ.get(sentinel) == "loaded"
    finally:
        if os.path.exists(env_path):
            os.unlink(env_path)
        os.environ.pop(sentinel, None)
