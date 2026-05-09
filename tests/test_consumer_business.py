"""Consumer ↔ Business contact-tracing flow.

Covers the new two-mode UX: a consumer checks in, optionally reports sick;
the business broadcasts to everyone who was there in a window. Notifications
are written to a per-token inbox even when push delivery isn't configured.
"""

import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

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


def _utc(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def stack(tmp_path):
    sim_tcp = _free_port(); sim_http = _free_port(); api = _free_port()
    db = tmp_path / "cb.db"

    sim = subprocess.Popen(
        [sys.executable, "-m", "sim.esp32_sim",
         "--no-stdin", "--tcp-port", str(sim_tcp),
         "--http-port", str(sim_http), "--level", "empty"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    env = os.environ.copy()
    env["SERIAL_PORT"]       = f"tcp://127.0.0.1:{sim_tcp}"
    env["FIRMWARE_HTTP_URL"] = f"http://127.0.0.1:{sim_http}"
    env["ECHOLOCATE_DB"]     = str(db)
    env["SNAPSHOT_COOLDOWN"] = "5"

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


def _api(stack) -> str:
    return f"http://127.0.0.1:{stack['api']}"


def _register(stack) -> str:
    r = httpx.post(f"{_api(stack)}/api/register", json={})
    return r.json()["token_id"]


# ---------- Consumer: check-in ----------

def test_consumer_checkin_logs_visit(stack):
    tok = _register(stack)
    r = httpx.post(f"{_api(stack)}/api/consumer/check-in",
                   json={"token_id": tok, "zone": "main", "crowded": True})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["token_id"] == tok
    assert body["auto_registered"] is False
    assert body["visit_id"] >= 1


def test_consumer_checkin_auto_registers_unknown_token(stack):
    """A first-time consumer with no server-side token should still be able
    to check in — the system auto-registers them and returns the new token."""
    fake = "00000000-0000-0000-0000-000000000000"
    r = httpx.post(f"{_api(stack)}/api/consumer/check-in",
                   json={"token_id": fake, "zone": "main"})
    assert r.status_code == 200
    body = r.json()
    assert body["auto_registered"] is True
    assert body["token_id"] != fake


def test_consumer_my_visits(stack):
    tok = _register(stack)
    httpx.post(f"{_api(stack)}/api/consumer/check-in",
               json={"token_id": tok, "zone": "main", "crowded": True})
    httpx.post(f"{_api(stack)}/api/consumer/check-in",
               json={"token_id": tok, "zone": "lobby"})

    r = httpx.get(f"{_api(stack)}/api/consumer/my-visits", params={"token_id": tok})
    assert r.status_code == 200
    visits = r.json()["visits"]
    assert len(visits) == 2
    zones = {v["zone"] for v in visits}
    assert zones == {"main", "lobby"}
    crowded_visits = [v for v in visits if v["crowded"]]
    assert len(crowded_visits) == 1


# ---------- Consumer ↔ Consumer: exposure flow ----------

def test_report_sick_creates_inapp_notification_for_overlapping_consumer(stack):
    a = _register(stack)
    b = _register(stack)
    httpx.post(f"{_api(stack)}/api/consumer/check-in",
               json={"token_id": a, "zone": "main"})
    httpx.post(f"{_api(stack)}/api/consumer/check-in",
               json={"token_id": b, "zone": "main"})

    r = httpx.post(f"{_api(stack)}/api/consumer/report-sick",
                   json={"token_id": a})
    assert r.status_code == 200
    assert r.json()["notifications_inapp"] >= 1

    # B should see an exposure notification
    r = httpx.get(f"{_api(stack)}/api/consumer/notifications",
                  params={"token_id": b})
    notifs = r.json()["notifications"]
    assert any(n["type"] == "exposure" for n in notifs)


def test_report_sick_does_not_notify_other_zones(stack):
    a = _register(stack); b = _register(stack)
    httpx.post(f"{_api(stack)}/api/consumer/check-in",
               json={"token_id": a, "zone": "main"})
    httpx.post(f"{_api(stack)}/api/consumer/check-in",
               json={"token_id": b, "zone": "balcony"})  # different zone

    httpx.post(f"{_api(stack)}/api/consumer/report-sick",
               json={"token_id": a})

    r = httpx.get(f"{_api(stack)}/api/consumer/notifications",
                  params={"token_id": b})
    assert all(n["type"] != "exposure" for n in r.json()["notifications"])


# ---------- Notification inbox ----------

def test_mark_notification_read(stack):
    a = _register(stack); b = _register(stack)
    httpx.post(f"{_api(stack)}/api/consumer/check-in", json={"token_id": a, "zone": "main"})
    httpx.post(f"{_api(stack)}/api/consumer/check-in", json={"token_id": b, "zone": "main"})
    httpx.post(f"{_api(stack)}/api/consumer/report-sick", json={"token_id": a})

    notifs = httpx.get(f"{_api(stack)}/api/consumer/notifications",
                       params={"token_id": b}).json()["notifications"]
    nid = notifs[0]["id"]

    r = httpx.post(f"{_api(stack)}/api/consumer/notifications/{nid}/read",
                   params={"token_id": b})
    assert r.status_code == 200
    assert r.json()["status"] == "ok"

    # Unread filter should exclude it
    r = httpx.get(f"{_api(stack)}/api/consumer/notifications",
                  params={"token_id": b, "unread_only": True})
    assert all(n["id"] != nid for n in r.json()["notifications"])


def test_unknown_token_returns_empty_inbox_not_error(stack):
    """Stale localStorage shouldn't tank the consumer UI."""
    r = httpx.get(f"{_api(stack)}/api/consumer/notifications",
                  params={"token_id": "definitely-not-a-token"})
    assert r.status_code == 200
    body = r.json()
    assert body["notifications"] == []
    assert "warning" in body


# ---------- Business broadcast ----------

def test_business_broadcast_reaches_only_window_visitors(stack):
    a = _register(stack); b = _register(stack); c = _register(stack)

    # All three check in NOW
    httpx.post(f"{_api(stack)}/api/consumer/check-in", json={"token_id": a, "zone": "main"})
    httpx.post(f"{_api(stack)}/api/consumer/check-in", json={"token_id": b, "zone": "main"})
    httpx.post(f"{_api(stack)}/api/consumer/check-in", json={"token_id": c, "zone": "lobby"})

    now = datetime.now(timezone.utc)
    r = httpx.post(f"{_api(stack)}/api/business/notify-visitors", json={
        "zone": "main",
        "time_from": _utc(now - timedelta(minutes=5)),
        "time_to":   _utc(now + timedelta(minutes=5)),
        "title": "Air filter test",
        "body":  "Filter was offline 30min during your visit.",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["matched_tokens"] == 2  # a and b, not c (different zone)
    assert body["notifications_inapp"] == 2

    # Confirm a and b each got one general-type notification, c did not
    for tok in (a, b):
        ns = httpx.get(f"{_api(stack)}/api/consumer/notifications",
                       params={"token_id": tok}).json()["notifications"]
        assert any(n["title"] == "Air filter test" for n in ns)
    cns = httpx.get(f"{_api(stack)}/api/consumer/notifications",
                    params={"token_id": c}).json()["notifications"]
    assert all(n["title"] != "Air filter test" for n in cns)


def test_business_broadcast_outside_window_matches_nothing(stack):
    a = _register(stack)
    httpx.post(f"{_api(stack)}/api/consumer/check-in", json={"token_id": a, "zone": "main"})

    # Pick a window from 2 days ago — nobody could have visited
    far = datetime.now(timezone.utc) - timedelta(days=2)
    r = httpx.post(f"{_api(stack)}/api/business/notify-visitors", json={
        "zone": "main",
        "time_from": _utc(far - timedelta(hours=1)),
        "time_to":   _utc(far),
        "title": "Old event", "body": "shouldn't reach anyone",
    })
    assert r.json()["matched_tokens"] == 0


def test_business_visit_stats(stack):
    for _ in range(3):
        tok = _register(stack)
        httpx.post(f"{_api(stack)}/api/consumer/check-in",
                   json={"token_id": tok, "zone": "main", "crowded": True})

    r = httpx.get(f"{_api(stack)}/api/business/visits")
    assert r.status_code == 200
    stats = r.json()
    assert stats["total_visits"] == 3
    assert stats["unique_tokens"] == 3
    assert stats["self_reported_crowded"] == 3


# ---------- Privacy invariants ----------

def test_visits_endpoint_returns_no_identifiers(stack):
    """The business visit-stats endpoint must not leak token_ids."""
    tok = _register(stack)
    httpx.post(f"{_api(stack)}/api/consumer/check-in", json={"token_id": tok, "zone": "main"})

    r = httpx.get(f"{_api(stack)}/api/business/visits")
    body = r.json()
    body_str = str(body)
    assert tok not in body_str, "token_id leaked into business stats!"
    for forbidden in ("user", "email", "ip", "name", "device", "rotating"):
        assert forbidden not in body, f"forbidden key {forbidden!r} in business stats"


def test_notification_body_does_not_contain_reporter_token(stack):
    """When A reports sick and B gets notified, the notification body must
    not mention A in any way — that's the whole privacy claim."""
    a = _register(stack); b = _register(stack)
    httpx.post(f"{_api(stack)}/api/consumer/check-in", json={"token_id": a, "zone": "main"})
    httpx.post(f"{_api(stack)}/api/consumer/check-in", json={"token_id": b, "zone": "main"})
    httpx.post(f"{_api(stack)}/api/consumer/report-sick", json={"token_id": a})

    notifs = httpx.get(f"{_api(stack)}/api/consumer/notifications",
                       params={"token_id": b}).json()["notifications"]
    for n in notifs:
        assert a not in n["body"]
        assert a not in (n.get("title") or "")
