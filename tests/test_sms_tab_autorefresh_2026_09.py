"""SMS tab + dashboard auto-refresh (27 Sep 2026).

Before this, a text to the business number was emailed to Nick and recorded
as an event holding only the sender and the LENGTH, so the dashboard had
nowhere to show it ("where is it supposed to show?"). Now the event carries
the words and an SMS tab lists them by number. Separately, the dashboard polls
/api/admin/snapshot for the active tab instead of waiting for a manual reload.

Nothing here goes through a real Twilio or email path: the webhook's email is
captured by a stub, and the owner-alert behaviour is pinned exactly as it was.
"""
import os
import re
import sys
import tempfile
import time
import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

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

client = TestClient(app.app)
AUTH = ("u", "p")


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "SUPABASE_URL", "")
    monkeypatch.setattr(app, "SUPABASE_KEY", None)
    for name, filename, empty in [
        ("CONVERSATION_LOG_FILE", "conversation_logs.jsonl", ""),
        ("EVENTS_FILE", "events.jsonl", ""),
        ("LEADS_FILE", "leads.json", "[]"),
    ]:
        path = tmp_path / filename
        path.write_text(empty)
        monkeypatch.setattr(app, name, path)
    monkeypatch.setattr(app, "TWILIO_AUTH_TOKEN", "test-token")
    monkeypatch.setattr(app, "twilio_signature_valid", lambda *a: True)
    monkeypatch.setattr(app, "ADMIN_USERNAME", "u")
    monkeypatch.setattr(app, "ADMIN_PASSWORD", "p")
    monkeypatch.setattr(app, "get_admin_password_hash", lambda: None)
    monkeypatch.setattr(app, "LEAD_SUMMARY_EMAIL_TO", "owner@example.com")
    sent = []
    monkeypatch.setattr(app, "send_email_resend",
                        lambda subject, body, recipients, html=None: sent.append((subject, body, recipients)) or True)
    yield sent


def _wait_for(sent, n=1):
    for _ in range(60):
        if len(sent) >= n:
            return
        time.sleep(0.05)


def _text(sender, body, **extra):
    r = client.post("/twilio-sms-webhook", data={"From": sender, "Body": body, **extra})
    assert r.status_code == 200
    assert "<Message>" not in r.text          # still no auto-reply SMS
    return r


# ── Persistence ────────────────────────────────────────────────────────────

def test_inbound_text_is_stored_with_its_words(_isolated):
    _text("+61411222333", "Hi, is the 6am class on tomorrow?")
    rows = app.read_sms_inbound()
    assert len(rows) == 1
    assert rows[0]["sender"] == "+61411222333"
    assert rows[0]["body"] == "Hi, is the 6am class on tomorrow?"
    assert rows[0]["length"] == len("Hi, is the 6am class on tomorrow?")


def test_owner_email_is_unchanged(_isolated):
    sent = _isolated
    _text("+61411222333", "hola, is there a class today?")
    _wait_for(sent)
    subject, body, recipients = sent[0]
    assert subject == "Text message to the business number from +61411222333"
    assert body.startswith("+61411222333 texted the Outdoor Squad number:\n\nhola, is there a class today?\n\n")
    assert "this number does not auto-reply" in body
    assert recipients == ["owner@example.com"]


def test_callback_number_in_the_text_is_not_redacted(_isolated):
    # The chat transcripts redact numbers; this store must not, or the number
    # a texter leaves for a callback disappears from the tab.
    _text("+61411222333", "call me on 0412 345 678 please")
    assert app.read_sms_inbound()[0]["body"] == "call me on 0412 345 678 please"


def test_picture_message_records_media_count(_isolated):
    _text("+61411222333", "", NumMedia="1")
    payload = app.sms_dashboard_payload()
    msg = payload["threads"][0]["messages"][0]
    assert msg["body"] == "" and msg["media"] == 1


def test_body_is_capped_like_the_email(_isolated):
    _text("+61411222333", "x" * 2000)
    assert len(app.read_sms_inbound()[0]["body"]) == 800


# ── Payload shape ──────────────────────────────────────────────────────────

def test_payload_groups_by_sender_most_recent_first(monkeypatch):
    rows = [
        {"timestamp": "2026-09-27T10:00:00", "sender": "+61400000001", "body": "first from one", "length": 14},
        {"timestamp": "2026-09-27T10:05:00", "sender": "+61400000002", "body": "only from two", "length": 13},
        {"timestamp": "2026-09-27T10:10:00", "sender": "+61400000001", "body": "second from one", "length": 15},
    ]
    monkeypatch.setattr(app, "read_sms_inbound", lambda limit=500: rows)
    payload = app.sms_dashboard_payload()
    assert [t["sender"] for t in payload["threads"]] == ["+61400000001", "+61400000002"]
    one = payload["threads"][0]
    assert one["message_count"] == 2
    assert one["last_at"] == "2026-09-27T10:10:00"
    # Inside a thread the texts read top to bottom, oldest first, like a chat.
    assert [m["body"] for m in one["messages"]] == ["first from one", "second from one"]
    assert payload["message_count"] == 3


def test_pre_27_sep_rows_say_the_words_were_never_stored(monkeypatch):
    rows = [
        {"timestamp": "2026-09-20T10:00:00", "sender": "+61400000001", "length": 42},
        {"timestamp": "2026-09-27T10:00:00", "sender": "+61400000001", "length": 0},
    ]
    monkeypatch.setattr(app, "read_sms_inbound", lambda limit=500: rows)
    old, empty = app.sms_dashboard_payload()["threads"][0]["messages"]
    assert old["body"] is None and old["length"] == 42
    assert empty["body"] == ""


def test_supabase_read_asks_only_for_sms_events(monkeypatch):
    calls = []

    def fake_request(method, table, *, params=None, json_body=None, prefer=None):
        calls.append((method, table, params))
        return [
            {"timestamp": "2026-09-27T10:10:00", "metadata": {"sender": "+61400000001", "body": "b", "length": 1}},
            {"timestamp": "2026-09-27T10:00:00", "metadata": {"sender": "+61400000001", "body": "a", "length": 1}},
        ]

    monkeypatch.setattr(app, "SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr(app, "SUPABASE_KEY", "k")
    monkeypatch.setattr(app, "supabase_request", fake_request)
    rows = app.read_sms_inbound()
    method, table, params = calls[0]
    assert (method, table) == ("GET", "outdoor_squad_events")
    assert params["event_type"] == "eq.sms_inbound"
    assert [r["body"] for r in rows] == ["a", "b"]      # oldest first


# ── Admin endpoints ────────────────────────────────────────────────────────

def test_sms_endpoint_is_admin_only(_isolated):
    assert client.get("/api/sms/messages").status_code == 401
    _text("+61411222333", "hello")
    r = client.get("/api/sms/messages", auth=AUTH)
    assert r.status_code == 200
    assert r.json()["threads"][0]["messages"][0]["body"] == "hello"


def test_snapshot_returns_only_the_requested_parts(_isolated):
    assert client.get("/api/admin/snapshot?parts=sms").status_code == 401
    _text("+61411222333", "hello")
    r = client.get("/api/admin/snapshot?parts=sms,leads,bogus,sms", auth=AUTH)
    assert r.status_code == 200
    assert set(r.json()) == {"sms", "leads"}
    assert r.json()["sms"]["threads"][0]["sender"] == "+61411222333"
    assert "no-store" in r.headers.get("cache-control", "")


def test_snapshot_parts_match_what_each_tab_polls():
    # Every part the page asks for must exist server-side, or a tab would poll
    # an empty object forever and look frozen.
    script = app.ADMIN_HTML
    block = re.search(r"const TAB_PARTS = \{(.*?)\};", script, re.S).group(1)
    asked = set(re.findall(r"'([a-z]+)'", block))
    assert asked <= set(app.ADMIN_SNAPSHOT_PARTS)
    tabs = set(re.findall(r"^\s*([a-z]+):", block, re.M))
    assert tabs == {"overview", "leads", "website", "whatsapp", "sms"}


def test_admin_page_has_the_sms_tab_and_data(_isolated):
    _text("+61411222333", "</script><script>alert(1)</script>")
    r = client.get("/admin", auth=AUTH)
    assert r.status_code == 200
    assert 'data-tab="sms"' in r.text and 'data-panel="sms"' in r.text
    assert 'id="smsCount"' in r.text
    assert "reply from your own phone" in r.text
    # The texter's words are inside the inline data, escaped (stored XSS guard).
    assert "</script><script>alert(1)" not in r.text


# ── Auto-refresh safeguards (static: there is no browser in this suite) ────

def test_poll_interval_is_between_five_and_ten_seconds():
    ms = int(re.search(r"const POLL_MS = (\d+);", app.ADMIN_HTML).group(1))
    assert 5000 <= ms <= 10000


def test_polling_pauses_when_the_browser_tab_is_hidden():
    html = app.ADMIN_HTML
    assert "visibilitychange" in html
    assert "document.visibilityState === 'hidden'" in html
    # Chained timeouts, never stacked requests.
    assert "setInterval(" not in html


def test_nothing_but_a_successful_send_clears_the_reply_box():
    html = app.ADMIN_HTML
    # One write to the reply input in the whole page, and it sits after the
    # server said yes.
    assert len(re.findall(r"\binput\.value\s*=", html)) == 1
    handler = html[html.index("getElementById('waReplyForm').addEventListener"):]
    assert handler.index("if (!res.ok)") < handler.index("input.value = ''")


def test_refresh_of_the_same_thread_keeps_the_reply_box_usable():
    detail = app.ADMIN_HTML[app.ADMIN_HTML.index("function renderWaDetail()"):]
    detail = detail[:detail.index("function waWorse")]
    assert "sameThread" in detail
    assert "document.activeElement === input" in detail
    assert "if (!sameThread) { note.textContent = ''" in detail


def test_no_dashes_in_the_new_copy():
    start = app.ADMIN_HTML.index('<section class="panel" data-panel="sms">')
    panel = app.ADMIN_HTML[start:app.ADMIN_HTML.index("</section>", start)]
    js = app.ADMIN_HTML[app.ADMIN_HTML.index("// ── SMS ──"):app.ADMIN_HTML.index("function notifData()")]
    for chunk in (panel, js):
        assert "—" not in chunk and "–" not in chunk


def test_overview_tiles_poll_slower_than_the_messages():
    # build_metrics_payload reads up to 5000 events (about 3s live); polling it
    # every 8s would keep a worker busy nearly full time for numbers that are
    # all-time totals.
    ms = int(re.search(r"const PART_MIN_MS = \{ metrics: (\d+) \};", app.ADMIN_HTML).group(1))
    assert ms >= 30000
