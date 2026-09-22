"""Verbose notification mode (Nicholas, 22 Sep 2026).

Nick's precondition for switching the published number: every new
conversation, every failed send, a daily digest, and a dial he can turn down.
Built over signals the bot already produces, on the existing owner email
channel only. These tests pin:
  1) Standard (the default) is byte-for-byte the pre-existing behaviour:
     nothing new fires while the dial is off.
  2) The dial persists through the settings store and the admin endpoints,
     with the same fail-closed 503 as the kill switch.
  3) New-conversation alerts respect the trust bar (signed widget, no internal
     QA), the QA exclusion list, per-session dedupe and the hourly cap.
  4) Failed-send alerts fire from both a Twilio refusal and a delivery
     receipt, once per thread per hour.
  5) The daily digest is idempotent per day, only fires while enabled, and
     the dry-run endpoint sends nothing.
"""
import base64
import importlib
import json
import os
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _k in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_KEY",
           "OUTDOOR_SQUAD_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY",
           "OUTDOOR_SQUAD_OPENAI_API_KEY", "OPENAI_API_KEY",
           "OUTDOOR_SQUAD_GEMINI_API_KEY", "GEMINI_API_KEY",
           "OUTDOOR_SQUAD_RESEND_API_KEY", "OUTDOOR_SQUAD_REPORT_EMAIL_TO"):
    os.environ.pop(_k, None)

import app  # noqa: E402

importlib.reload(app)
app.SUPABASE_URL = ""
app.SUPABASE_KEY = None

AUTH = {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}


@pytest.fixture()
def env(monkeypatch, tmp_path):
    """Isolated files, captured Resend transport, synchronous threads."""
    for name, filename, empty in [
        ("CONVERSATION_LOG_FILE", "conversation_logs.jsonl", ""),
        ("EVENTS_FILE", "events.jsonl", ""),
        ("LEADS_FILE", "leads.json", "[]"),
        ("HUMAN_REQUEST_CLAIMS_FILE", "claims.jsonl", ""),
    ]:
        path = tmp_path / filename
        path.write_text(empty)
        monkeypatch.setattr(app, name, path)
    monkeypatch.setattr(app, "HUMAN_REQUEST_CLAIMS_LOCK_FILE", tmp_path / "claims.lock")
    monkeypatch.setattr(app, "WA_STATE_FILE", tmp_path / "wa_state.json")
    monkeypatch.setattr(app, "ADMIN_USERNAME", "u")
    monkeypatch.setattr(app, "ADMIN_PASSWORD", "p")
    monkeypatch.setattr(app, "LEAD_SUMMARY_EMAIL_TO", "owner@example.com")
    monkeypatch.setattr(app, "LEAD_SUMMARY_RESEND_API_KEY", "stub")
    monkeypatch.setattr(app, "LEAD_SUMMARY_EMAIL_FROM", "bot@example.com")
    monkeypatch.setattr(app, "REPORT_EMAIL_TO", "")
    monkeypatch.setattr(app, "VERBOSE_ALERTS_MAX_PER_HOUR", 20)
    app._wa_setting_cache.clear()
    app._verbose_alert_times.clear()
    app._verbose_alert_seen.clear()
    app._report_excluded_cache.clear()
    app.conversations.clear()

    sent = []

    def fake_resend(subject, body, recipients, html=None):
        sent.append({"subject": subject, "body": body, "to": list(recipients), "html": html})
        return True

    monkeypatch.setattr(app, "send_email_resend", fake_resend)
    # Threads run inline so assertions are deterministic.
    class _Inline:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None, name=None):
            self._t, self._a, self._k = target, args, kwargs or {}
        def start(self):
            self._t(*self._a, **self._k)
    monkeypatch.setattr(app.threading, "Thread", _Inline)
    yield sent
    app._wa_setting_cache.clear()


def events(kind=None):
    rows = [json.loads(l) for l in app.EVENTS_FILE.read_text().splitlines() if l.strip()]
    return [r for r in rows if kind is None or r.get("event_type") == kind]


# ── 1. The dial ──────────────────────────────────────────────────────────────

def test_default_is_standard_and_nothing_new_fires(env):
    assert app.verbose_signals() == frozenset()
    assert app.notification_settings_payload()["level"] == "standard"
    assert app.notify_new_conversation("s-abc", "hi", "website") is False
    assert app.notify_failed_send("wa-61400000001", "reply", "failed", "63016", "send") is False
    assert app._daily_digest_due("2026-09-22") is False
    assert env == []
    assert events("verbose_alert_sent") == []


def test_dial_persists_and_round_trips_through_the_api(env):
    client = TestClient(app.app)
    r = client.post("/api/notifications", json={"level": "verbose"}, headers=AUTH)
    assert r.status_code == 200
    assert r.json()["level"] == "verbose"
    assert all(r.json()["signals"].values())
    app._wa_setting_cache.clear()
    assert app.verbose_signals() == frozenset(app.NOTIFY_SIGNALS)

    # Turn it down one notch: keep conversations and the digest, drop failed sends.
    r = client.post("/api/notifications", json={"level": "verbose", "signals": ["conversations", "daily_digest"]}, headers=AUTH)
    assert r.status_code == 200
    app._wa_setting_cache.clear()
    assert app.verbose_signals() == frozenset({"conversations", "daily_digest"})
    assert app.get_wa_setting(app.NOTIFY_SETTING_KEY) == "conversations,daily_digest"

    # Verbose with nothing ticked is Standard, stored honestly as such.
    r = client.post("/api/notifications", json={"level": "verbose", "signals": []}, headers=AUTH)
    assert r.status_code == 200 and r.json()["level"] == "standard"
    app._wa_setting_cache.clear()
    assert app.verbose_signals() == frozenset()

    r = client.get("/api/notifications", headers=AUTH)
    assert r.status_code == 200 and r.json()["level"] == "standard"
    assert events("notification_mode_changed")


def test_dial_rejects_bad_input_and_needs_auth(env):
    client = TestClient(app.app)
    assert client.post("/api/notifications", json={"level": "verbose"}).status_code == 401
    assert client.get("/api/notifications").status_code == 401
    assert client.post("/api/notifications", json={"level": "loud"}, headers=AUTH).status_code == 400
    assert client.post("/api/notifications", json={"level": "verbose", "signals": "all"}, headers=AUTH).status_code == 400
    r = client.post("/api/notifications", json={"level": "verbose", "signals": ["missed_calls", "conversations"]}, headers=AUTH)
    assert r.status_code == 200
    assert r.json()["signals"] == {"conversations": True, "failed_sends": False, "daily_digest": False}


def test_dial_fails_closed_when_nothing_persists(env, monkeypatch):
    monkeypatch.setattr(app, "set_wa_setting", lambda k, v: False)
    client = TestClient(app.app)
    r = client.post("/api/notifications", json={"level": "verbose"}, headers=AUTH)
    assert r.status_code == 503 and r.json()["ok"] is False


def test_admin_dashboard_carries_the_dial(env):
    client = TestClient(app.app)
    app.set_verbose_signals(["conversations"])
    app._wa_setting_cache.clear()
    r = client.get("/admin", headers=AUTH)
    assert r.status_code == 200
    assert 'id="notifLevel"' in r.text and 'id="notifSigConversations"' in r.text
    assert '"notifications": {' in r.text and '"level": "verbose"' in r.text


def test_entry_points_toggle_drives_the_report_status_line(env):
    client = TestClient(app.app)
    assert "not yet publicly reachable" in app.wa_channel_status_line({"wa_channel_enabled": True, "wa_entry_points_live": False})
    r = client.post("/api/wa/entry-points", json={"live": True}, headers=AUTH)
    assert r.status_code == 200 and r.json()["live"] is True
    app._wa_setting_cache.clear()
    stats = app.build_report_stats(days=7)
    assert stats["wa_entry_points_live"] is True
    assert app.wa_channel_status_line(stats).startswith("LIVE and publicly reachable")
    assert client.post("/api/wa/entry-points", json={"live": True}).status_code == 401


# ── 2. New conversation alerts ───────────────────────────────────────────────

def test_new_conversation_alert_fires_once_per_session(env):
    app.set_verbose_signals(["conversations"])
    app._wa_setting_cache.clear()
    assert app.notify_new_conversation("s-abc", "hi, what time is the 6am?", "website") is True
    assert app.notify_new_conversation("s-abc", "hi again", "website") is False
    assert len(env) == 1
    mail = env[0]
    assert mail["to"] == ["owner@example.com"]
    assert mail["subject"] == "New Website conversation started"
    assert "what time is the 6am" in mail["body"]
    assert "Verbose" in mail["html"]
    assert "—" not in mail["body"] and "—" not in mail["html"]
    assert [e["reason"] for e in events("verbose_alert_skipped")] == ["duplicate"]


def test_whatsapp_returning_episode_gets_its_own_wording(env):
    app.set_verbose_signals(["conversations"])
    app._wa_setting_cache.clear()
    assert app.notify_new_conversation("wa-61400000001", "hi", "whatsapp", episode=2) is True
    assert env[0]["subject"] == "Returning WhatsApp customer started a new conversation"
    assert "WhatsApp thread: 61400000001" in env[0]["body"]


def test_excluded_qa_sessions_never_alert(env, monkeypatch):
    app.set_verbose_signals(["conversations", "failed_sends"])
    app._wa_setting_cache.clear()
    monkeypatch.setattr(app, "report_excluded_session_ids", lambda: frozenset({"wa-61400000009"}))
    assert app.notify_new_conversation("wa-61400000009", "hi", "whatsapp", episode=1) is False
    assert app.notify_failed_send("wa-61400000009", "reply", "failed", "63016", "send") is False
    assert env == []
    assert {e["reason"] for e in events("verbose_alert_skipped")} == {"excluded_session"}


def test_hourly_cap_stops_a_storm(env, monkeypatch):
    monkeypatch.setattr(app, "VERBOSE_ALERTS_MAX_PER_HOUR", 3)
    app.set_verbose_signals(["conversations"])
    app._wa_setting_cache.clear()
    results = [app.notify_new_conversation(f"s-{i}", "hi", "website") for i in range(6)]
    assert results == [True, True, True, False, False, False]
    assert len(env) == 3
    assert [e["reason"] for e in events("verbose_alert_skipped")] == ["rate_limited"] * 3


def test_widget_chat_alerts_only_trusted_non_qa_first_messages(env, monkeypatch):
    app.set_verbose_signals(["conversations"])
    app._wa_setting_cache.clear()
    monkeypatch.setattr(app, "WIDGET_SIGNING_KEY", "test-signing-key")
    monkeypatch.setattr(app, "verify_turnstile_token", lambda *a, **k: True)
    monkeypatch.setattr(app, "generate_ai_reply", lambda m, s: ("Sure thing.", "test"))
    monkeypatch.setattr(app, "is_rate_limited", lambda *a, **k: False)
    client = TestClient(app.app)

    # Unsigned caller: no alert.
    r = client.post("/api/chat", json={"message": "hi there", "session_id": "widget-unsigned1"})
    assert r.status_code == 200
    assert env == []

    # Signed widget session: one alert on the first message, none on the second.
    sid, token = app.mint_widget_session()
    r = client.post("/api/chat", json={"message": "hi, is the trial free?", "session_id": sid, "widget_token": token})
    assert r.status_code == 200
    assert len(env) == 1 and "is the trial free" in env[0]["body"]
    r = client.post("/api/chat", json={"message": "and where?", "session_id": sid, "widget_token": token})
    assert r.status_code == 200
    assert len(env) == 1

    # Internal QA: never Nick's business.
    monkeypatch.setattr(app, "INTERNAL_QA_TOKEN", "qa-secret")
    sid2, token2 = app.mint_widget_session()
    r = client.post("/api/chat", json={"message": "qa probe", "session_id": sid2, "widget_token": token2,
                                       "internal_qa": True, "qa_token": "qa-secret"})
    assert r.status_code == 200
    assert len(env) == 1


def test_widget_chat_in_standard_mode_sends_nothing(env, monkeypatch):
    monkeypatch.setattr(app, "WIDGET_SIGNING_KEY", "test-signing-key")
    monkeypatch.setattr(app, "verify_turnstile_token", lambda *a, **k: True)
    monkeypatch.setattr(app, "generate_ai_reply", lambda m, s: ("Sure thing.", "test"))
    monkeypatch.setattr(app, "is_rate_limited", lambda *a, **k: False)
    client = TestClient(app.app)
    sid, token = app.mint_widget_session()
    r = client.post("/api/chat", json={"message": "hi", "session_id": sid, "widget_token": token})
    assert r.status_code == 200
    assert env == []
    assert events("verbose_alert_sent") == [] and events("verbose_alert_skipped") == []


# ── 3. Failed sends ──────────────────────────────────────────────────────────

def test_failed_send_alert_from_twilio_refusal_and_receipt_once_per_hour(env, monkeypatch):
    app.set_verbose_signals(["failed_sends"])
    app._wa_setting_cache.clear()
    monkeypatch.setattr(app, "WA_SEND_RETRY_SECONDS", 0.0)
    monkeypatch.setattr(app, "send_whatsapp_via_twilio",
                        lambda to, body: (False, 'HTTP 400: {"code": 63016, "message": "outside window"}'))
    sid = "wa-61400000002"
    outcome, _detail, parts, _attempts = app._wa_send_and_register(sid, "61400000002", "Here are the times.", "reply")
    assert outcome == "failed" and parts == 0
    assert len(env) == 1
    assert env[0]["subject"] == "WhatsApp message not delivered"
    assert "a reply, Twilio refused the message" in env[0]["body"]
    assert "63016" in env[0]["body"]

    # A receipt for the same thread inside the hour is folded, not re-emailed.
    app._wa_apply_delivery_receipt(sid, "SMxyz", "undelivered", "63024")
    assert len(env) == 1
    assert [e["reason"] for e in events("verbose_alert_skipped")] == ["duplicate"]

    # A different thread's receipt is its own alert.
    app._wa_apply_delivery_receipt("wa-61400000003", "SMabc", "failed", "63032")
    assert len(env) == 2
    assert "WhatsApp reported it as failed" in env[1]["body"]
    assert "63032" in env[1]["body"]
    assert "WhatsApp thread: 61400000003" in env[1]["body"]
    assert "Reported by: delivery receipt" in env[1]["body"]


def test_failed_send_in_standard_mode_changes_nothing(env, monkeypatch):
    monkeypatch.setattr(app, "WA_SEND_RETRY_SECONDS", 0.0)
    monkeypatch.setattr(app, "send_whatsapp_via_twilio", lambda to, body: (False, "HTTP 400: 63016 outside window"))
    sid = "wa-61400000004"
    outcome, _d, _p, _a = app._wa_send_and_register(sid, "61400000004", "Hello.", "reply")
    assert outcome == "failed"
    app._wa_apply_delivery_receipt(sid, "SMdef", "undelivered", "63016")
    assert env == []
    # The pre-existing ledger and flags still behave exactly as before.
    assert app.get_wa_setting(f"undelivered:{app.wa_episode_key(sid)}") == "1"
    assert events("wa_reply_undelivered")


def test_delivered_receipt_never_alerts(env):
    app.set_verbose_signals(["failed_sends"])
    app._wa_setting_cache.clear()
    app._wa_apply_delivery_receipt("wa-61400000005", "SMok", "delivered", "")
    assert env == []


# ── 4. Daily digest ──────────────────────────────────────────────────────────

def test_daily_digest_only_when_enabled_and_once_per_day(env):
    assert app._daily_digest_due("2026-09-22") is False
    app.set_verbose_signals(["daily_digest"])
    app._wa_setting_cache.clear()
    assert app._daily_digest_due("2026-09-22") is True
    app.set_wa_setting("digest_last_sent", "2026-09-22")
    assert app._daily_digest_due("2026-09-22") is False
    assert app._daily_digest_due("2026-09-23") is True


def test_daily_digest_uses_one_day_window_and_daily_wording(env):
    result = app.send_daily_digest()
    assert result["sent_email"] is True
    assert result["stats"]["window_days"] == 1
    mail = env[0]
    assert mail["subject"].startswith("Robo-Nick daily digest, ")
    assert mail["to"] == ["owner@example.com"]
    assert "Robo-Nick stats, last 1 days" in mail["body"]
    assert "Daily digest" in mail["html"] and "Weekly report" not in mail["html"]
    assert "—" not in mail["html"]
    assert events("daily_digest_sent")
    # The weekly report is untouched.
    assert "Weekly report" in app.format_report_html(app.build_report_stats(days=7))


def test_daily_digest_endpoint_dry_run_sends_nothing(env):
    client = TestClient(app.app)
    assert client.get("/api/reports/daily").status_code == 401
    r = client.get("/api/reports/daily", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["sent_email"] is False
    assert r.json()["stats"]["window_days"] == 1
    assert "Robo-Nick stats, last 1 days" in r.json()["report_text"]
    assert env == []


def test_next_digest_time_is_strictly_future_same_hour_daily():
    tz = ZoneInfo("Australia/Sydney")
    before = datetime(2026, 9, 22, 9, 0, tzinfo=tz)
    target = app._next_digest_time(before)
    assert target.hour == app.DIGEST_HOUR and target.date() == before.date()
    after = datetime(2026, 9, 22, app.DIGEST_HOUR, 0, 0, tzinfo=tz)
    assert app._next_digest_time(after).date() == (after + timedelta(days=1)).date()


def test_digest_scheduler_only_starts_when_email_is_configured(monkeypatch):
    started = []
    monkeypatch.setattr(app.threading, "Thread", lambda **kw: type("T", (), {"start": lambda self: started.append(kw["name"])})())
    monkeypatch.setattr(app, "REPORT_EMAIL_TO", "")
    monkeypatch.setattr(app, "LEAD_SUMMARY_EMAIL_TO", "")
    monkeypatch.setattr(app, "LEAD_SUMMARY_RESEND_API_KEY", "")
    app._start_daily_digest_scheduler()
    assert started == []
    monkeypatch.setattr(app, "LEAD_SUMMARY_EMAIL_TO", "owner@example.com")
    monkeypatch.setattr(app, "LEAD_SUMMARY_RESEND_API_KEY", "stub")
    app._start_daily_digest_scheduler()
    assert started == ["daily-digest"]


# ── 5. Storm memory stays bounded ────────────────────────────────────────────

def test_seen_map_is_pruned_and_capped(env, monkeypatch):
    monkeypatch.setattr(app, "_VERBOSE_SEEN_CAP", 5)
    monkeypatch.setattr(app, "VERBOSE_ALERTS_MAX_PER_HOUR", 1000)
    for i in range(20):
        app._verbose_alert_admit(f"k{i}", 60)
    assert len(app._verbose_alert_seen) <= 6
    app._verbose_alert_seen["old"] = time.time() - app.VERBOSE_CONVERSATION_DEDUPE_SECONDS - 5
    app._verbose_alert_admit("fresh", 60)
    assert "old" not in app._verbose_alert_seen
