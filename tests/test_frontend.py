"""Frontend smoke test — boots the backend and asserts the static PWA loads."""

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
def backend(tmp_path):
    api = _free_port()
    env = os.environ.copy()
    # Tests must never burn real API tokens. Force stub mode by
    # blanking any AI keys the parent shell or .env may have set.
    env["ANTHROPIC_API_KEY"] = ""
    env["OPENAI_API_KEY"] = ""
    # Point at a non-existent serial source — backend still serves the frontend.
    env["SERIAL_PORT"] = "tcp://127.0.0.1:1"
    env["ECHOLOCATE_DB"] = str(tmp_path / "fe.db")
    env["FIRMWARE_HTTP_URL"] = "http://127.0.0.1:1"
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.main:app",
         "--host", "127.0.0.1", "--port", str(api), "--log-level", "warning"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for(f"http://127.0.0.1:{api}/")
        yield api
    finally:
        try: proc.terminate(); proc.wait(timeout=5)
        except Exception: proc.kill()


def test_app_root_serves_html(backend):
    r = httpx.get(f"http://127.0.0.1:{backend}/app/")
    assert r.status_code == 200
    assert "<!DOCTYPE html>" in r.text
    assert "Echolocate" in r.text


def test_app_includes_three_primary_tabs(backend):
    """The app collapses into three modes: Home, Consumer, Business."""
    r = httpx.get(f"http://127.0.0.1:{backend}/app/")
    html = r.text
    for tab in ("home", "consumer", "business"):
        assert f'data-tab="{tab}"' in html, f"tab '{tab}' missing"
    # The old multi-tab shape should be gone from the nav
    assert html.count('<nav class="tabs">') == 1


def test_app_js_references_consumer_business_endpoints(backend):
    r = httpx.get(f"http://127.0.0.1:{backend}/app/app.js")
    js = r.text
    for path in (
        "/api/consumer/check-in",
        "/api/consumer/notifications",
        "/api/consumer/my-visits",
        "/api/consumer/report-sick",
        "/api/business/notify-visitors",
        "/api/business/visits",
        "/api/decisions",
        "/api/community-feedback",
        "/api/transparency",
    ):
        assert path in js, f"app.js missing reference to {path}"


def test_app_html_has_consumer_and_business_forms(backend):
    """Spot-check the key interactive elements that drive the contact-tracing
    flow exist in the served HTML."""
    r = httpx.get(f"http://127.0.0.1:{backend}/app/")
    html = r.text
    for el in (
        'id="btn-checkin"',           # consumer: I'm here
        'id="con-crowded"',           # consumer: felt crowded toggle
        'id="btn-report-sick"',       # consumer: report positive
        'id="con-notifs"',            # consumer: notifications inbox
        'id="con-visits"',            # consumer: my visits
        'id="broadcast-form"',        # business: notify visitors
        'id="bc-from"',               # business: time window
        'id="bc-to"',
        'id="bc-body"',
        'id="biz-visits-total"',      # business: stats
    ):
        assert el in html, f"missing element: {el}"


def test_static_pwa_assets_load(backend):
    base = f"http://127.0.0.1:{backend}/app"
    for path in ("app.js", "sw.js", "manifest.json", "icon-192.png", "icon-512.png"):
        r = httpx.get(f"{base}/{path}")
        assert r.status_code == 200, f"{path} returned {r.status_code}"
        assert len(r.content) > 0


def test_manifest_is_valid_json_with_correct_scope(backend):
    r = httpx.get(f"http://127.0.0.1:{backend}/app/manifest.json")
    assert r.status_code == 200
    data = r.json()
    assert data["display"] == "standalone"
    assert data["scope"] == "/app/"
    assert any(icon.get("sizes") == "192x192" for icon in data["icons"])


def test_app_js_references_diagnostics_endpoint(backend):
    r = httpx.get(f"http://127.0.0.1:{backend}/app/app.js")
    assert "/api/diagnostics" in r.text
    assert "/api/firmware-status" in r.text
