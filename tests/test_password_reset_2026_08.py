"""Regression tests for the 2026-08-31 login changes.

The owner asked to sign in with his email address and to be able to reset a
forgotten password without signing in first. Covers:
  A) The email address works as the username (login form + Basic), case-
     insensitively; the legacy short username keeps working for scripts.
  B) /forgot-password emails a reset link to the FIXED owner address and the
     sends are capped globally (anti-mailbomb).
  C) /reset-password validates the HMAC token, stores the new password, kills
     the old credential, signs the resetting browser in, and the used token
     dies with the password change.
"""
import os
import re
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

_TMP = Path(tempfile.mkdtemp(prefix="os-reset-test-"))
app.LEADS_FILE = _TMP / "leads.json"
app.LEADS_FILE.write_text("[]")
app.EVENTS_FILE = _TMP / "events.jsonl"
app.EVENTS_FILE.write_text("")
app.CONVERSATION_LOG_FILE = _TMP / "conversation_logs.jsonl"
app.CONVERSATION_LOG_FILE.write_text("")

# https base_url: the session cookie is Secure, and the cookie jar won't
# return Secure cookies over plain http.
client = TestClient(app.app, base_url="https://testserver")

PASSWORD = "default-password-abc"
OWNER_EMAIL = "owner@example.com"


def _reset_auth_state():
    with app._admin_hash_lock:
        app._admin_hash_cache.update(value=None, epoch="", loaded_at=0.0, ever_loaded=False)
    with app._login_failures_lock:
        app._login_failures.clear()
    with app._reset_email_lock:
        app._reset_email_times.clear()
    client.cookies.clear()


@pytest.fixture(autouse=True)
def _admin_creds():
    # Credentials per-test, never module-level: every test module in this suite
    # reloads `app` at collection time and clobbers module-level assignments.
    orig = (app.ADMIN_USERNAME, app.ADMIN_PASSWORD, app.ADMIN_EMAIL_USERNAME, app.PASSWORD_RESET_EMAIL_TO)
    app.ADMIN_USERNAME = "nick"
    app.ADMIN_PASSWORD = PASSWORD
    app.ADMIN_EMAIL_USERNAME = OWNER_EMAIL
    app.PASSWORD_RESET_EMAIL_TO = OWNER_EMAIL
    _reset_auth_state()
    yield
    app.ADMIN_USERNAME, app.ADMIN_PASSWORD, app.ADMIN_EMAIL_USERNAME, app.PASSWORD_RESET_EMAIL_TO = orig
    _reset_auth_state()


def _fake_settings_store(store):
    def fake_supabase_request(method, table, *, params=None, json_body=None, prefer=None):
        assert table == app.SUPABASE_TABLES["settings"]
        if method == "GET":
            return [
                {"key": key, "value": store[key]}
                for key in (app.ADMIN_SETTINGS_PASSWORD_KEY, app.ADMIN_SETTINGS_EPOCH_KEY)
                if key in store
            ]
        if method == "POST":
            store[json_body["key"]] = json_body["value"]
            return None
        raise AssertionError(f"unexpected method {method}")
    return fake_supabase_request


def _login(username, password=PASSWORD):
    return client.post(
        "/login",
        content=f"username={username}&password={password}",
        headers={"content-type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )


# ── A) Email address as username ────────────────────────────────────────────
def test_email_username_signs_in_on_login_form():
    assert _login(OWNER_EMAIL).status_code == 303


def test_email_username_is_case_insensitive():
    # Phone keyboards capitalise the first letter; that must not lock him out.
    assert _login("Owner%40Example.COM").status_code == 303


def test_legacy_username_still_works_everywhere():
    assert _login("nick").status_code == 303
    assert client.get("/api/metrics", auth=("nick", PASSWORD)).status_code == 200


def test_email_username_works_for_basic_auth():
    assert client.get("/api/metrics", auth=(OWNER_EMAIL, PASSWORD)).status_code == 200
    assert client.get("/api/metrics", auth=("stranger@example.com", PASSWORD)).status_code == 401


def test_login_page_offers_email_and_forgot_link():
    page = client.get("/login")
    assert page.status_code == 200
    assert ">Email<" in page.text
    assert "/forgot-password" in page.text


# ── B) Forgot password: email with reset link ───────────────────────────────
def test_forgot_password_emails_reset_link_to_owner(monkeypatch):
    sent = []
    monkeypatch.setattr(app, "send_email_resend", lambda subject, body, recipients, html=None: sent.append((recipients, body)) or True)
    response = client.post("/forgot-password")
    assert response.status_code == 200
    assert "Check the Outdoor Squad inbox" in response.text
    assert sent[0][0] == [OWNER_EMAIL]
    token = re.search(r"/reset-password\?token=([\w.]+)", sent[0][1]).group(1)
    # The emailed link renders the reset form.
    form = client.get(f"/reset-password?token={token}")
    assert form.status_code == 200
    assert "new_password" in form.text


def test_forgot_password_send_cap_is_global(monkeypatch):
    sent = []
    monkeypatch.setattr(app, "send_email_resend", lambda *a, **k: sent.append(1) or True)
    for _ in range(app.RESET_EMAILS_MAX_PER_HOUR):
        assert client.post("/forgot-password").status_code == 200
    # Over the cap: same page (no oracle), but no further email.
    over = client.post("/forgot-password")
    assert over.status_code == 200
    assert len(sent) == app.RESET_EMAILS_MAX_PER_HOUR


# ── C) Reset flow ───────────────────────────────────────────────────────────
def test_reset_password_full_flow(monkeypatch):
    store = {}
    monkeypatch.setattr(app, "supabase_enabled", lambda: True)
    monkeypatch.setattr(app, "supabase_request", _fake_settings_store(store))

    token = app.issue_password_reset_token()
    response = client.post(
        "/reset-password",
        content=f"token={token}&new_password=brand-new-password-1&confirm_password=brand-new-password-1",
        headers={"content-type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    # Straight into the dashboard, signed in with the fresh material.
    assert response.status_code == 303
    assert response.headers["location"] == "/admin"
    assert store[app.ADMIN_SETTINGS_PASSWORD_KEY].startswith("pbkdf2_sha256$")
    cookie = response.cookies[app.ADMIN_SESSION_COOKIE]
    client.cookies.clear()
    client.cookies.set(app.ADMIN_SESSION_COOKIE, cookie)
    assert client.get("/admin", headers={"accept": "text/html"}, follow_redirects=False).status_code == 200

    # Old credential dead, new one live (email username included).
    assert client.get("/api/metrics", auth=(OWNER_EMAIL, PASSWORD)).status_code == 401
    assert client.get("/api/metrics", auth=(OWNER_EMAIL, "brand-new-password-1")).status_code == 200

    # The used token died with the password change (secret basis rotated).
    assert client.get(f"/reset-password?token={token}").status_code == 410


def test_reset_password_rejects_garbage_and_expired_tokens():
    assert client.get("/reset-password?token=garbage").status_code == 410
    expired = f"{int(app.time.time()) - 5}.deadbeef"
    assert client.get(f"/reset-password?token={expired}").status_code == 410
    response = client.post(
        "/reset-password",
        content="token=garbage&new_password=brand-new-password-1&confirm_password=brand-new-password-1",
        headers={"content-type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert response.status_code == 410


def test_reset_password_validates_the_new_password(monkeypatch):
    monkeypatch.setattr(app, "supabase_enabled", lambda: True)
    monkeypatch.setattr(app, "supabase_request", _fake_settings_store({}))
    token = app.issue_password_reset_token()
    short = client.post(
        "/reset-password",
        content=f"token={token}&new_password=short&confirm_password=short",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert short.status_code == 422
    mismatch = client.post(
        "/reset-password",
        content=f"token={token}&new_password=brand-new-password-1&confirm_password=brand-new-password-2",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert mismatch.status_code == 422
