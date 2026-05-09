"""Governance surface — AI decision log, community feedback, public transparency.

These tests assert the *contract* between operator (private), the watched
(public transparency), and the system. The privacy invariants are encoded
in the test, not just in docs.
"""

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
    raise RuntimeError(f"timed out: {url}")


@pytest.fixture
def stack(tmp_path):
    sim_tcp = _free_port(); sim_http = _free_port(); api = _free_port()
    db = tmp_path / "gov.db"

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
    env["SERIAL_PORT"]        = f"tcp://127.0.0.1:{sim_tcp}"
    env["FIRMWARE_HTTP_URL"]  = f"http://127.0.0.1:{sim_http}"
    env["ECHOLOCATE_DB"]      = str(db)
    env["SNAPSHOT_COOLDOWN"]  = "3"

    backend = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.main:app",
         "--host", "127.0.0.1", "--port", str(api), "--log-level", "warning"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for(f"http://127.0.0.1:{sim_http}/health")
        _wait_for(f"http://127.0.0.1:{api}/")
        yield {"api": api, "sim_http": sim_http}
    finally:
        for p in (backend, sim):
            try: p.terminate(); p.wait(timeout=5)
            except Exception: p.kill()


def _drive_threshold_breach(api: int, sim_http: int) -> None:
    """Calibrate empty, then ramp sim → high; backend logs an AI decision."""
    time.sleep(13)  # finish calibration
    httpx.post(f"http://127.0.0.1:{sim_http}/control",
               json={"level": "high"}, timeout=2.0)
    time.sleep(6)   # window + monitor cycle


def test_ai_decision_recorded_on_threshold_breach(stack):
    _drive_threshold_breach(stack["api"], stack["sim_http"])
    api = f"http://127.0.0.1:{stack['api']}"

    r = httpx.get(f"{api}/api/decisions")
    assert r.status_code == 200
    data = r.json()
    assert data["stats"]["total"] >= 1
    decision = data["decisions"][0]
    assert decision["decision_type"] == "spatial_analysis"
    assert decision["operator_status"] == "pending"
    assert "summary" in decision and decision["summary"]


def test_operator_can_change_decision_status_with_notes(stack):
    _drive_threshold_breach(stack["api"], stack["sim_http"])
    api = f"http://127.0.0.1:{stack['api']}"

    decisions = httpx.get(f"{api}/api/decisions").json()["decisions"]
    did = decisions[0]["id"]

    r = httpx.post(f"{api}/api/decisions/{did}",
                   json={"status": "accepted", "notes": "Will move counter"})
    assert r.status_code == 200

    refetched = httpx.get(f"{api}/api/decisions").json()["decisions"]
    by_id = {d["id"]: d for d in refetched}
    assert by_id[did]["operator_status"] == "accepted"
    assert by_id[did]["operator_notes"] == "Will move counter"
    assert by_id[did]["operator_at"] is not None


def test_invalid_decision_status_rejected(stack):
    _drive_threshold_breach(stack["api"], stack["sim_http"])
    api = f"http://127.0.0.1:{stack['api']}"
    decisions = httpx.get(f"{api}/api/decisions").json()["decisions"]
    did = decisions[0]["id"]
    r = httpx.post(f"{api}/api/decisions/{did}", json={"status": "approved-by-mom"})
    assert r.status_code == 400


def test_public_transparency_redacts_raw_io(stack):
    """The public transparency endpoint must NEVER expose raw_input or
    raw_output. Operator can see them; watched cannot."""
    _drive_threshold_breach(stack["api"], stack["sim_http"])
    api = f"http://127.0.0.1:{stack['api']}"

    # Operator can see the raw I/O
    operator = httpx.get(f"{api}/api/decisions").json()
    assert "raw_input" in operator["decisions"][0]
    assert "raw_output" in operator["decisions"][0]

    # Public cannot
    public = httpx.get(f"{api}/api/transparency").json()
    for d in public["ai_activity"]["recent"]:
        assert "raw_input" not in d, f"raw_input leaked to public: {d}"
        assert "raw_output" not in d, f"raw_output leaked to public: {d}"
        # But operator decisions ARE public — that's the accountability claim
        assert "operator_status" in d
        assert "summary" in d


def test_public_transparency_lists_privacy_invariants(stack):
    api = f"http://127.0.0.1:{stack['api']}"
    r = httpx.get(f"{api}/api/transparency")
    assert r.status_code == 200
    inv = r.json()["privacy_invariants"]
    assert len(inv["what_is_collected"]) > 0
    assert len(inv["what_is_NEVER_collected"]) > 0
    # Sanity: forbidden things must explicitly appear in the "never" list
    never = " ".join(inv["what_is_NEVER_collected"]).lower()
    for forbidden in ("photo", "face", "name", "email", "ip", "mac"):
        assert forbidden in never, f"'{forbidden}' must be in 'never collected'"


def test_community_feedback_anonymous_round_trip(stack):
    api = f"http://127.0.0.1:{stack['api']}"

    r = httpx.post(f"{api}/api/community-feedback", json={
        "sentiment": "concern", "message": "Door area felt tight at noon",
    })
    assert r.status_code == 200
    fid = r.json()["id"]

    listed = httpx.get(f"{api}/api/community-feedback").json()["feedback"]
    by_id = {f["id"]: f for f in listed}
    assert fid in by_id
    f = by_id[fid]
    assert f["sentiment"] == "concern"
    assert f["message"] == "Door area felt tight at noon"
    # Must NOT contain identifiers
    for forbidden_key in ("user", "ip", "email", "name", "device", "token"):
        assert forbidden_key not in f, f"feedback leaked '{forbidden_key}'"


def test_community_feedback_visible_on_transparency(stack):
    api = f"http://127.0.0.1:{stack['api']}"
    httpx.post(f"{api}/api/community-feedback", json={
        "sentiment": "suggestion", "message": "More seating near windows please",
    })
    public = httpx.get(f"{api}/api/transparency").json()
    msgs = [f["message"] for f in public["community_feedback_recent"]]
    assert "More seating near windows please" in msgs


def test_empty_message_rejected(stack):
    api = f"http://127.0.0.1:{stack['api']}"
    r = httpx.post(f"{api}/api/community-feedback", json={"message": "   "})
    assert r.status_code == 400


def test_chat_call_logged_as_ai_decision(stack):
    api = f"http://127.0.0.1:{stack['api']}"
    httpx.post(f"{api}/api/chat", json={"message": "Is it busy now?"})
    decisions = httpx.get(f"{api}/api/decisions").json()["decisions"]
    chat_decisions = [d for d in decisions if d["decision_type"] == "chat"]
    assert len(chat_decisions) >= 1
    assert "Is it busy now?" in chat_decisions[0]["summary"]
