"""The /api/_demo/seed endpoint must produce a coherent dataset every time
so a judge or operator can demo the same story. These tests assert the
shape and the privacy invariants — not the exact wording."""

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
            if r.status_code < 500: return
        except Exception: pass
        time.sleep(0.2)
    raise RuntimeError(f"timed out: {url}")


@pytest.fixture
def stack(tmp_path):
    sim_tcp = _free_port(); sim_http = _free_port(); api = _free_port()
    db = tmp_path / "ds.db"
    sim = subprocess.Popen(
        [sys.executable, "-m", "sim.esp32_sim",
         "--no-stdin", "--tcp-port", str(sim_tcp),
         "--http-port", str(sim_http), "--level", "empty"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    env = os.environ.copy()
    env["ANTHROPIC_API_KEY"] = ""
    env["OPENAI_API_KEY"] = ""
    env["SERIAL_PORT"]       = f"tcp://127.0.0.1:{sim_tcp}"
    env["FIRMWARE_HTTP_URL"] = f"http://127.0.0.1:{sim_http}"
    env["ECHOLOCATE_DB"]     = str(db)
    backend = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.main:app",
         "--host", "127.0.0.1", "--port", str(api), "--log-level", "warning"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for(f"http://127.0.0.1:{sim_http}/health")
        _wait_for(f"http://127.0.0.1:{api}/")
        yield {"api": api}
    finally:
        for p in (backend, sim):
            try: p.terminate(); p.wait(timeout=5)
            except Exception: p.kill()


def test_seed_populates_every_panel(stack):
    api = f"http://127.0.0.1:{stack['api']}"
    r = httpx.post(f"{api}/api/_demo/seed")
    assert r.status_code == 200
    s = r.json()["summary"]
    assert s["tokens_registered"] >= 6
    assert s["visits"]              >= 10
    assert s["community_feedback"]  >= 3
    assert s["spatial_observations"] >= 3
    assert s["ai_decisions"]        >= 5
    assert s["notifications"]       >= 3

    # Business panels
    assert httpx.get(f"{api}/api/business/visits").json()["total_visits"] >= 10
    decisions = httpx.get(f"{api}/api/decisions").json()
    assert decisions["stats"]["total"] >= 5
    by_status = decisions["stats"]["by_status"]
    # The fixture story includes a mix of statuses — never all pending
    assert by_status.get("accepted", 0) >= 1
    assert by_status.get("pending", 0) >= 1

    feedback = httpx.get(f"{api}/api/community-feedback").json()["feedback"]
    sentiments = {f["sentiment"] for f in feedback}
    assert sentiments & {"concern", "suggestion", "praise"}, \
        "feedback must have sentiment diversity"

    obs = httpx.get(f"{api}/api/observations").json()["observations"]
    assert len(obs) >= 3
    assert any(o.get("chokepoints") for o in obs), "must have chokepoint examples"


def test_seed_anchor_token_has_inbox(stack):
    """The demo phone token must come back with an inbox so the Consumer
    tab is non-empty when the user adopts it."""
    api = f"http://127.0.0.1:{stack['api']}"
    r = httpx.post(f"{api}/api/_demo/seed").json()
    tok = r["demo_token_anchor_a"]
    assert tok

    notifs = httpx.get(f"{api}/api/consumer/notifications",
                       params={"token_id": tok}).json()["notifications"]
    assert len(notifs) >= 2
    types = {n["type"] for n in notifs}
    # Story includes at least an exposure or general broadcast plus a crowding alert
    assert types & {"exposure", "general", "crowding"}

    visits = httpx.get(f"{api}/api/consumer/my-visits",
                       params={"token_id": tok}).json()["visits"]
    assert len(visits) >= 1


def test_reset_then_seed_is_idempotent(stack):
    api = f"http://127.0.0.1:{stack['api']}"
    s1 = httpx.post(f"{api}/api/_demo/seed").json()["summary"]
    s2 = httpx.post(f"{api}/api/_demo/seed").json()["summary"]
    # Same fixture, same counts (reset=true is the default)
    for k in ("tokens_registered", "visits", "community_feedback",
              "spatial_observations", "ai_decisions"):
        assert s1[k] == s2[k], f"{k} drifted between seeds"


def test_reset_wipes_everything(stack):
    api = f"http://127.0.0.1:{stack['api']}"
    httpx.post(f"{api}/api/_demo/seed")
    httpx.post(f"{api}/api/_demo/reset")
    assert httpx.get(f"{api}/api/business/visits").json()["total_visits"] == 0
    assert httpx.get(f"{api}/api/decisions").json()["stats"]["total"] == 0
    assert httpx.get(f"{api}/api/community-feedback").json()["feedback"] == []
    assert httpx.get(f"{api}/api/observations").json()["observations"] == []


def test_seed_does_not_leak_identifiers(stack):
    """Privacy invariant: seeded data must not contain anything that could
    identify a real person — the anchor labels are pseudonyms, not names."""
    api = f"http://127.0.0.1:{stack['api']}"
    httpx.post(f"{api}/api/_demo/seed")
    # No persona label like "anchor-A" should leak into the public surface
    for endpoint in ("/api/transparency", "/api/community-feedback",
                     "/api/business/visits"):
        body = httpx.get(f"{api}{endpoint}").text
        for forbidden in ("anchor-A", "lunch-rush-1", "afternoon-1"):
            assert forbidden not in body, f"{forbidden!r} leaked via {endpoint}"
