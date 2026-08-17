"""Regression tests for the 2026-08-17 admin auth overhaul.

The HTTP Basic browser popup was replaced with a real /login page backed by a
signed session cookie, plus an owner-facing change-password flow whose hash
lives in the Supabase settings table (env default until first change). Covers:
  A) Basic auth still works for API callers (watcher / scripts / monitoring)
  B) Unauthenticated browser hits on /admin redirect to /login (no popup)
  C) /login form flow issues a session cookie that opens /admin
  D) Wrong login is rejected and rate-limited
  E) change-password: stored hash takes over, env default stops working,
     old sessions die, the changing browser stays signed in
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

_TMP = Path(tempfile.mkdtemp(prefix="os-auth-test-"))
app.LEADS_FILE = _TMP / "leads.json"
app.LEADS_FILE.write_text("[]")
app.EVENTS_FILE = _TMP / "events.jsonl"
app.EVENTS_FILE.write_text("")
app.CONVERSATION_LOG_FILE = _TMP / "conversation_logs.jsonl"
app.CONVERSATION_LOG_FILE.write_text("")

# https base_url: the session cookie is Secure, and the cookie jar won't
# return Secure cookies over plain http.
client = TestClient(app.app, base_url="https://testserver")

BASIC_OK = ("nick", "default-password-abc")


def _reset_auth_state():
    with app._admin_hash_lock:
        app._admin_hash_cache.update(value=None, loaded_at=0.0, ever_loaded=False)
    with app._login_failures_lock:
        app._login_failures.clear()
    client.cookies.clear()


@pytest.fixture(autouse=True)
def _admin_creds():
    # Every test module in this suite reloads `app` at collection time, so
    # module-level credential assignments are clobbered by whichever file
    # imports last. Set (and restore) them per-test instead.
    orig_user, orig_pass = app.ADMIN_USERNAME, app.ADMIN_PASSWORD
    app.ADMIN_USERNAME = "nick"
    app.ADMIN_PASSWORD = "default-password-abc"
    _reset_auth_state()
    yield
    app.ADMIN_USERNAME, app.ADMIN_PASSWORD = orig_user, orig_pass
    _reset_auth_state()


# ── A) Basic auth compatibility ─────────────────────────────────────────────
def test_basic_auth_still_works_for_api_callers():
    _reset_auth_state()
    response = client.get("/api/metrics", auth=BASIC_OK)
    assert response.status_code == 200
    response = client.get("/api/metrics", auth=("nick", "wrong"))
    assert response.status_code == 401


# ── B) Browser redirect instead of popup ────────────────────────────────────
def test_admin_without_auth_redirects_browser_to_login():
    _reset_auth_state()
    response = client.get(
        "/admin",
        headers={"accept": "text/html,application/xhtml+xml"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_api_without_auth_gets_401_not_redirect():
    _reset_auth_state()
    response = client.get("/api/metrics", follow_redirects=False)
    assert response.status_code == 401
    # No Basic challenge header for cookie-less API callers → browsers that hit
    # an API URL directly never see the legacy popup.
    assert "www-authenticate" not in {k.lower() for k in response.headers}


# ── C) Login flow ────────────────────────────────────────────────────────────
def test_login_page_renders_and_login_flow_sets_cookie():
    _reset_auth_state()
    page = client.get("/login")
    assert page.status_code == 200
    assert "realti" in page.text and "Sign in" in page.text

    response = client.post(
        "/login",
        content="username=nick&password=default-password-abc",
        headers={"content-type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin"
    assert app.ADMIN_SESSION_COOKIE in response.cookies

    admin = client.get("/admin", headers={"accept": "text/html"})
    assert admin.status_code == 200
    assert "realti" in admin.text

    # Logged-in browsers get bounced away from /login back to the console.
    bounce = client.get("/login", follow_redirects=False)
    assert bounce.status_code == 303
    assert bounce.headers["location"] == "/admin"

    # Sign-out is POST-only: a GET logout is triggerable cross-site by any <img>.
    assert client.get("/logout", follow_redirects=False).status_code == 405
    out = client.post("/logout", follow_redirects=False)
    assert out.status_code == 303
    after = client.get("/admin", headers={"accept": "text/html"}, follow_redirects=False)
    assert after.status_code == 303


# ── D) Wrong login + rate limit ──────────────────────────────────────────────
def test_wrong_login_rejected_then_rate_limited():
    _reset_auth_state()
    for _ in range(app.LOGIN_MAX_ATTEMPTS):
        response = client.post(
            "/login",
            content="username=nick&password=nope",
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
        assert response.status_code == 401
        assert "Wrong username or password" in response.text
    response = client.post(
        "/login",
        content="username=nick&password=default-password-abc",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 429


# ── E) Change password ───────────────────────────────────────────────────────
def _fake_settings_store(store):
    def fake_supabase_request(method, table, *, params=None, json_body=None, prefer=None):
        assert table == app.SUPABASE_TABLES["settings"]
        if method == "GET":
            value = store.get(app.ADMIN_SETTINGS_PASSWORD_KEY)
            return [{"value": value}] if value else []
        if method == "POST":
            store[json_body["key"]] = json_body["value"]
            return None
        raise AssertionError(f"unexpected method {method}")
    return fake_supabase_request


def test_change_password_flow(monkeypatch):
    _reset_auth_state()
    store = {}
    monkeypatch.setattr(app, "supabase_enabled", lambda: True)
    monkeypatch.setattr(app, "supabase_request", _fake_settings_store(store))

    # Sign in with the env default and grab the session cookie.
    login = client.post(
        "/login",
        content="username=nick&password=default-password-abc",
        headers={"content-type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert login.status_code == 303
    old_cookie = login.cookies[app.ADMIN_SESSION_COOKIE]

    # Reject: wrong current password / too-short new password.
    response = client.post(
        "/api/admin/change-password",
        json={"current_password": "nope", "new_password": "long-enough-secret"},
    )
    assert response.status_code == 403
    response = client.post(
        "/api/admin/change-password",
        json={"current_password": "default-password-abc", "new_password": "short"},
    )
    assert response.status_code == 422

    # Accept a real change.
    response = client.post(
        "/api/admin/change-password",
        json={"current_password": "default-password-abc", "new_password": "brand-new-password-1"},
    )
    assert response.status_code == 200 and response.json()["ok"] is True
    assert store[app.ADMIN_SETTINGS_PASSWORD_KEY].startswith("pbkdf2_sha256$")
    new_cookie = response.cookies[app.ADMIN_SESSION_COOKIE]
    assert new_cookie != old_cookie

    # The env default is dead; the new password works — for Basic auth too.
    assert client.get("/api/metrics", auth=("nick", "default-password-abc")).status_code == 401
    assert client.get("/api/metrics", auth=("nick", "brand-new-password-1")).status_code == 200

    # Old session cookie died with the secret rotation; the fresh one works.
    client.cookies.clear()
    client.cookies.set(app.ADMIN_SESSION_COOKIE, old_cookie)
    stale = client.get("/admin", headers={"accept": "text/html"}, follow_redirects=False)
    assert stale.status_code == 303
    client.cookies.clear()
    client.cookies.set(app.ADMIN_SESSION_COOKIE, new_cookie)
    fresh = client.get("/admin", headers={"accept": "text/html"})
    assert fresh.status_code == 200

    # Login page now only accepts the new password.
    client.cookies.clear()
    bad = client.post(
        "/login",
        content="username=nick&password=default-password-abc",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert bad.status_code == 401
    good = client.post(
        "/login",
        content="username=nick&password=brand-new-password-1",
        headers={"content-type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert good.status_code == 303


# ── 2026-08-17 security audit regressions ───────────────────────────────────
def test_basic_auth_is_rate_limited_like_the_login_form():
    """Every admin route accepted Basic auth with no throttle, so `curl -u` against
    /admin was an unlimited password oracle while /login enforced a lockout."""
    for _ in range(app.LOGIN_MAX_ATTEMPTS):
        assert client.get("/api/metrics", auth=("nick", "wrong")).status_code == 401
    assert client.get("/api/metrics", auth=("nick", "wrong")).status_code == 429
    # The correct password is refused too while the lockout holds (fail closed).
    assert client.get("/api/metrics", auth=BASIC_OK).status_code == 429


def test_login_does_not_short_circuit_on_username(monkeypatch):
    """A wrong username must still run the password check. Short-circuiting made a
    bad username answer measurably faster (no PBKDF2), leaking valid usernames."""
    _reset_auth_state()
    calls = []
    real_verify = app.verify_admin_password
    monkeypatch.setattr(
        app,
        "verify_admin_password",
        lambda pw: (calls.append(pw), real_verify(pw))[1],
    )

    client.post(
        "/login",
        content="username=definitely-not-the-admin&password=whatever",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert calls == ["whatever"], "password check was skipped for a wrong username"

    calls.clear()
    client.get("/api/metrics", auth=("definitely-not-the-admin", "whatever"))
    assert calls == ["whatever"], "Basic-auth path skipped the password check"


def test_oversized_login_body_is_rejected_without_parsing():
    _reset_auth_state()
    huge = "username=nick&password=" + ("x" * 20000)
    response = client.post(
        "/login",
        content=huge,
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 401


def test_public_health_does_not_leak_supabase_error_text():
    body = client.get("/api/health").json()
    assert "supabase_last_error" not in body
    assert "supabase_error_present" in body
    assert "review_hosted_by_ai_sprints" not in body


def test_security_headers_cover_the_auth_pages():
    for path in ("/login", "/admin"):
        headers = client.get(path, headers={"accept": "text/html"}, follow_redirects=False).headers
        assert headers.get("X-Frame-Options") == "DENY"
        assert "frame-ancestors 'none'" in headers.get("Content-Security-Policy", "")
        assert "max-age=" in headers.get("Strict-Transport-Security", "")


def test_momence_callback_escapes_reflected_error():
    """?error=<script> was reflected raw into an admin-authenticated page."""
    response = client.get(
        "/api/momence/oauth/callback",
        params={"error": "<script>alert(1)</script>"},
        auth=BASIC_OK,
    )
    assert response.status_code == 200
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;" in response.text


def test_html_escape_covers_quotes():
    assert app._html_escape('a"b') == "a&quot;b"
    assert app._html_escape("a'b") == "a&#39;b"


def test_password_state_unavailable_fails_closed(monkeypatch):
    """Cold process + unreachable settings store must NOT fall back to the env
    default password (that would resurrect the credential the owner retired)."""
    _reset_auth_state()

    def boom(*a, **k):
        raise RuntimeError("supabase down")

    monkeypatch.setattr(app, "supabase_enabled", lambda: True)
    monkeypatch.setattr(app, "supabase_request", boom)

    import pytest as _pytest
    with _pytest.raises(app.PasswordStateUnavailable):
        app.verify_admin_password("default-password-abc")

    # Over HTTP that surfaces as 503, never as a successful auth.
    assert client.get("/api/metrics", auth=BASIC_OK).status_code == 503
    assert client.post(
        "/login",
        content="username=nick&password=default-password-abc",
        headers={"content-type": "application/x-www-form-urlencoded"},
    ).status_code == 503
    # And a cookie can't be validated either, so no session slips through.
    assert app.session_token_valid("9999999999.deadbeef") is False


def test_logout_rotates_the_session_epoch(monkeypatch):
    """Sign-out must invalidate outstanding cookies, not just clear the local one."""
    _reset_auth_state()
    store = {}

    def fake_request(method, table, *, params=None, json_body=None, prefer=None):
        assert table == app.SUPABASE_TABLES["settings"]
        if method == "GET":
            return [{"key": k, "value": v} for k, v in store.items()]
        if method == "POST":
            store[json_body["key"]] = json_body["value"]
            return None
        raise AssertionError(method)

    monkeypatch.setattr(app, "supabase_enabled", lambda: True)
    monkeypatch.setattr(app, "supabase_request", fake_request)

    login = client.post(
        "/login",
        content="username=nick&password=default-password-abc",
        headers={"content-type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    token = login.cookies[app.ADMIN_SESSION_COOKIE]
    assert app.session_token_valid(token) is True

    client.post("/logout", follow_redirects=False)
    assert store[app.ADMIN_SETTINGS_EPOCH_KEY]
    # The captured cookie is now worthless even though it has not expired.
    assert app.session_token_valid(token) is False
