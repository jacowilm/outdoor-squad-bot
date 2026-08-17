"""Regression tests for the 2026-08-17 home-screen app (PWA) work.

The dashboard became installable on a phone. The parts that can silently break
an install are all asserted here, because a broken install fails as "Add to
Home Screen just made a bookmark" with no error anywhere:
  A) the manifest is PUBLIC (an auth-gated manifest is fetched without
     credentials and simply never loads)
  B) the manifest declares what makes an app installable
  C) icons are real PNGs at the declared sizes
  D) /install is reachable before signing in (it is what the QR opens)
  E) the service worker is served as JavaScript and caches no dashboard data
  F) both auth pages carry the standalone meta tags
"""
import os
import sys
import tempfile
import importlib
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for _k in (
    "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_KEY",
    "OUTDOOR_SQUAD_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY",
    "OUTDOOR_SQUAD_OPENAI_API_KEY", "OPENAI_API_KEY",
    "OUTDOOR_SQUAD_GEMINI_API_KEY", "GEMINI_API_KEY",
):
    os.environ.pop(_k, None)

import app  # noqa: E402
importlib.reload(app)
app.SUPABASE_URL = ""
app.SUPABASE_KEY = None
from fastapi.testclient import TestClient  # noqa: E402

_TMP = Path(tempfile.mkdtemp(prefix="os-pwa-test-"))
app.LEADS_FILE = _TMP / "leads.json"
app.LEADS_FILE.write_text("[]")
app.EVENTS_FILE = _TMP / "events.jsonl"
app.EVENTS_FILE.write_text("")
app.CONVERSATION_LOG_FILE = _TMP / "conversation_logs.jsonl"
app.CONVERSATION_LOG_FILE.write_text("")

client = TestClient(app.app, base_url="https://testserver")

BASIC_OK = ("nick", "pwa-test-password")


@pytest.fixture(autouse=True)
def _admin_creds():
    # Per-test, NOT module level: every test file in this suite calls
    # importlib.reload(app) at import time, so whichever module imports last wins
    # and module-level credential assignment silently reverts to None.
    orig_user, orig_pass = app.ADMIN_USERNAME, app.ADMIN_PASSWORD
    app.ADMIN_USERNAME, app.ADMIN_PASSWORD = BASIC_OK
    yield
    app.ADMIN_USERNAME, app.ADMIN_PASSWORD = orig_user, orig_pass


# ── A/B) Manifest ───────────────────────────────────────────────────────────
def test_manifest_is_public_and_installable():
    response = client.get("/app.webmanifest")
    assert response.status_code == 200, "an auth-gated manifest never loads"
    assert "manifest" in response.headers["content-type"]
    m = response.json()
    assert m["display"] == "standalone"
    assert m["start_url"] == "/admin"
    assert m["scope"] == "/"
    assert m["name"] and m["short_name"]
    sizes = {i["sizes"] for i in m["icons"]}
    assert {"192x192", "512x512"} <= sizes, "Android needs both 192 and 512"
    assert any(i.get("purpose") == "maskable" for i in m["icons"])
    assert m["background_color"] == app.REALTIQ_NAVY


def test_manifest_start_url_carries_no_secret():
    """Unlike the Desk (URL-token auth), this app authenticates with a password,
    so nothing sensitive may ride in the install link — the QR is shareable."""
    m = client.get("/app.webmanifest").json()
    assert "?" not in m["start_url"]
    assert app.ADMIN_PASSWORD not in client.get("/app.webmanifest").text
    assert app.ADMIN_USERNAME not in client.get("/app.webmanifest").text


# ── C) Icons ────────────────────────────────────────────────────────────────
def test_icons_are_real_pngs():
    for path, expected in (("/icon-192.png", 192), ("/icon-512.png", 512),
                           ("/icon-maskable-512.png", 512)):
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.headers["content-type"] == "image/png"
        assert response.content[:8] == b"\x89PNG\r\n\x1a\n", f"{path} is not a PNG"
        # width is bytes 16-20 of the IHDR chunk
        width = int.from_bytes(response.content[16:20], "big")
        assert width == expected, f"{path} is {width}px, manifest claims {expected}"


# ── D) Install page ─────────────────────────────────────────────────────────
def test_install_page_is_reachable_without_signing_in():
    response = client.get("/install", headers={"accept": "text/html"})
    assert response.status_code == 200
    body = response.text
    assert "Add to Home Screen" in body      # iPhone route
    assert "Install app" in body             # Android route
    assert "/app.webmanifest" in body
    # It must set expectations about the one-time sign-in, or the login screen
    # on first launch reads as "the app is broken".
    assert "sign in once" in body.lower()


def test_install_page_leaks_no_credentials():
    body = client.get("/install").text
    assert app.ADMIN_PASSWORD not in body
    assert app.ADMIN_USERNAME not in body


# ── E) Service worker ───────────────────────────────────────────────────────
def test_service_worker_is_js_and_caches_no_data():
    response = client.get("/sw.js")
    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]
    js = response.text
    assert "addEventListener('fetch'" in js, "no fetch handler = not installable"
    # A stale lead count is worse than an honest offline page, so there must be
    # no Cache Storage use at all.
    assert "caches.open" not in js and "cache.put" not in js
    assert "mode !== 'navigate'" in js, "only navigations may be intercepted"


# ── F) Standalone meta on both auth pages ───────────────────────────────────
def test_auth_pages_declare_standalone_app():
    for path in ("/login", "/admin"):
        body = client.get(path, headers={"accept": "text/html"},
                          auth=BASIC_OK, follow_redirects=True).text
        assert 'rel="manifest"' in body, path
        assert 'name="apple-mobile-web-app-capable"' in body, path
        assert 'name="apple-mobile-web-app-title"' in body, path
        # black, NOT black-translucent: translucent slides the header under the
        # clock on iPhone.
        assert 'content="black"' in body, path
        assert "black-translucent" not in body, path
        assert "viewport-fit=cover" in body, path


def test_leads_table_is_labelled_for_the_phone_layout():
    """Under 700px the header row is dropped and each cell shows its own label,
    so every column must carry one or the phone view loses the column name."""
    body = client.get("/admin", headers={"accept": "text/html"},
                      auth=BASIC_OK, follow_redirects=True).text
    for label in ("When", "Name", "Contact", "Route", "Context", "Session"):
        assert f"data-label=\\\"{label}\\\"" in body or f'data-label="{label}"' in body, label
    assert ".lead-table thead { display: none; }" in body
