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


def test_app_includes_all_tabs(backend):
    r = httpx.get(f"http://127.0.0.1:{backend}/app/")
    html = r.text
    for tab in ("individual", "operator", "transparency", "chat",
                "diagnostics", "privacy"):
        assert f'data-tab="{tab}"' in html, f"tab '{tab}' missing"


def test_app_js_references_governance_endpoints(backend):
    r = httpx.get(f"http://127.0.0.1:{backend}/app/app.js")
    js = r.text
    for path in ("/api/decisions", "/api/community-feedback",
                 "/api/transparency"):
        assert path in js, f"app.js missing reference to {path}"


def test_app_html_has_plain_language_toggle(backend):
    r = httpx.get(f"http://127.0.0.1:{backend}/app/")
    assert 'id="plain-toggle"' in r.text


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
