"""WhatsApp counters in the Monday report and on the dashboard (11 Sep 2026 diff, #20).

The report used to show four WhatsApp numbers and the dashboard counted
outcomes the report never mentioned, so the two disagreed. Both now read
wa_channel_counters over the same window.
"""
import base64
import importlib
import json
import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _k in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_KEY",
           "OUTDOOR_SQUAD_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY",
           "OUTDOOR_SQUAD_OPENAI_API_KEY", "OPENAI_API_KEY",
           "OUTDOOR_SQUAD_GEMINI_API_KEY", "GEMINI_API_KEY"):
    os.environ.pop(_k, None)

import app  # noqa: E402

importlib.reload(app)
app.SUPABASE_URL = ""
app.SUPABASE_KEY = None

from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(app.app)

A = "wa-61400555001"
B = "wa-61400555002"
C = "wa-61400555003"
ADMIN_PW = "wa-report-counters-pw"
AUTH = {"Authorization": "Basic " + base64.b64encode(
    ("outdoorsquad:" + ADMIN_PW).encode()).decode()}
EM_DASH = "\u2014"  # escaped: the repo forbids the literal character


def _ts(days_ago: float) -> str:
    return (datetime.now() - timedelta(days=days_ago)).isoformat()


@pytest.fixture(autouse=True)
def report_env(monkeypatch, tmp_path):
    """Own events/leads/logs, no Supabase, no cached counters between tests."""
    for name, filename, empty in [
        ("EVENTS_FILE", "events.jsonl", ""),
        ("LEADS_FILE", "leads.json", "[]"),
        ("CONVERSATION_LOG_FILE", "conversation_logs.jsonl", ""),
    ]:
        path = tmp_path / filename
        path.write_text(empty)
        monkeypatch.setattr(app, name, path)
    monkeypatch.setattr(app, "WA_STATE_FILE", tmp_path / "wa_state.json")
    monkeypatch.setattr(app, "SUPABASE_URL", "")
    monkeypatch.setattr(app, "SUPABASE_KEY", None)
    orig_user, orig_pass = app.ADMIN_USERNAME, app.ADMIN_PASSWORD
    app.ADMIN_USERNAME, app.ADMIN_PASSWORD = "outdoorsquad", ADMIN_PW
    with app._admin_hash_lock:
        app._admin_hash_cache.update(value=None, loaded_at=0.0, ever_loaded=False)
    with app._login_failures_lock:
        app._login_failures.clear()
    app._report_excluded_cache.clear()
    app._wa_counters_cache.clear()
    app._wa_setting_cache.clear()
    app.conversations.clear()
    yield
    app.ADMIN_USERNAME, app.ADMIN_PASSWORD = orig_user, orig_pass
    app._report_excluded_cache.clear()
    app._wa_counters_cache.clear()


def _seed(rows):
    app.EVENTS_FILE.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def _seed_wa():
    """Two live threads plus every kind of noise the counters must ignore."""
    rows = [
        # Thread A: came back after the gap, so two conversations on one thread.
        {"timestamp": _ts(3), "event_type": "conversation_started", "session_id": A, "episode": 1},
        {"timestamp": _ts(1), "event_type": "conversation_started", "session_id": A, "episode": 2},
        # Thread B: one conversation, episode unstamped (pre-11 Sep shape).
        {"timestamp": _ts(2), "event_type": "conversation_started", "session_id": B},
        # Thread C only ever had a message, never a conversation_started.
        {"timestamp": _ts(2), "event_type": "message_received", "session_id": C},
        {"timestamp": _ts(3), "event_type": "message_received", "session_id": A},
        {"timestamp": _ts(1), "event_type": "message_received", "session_id": A},
        {"timestamp": _ts(2), "event_type": "message_received", "session_id": B},
        {"timestamp": _ts(3), "event_type": "lead_captured", "session_id": A},
        {"timestamp": _ts(2), "event_type": "lead_captured", "session_id": B},
        # Human requests: A counts, B was skipped, the widget one is another channel.
        {"timestamp": _ts(3), "event_type": "human_handoff_requested", "session_id": A,
         "alert_eligible": True},
        {"timestamp": _ts(2), "event_type": "human_handoff_requested", "session_id": B,
         "alert_eligible": False},
        {"timestamp": _ts(2), "event_type": "human_handoff_requested", "session_id": "widget-zz",
         "alert_eligible": True},
        {"timestamp": _ts(3), "event_type": "lead_summary_notification_sent", "session_id": A,
         "reason": "explicit_human_request", "channels": "email"},
        {"timestamp": _ts(3), "event_type": "booking_link_shown", "session_id": A},
        {"timestamp": _ts(2), "event_type": "booking_link_shown", "session_id": B},
        {"timestamp": _ts(2), "event_type": "booking_link_shown", "session_id": B},
        {"timestamp": _ts(1), "event_type": "wa_nudge_sent", "session_id": A},
        # Nick typed twice: the first one WhatsApp reported failed, so it is not
        # a reply he actually sent; the second still counts.
        {"timestamp": _ts(1), "event_type": "wa_manual_reply_sent", "session_id": A,
         "message_sid": "SM-MAN-1"},
        {"timestamp": _ts(1), "event_type": "wa_reply_undelivered", "session_id": A,
         "message_sid": "SM-MAN-1", "status": "failed", "error_code": "63016",
         "source": "receipt"},
        {"timestamp": _ts(1), "event_type": "wa_manual_reply_sent", "session_id": A,
         "message_sid": "SM-MAN-2"},
        # Twilio refusing outright is a different fact from WhatsApp not delivering.
        {"timestamp": _ts(2), "event_type": "wa_reply_undelivered", "session_id": B,
         "attempts": 2, "source": "rest"},
        {"timestamp": _ts(2), "event_type": "wa_send_unknown", "session_id": B,
         "kind": "reply", "error": "TimeoutError: timed out"},
        # Receipts: delivered then read for ONE message is one delivery, and a
        # message that later failed is not a delivery at all.
        {"timestamp": _ts(2), "event_type": "wa_delivery_status", "session_id": A,
         "message_sid": "SM-D1", "status": "delivered"},
        {"timestamp": _ts(2), "event_type": "wa_delivery_status", "session_id": A,
         "message_sid": "SM-D1", "status": "read"},
        {"timestamp": _ts(2), "event_type": "wa_delivery_status", "session_id": B,
         "message_sid": "SM-F1", "status": "delivered"},
        {"timestamp": _ts(2), "event_type": "wa_delivery_status", "session_id": B,
         "message_sid": "SM-F1", "status": "failed", "error_code": "63024"},
        # Silent by design. One STOP logs wa_opted_out twice, one session.
        {"timestamp": _ts(1), "event_type": "wa_opted_out", "session_id": B},
        {"timestamp": _ts(1), "event_type": "wa_opted_out", "session_id": B},
        {"timestamp": _ts(1), "event_type": "wa_channel_off_skip", "session_id": C},
        {"timestamp": _ts(1), "event_type": "wa_reply_suppressed", "session_id": C,
         "reason": "muted"},
        {"timestamp": _ts(1), "event_type": "wa_bot_muted_skip", "session_id": A},
        {"timestamp": _ts(1), "event_type": "wa_rate_limited", "session_id": C},
        # Noise that must never reach a counter.
        {"timestamp": _ts(1), "event_type": "wa_delivery_status", "session_id": "wa-system",
         "message_sid": "SM-ORPHAN", "status": "delivered"},
        {"timestamp": _ts(30), "event_type": "conversation_started", "session_id": B,
         "episode": 9},
        {"timestamp": _ts(30), "event_type": "wa_nudge_sent", "session_id": B},
    ]
    _seed(rows)


EXPECTED = {
    "wa_conversations": 3,
    "wa_returning_episodes": 1,
    "wa_messages": 4,
    "wa_leads": 2,
    "wa_human_requests": 1,
    "wa_alerts_sent": 1,
    "wa_link_offered": 2,
    "wa_nudges_sent": 1,
    "wa_manual_replies": 1,
    "wa_undelivered": 1,
    "wa_send_unknown": 1,
    "wa_answers_lost": 0,
    "wa_delivered": 1,
    "wa_delivery_failed": 1,
    "wa_silent_kill_switch": 1,
    "wa_silent_muted": 2,
    "wa_silent_opt_outs": 1,
    "wa_silent_rate_limited": 1,
    "wa_silent_total": 5,
}


# ── 1) build_report_stats carries every counter ─────────────────────────────
def test_build_report_stats_returns_every_wa_counter():
    _seed_wa()
    stats = app.build_report_stats(7)
    for key, value in EXPECTED.items():
        assert stats[key] == value, f"{key}: {stats[key]} != {value}"
    # wa-system is a receipt nobody could attribute to a thread.
    assert stats["wa_delivered"] == 1
    # The widget half of the report is untouched by any of this.
    assert stats["human_requests"] == 1
    assert stats["conversations_started"] == 0


def test_wa_channel_counters_is_pure_and_zeroed_on_an_empty_window():
    assert set(app.wa_channel_counters([]).values()) == {0}
    assert set(app.wa_channel_counters([]).keys()) == set(EXPECTED.keys())


# ── 2) Both renderers show the lines ────────────────────────────────────────
def _wa_slice(text: str) -> str:
    start = text.index("WHATSAPP (new channel)")
    return text[start:text.index("WENT LIVE THIS WEEK", start)]


def test_report_text_and_html_carry_the_new_lines():
    _seed_wa()
    stats = app.build_report_stats(7)
    text = app.format_report_text(stats)
    block = _wa_slice(text)
    for line in [
        "- Returning customers who started a fresh conversation: 1",
        "- Asked to speak with Nick/Lyn: 1",
        "- Owner alerts sent to you: 1",
        "- Trial link offered: 2 conversation(s)",
        "- Follow-up nudges sent: 1",
        "- Manual replies you sent: 1",
        "- Bot replies Twilio refused to send: 1",
        "- Replies Twilio never confirmed: 1",
        "- Answers started but never sent: 0",
        "- Replies WhatsApp confirmed delivered / reported failed: 1 / 1",
        "- Times the bot stayed silent by design: 5 (kill switch 1, bot muted 2, "
        "opt-outs 1, flood limit 1)",
    ]:
        assert line in block, line
    # The header and signature already carry one, so only this block is asserted.
    assert EM_DASH not in block

    html = app.format_report_html(stats)
    for label in [
        "Returning customers who started a fresh conversation",
        "Asked to speak with Nick/Lyn",
        "Owner alerts sent to you",
        "Trial link offered",
        "Follow-up nudges sent",
        "Bot replies Twilio refused to send",
        "Replies Twilio never confirmed",
        "Answers started but never sent",
        "Replies WhatsApp confirmed delivered / reported failed",
        "Times the bot stayed silent by design",
    ]:
        assert label in html, label
    assert "kill switch 1, bot muted 2, opt-outs 1, flood limit 1" in html


def test_report_sms_is_untouched_by_the_whatsapp_block():
    _seed_wa()
    stats = app.build_report_stats(7)
    sms = app.format_report_sms(stats)
    assert "silent by design" not in sms.lower()


# ── 3) QA exclusion still removes a session from EVERY counter ──────────────
def test_excluded_session_drops_out_of_every_counter(monkeypatch):
    _seed_wa()
    baseline = app.build_report_stats(7)
    app._report_excluded_cache["ids"] = (9e18, frozenset({A}))
    app._wa_counters_cache.clear()
    excluded = app.build_report_stats(7)
    assert excluded["wa_conversations"] == 1          # only B's episode survives
    assert excluded["wa_returning_episodes"] == 0
    assert excluded["wa_messages"] == 2
    assert excluded["wa_leads"] == 1
    assert excluded["wa_human_requests"] == 0
    assert excluded["wa_alerts_sent"] == 0
    assert excluded["wa_link_offered"] == 1
    assert excluded["wa_nudges_sent"] == 0
    assert excluded["wa_manual_replies"] == 0
    assert excluded["wa_delivered"] == 0
    assert excluded["wa_silent_muted"] == 1  # only the muted skip on A goes
    assert excluded["wa_silent_total"] == 4
    assert baseline["wa_conversations"] == 3         # the seed itself did not move


# ── 4) The window is the report window ──────────────────────────────────────
def test_old_rows_appear_only_in_a_wide_window():
    _seed_wa()
    assert app.build_report_stats(7)["wa_nudges_sent"] == 1
    app._wa_counters_cache.clear()
    wide = app.build_report_stats(60)
    assert wide["wa_nudges_sent"] == 2
    assert wide["wa_conversations"] == 4
    assert wide["wa_returning_episodes"] == 2


# ── 5) Dashboard and report agree, off the event loop ───────────────────────
def test_dashboard_counters_match_the_report():
    _seed_wa()
    payload = app.wa_dashboard_payload()
    assert set(payload["counters"].keys()) == set(app.wa_channel_counters([]).keys())
    assert payload["counters_window_days"] == 7
    stats = app.build_report_stats(7)
    for key in payload["counters"]:
        assert payload["counters"][key] == stats[key], key


def test_dashboard_counters_endpoint_and_markup():
    _seed_wa()
    response = client.get("/api/wa/conversations", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["counters_window_days"] == 7
    assert body["counters"]["wa_human_requests"] == 1
    assert body["counters_excluded_sessions"] == 0
    admin = client.get("/admin", headers=AUTH)
    assert admin.status_code == 200
    assert 'id="waCounters"' in admin.text
    assert "as in the Monday report" in admin.text
    assert "test session(s) excluded" in admin.text


def test_dashboard_counter_tiles_report_the_exclusion_count():
    _seed_wa()
    app._report_excluded_cache["ids"] = (9e18, frozenset({A}))
    app._wa_counters_cache.clear()
    payload = app.wa_dashboard_payload()
    assert payload["counters_excluded_sessions"] == 1
    assert payload["counters"]["wa_human_requests"] == 0


# ── 6) Answers started but never sent ───────────────────────────────────────
def test_answers_lost_counts_a_deferral_with_no_outcome():
    _seed([
        {"timestamp": _ts(1), "event_type": "wa_reply_deferred", "session_id": A,
         "message_sid": "SMinbound1", "sender": "555001"},
        {"timestamp": _ts(1), "event_type": "wa_reply_deferred", "session_id": B,
         "message_sid": "SMinbound2", "sender": "555002"},
        # B's worker finished: the outcome carries the OUTBOUND sid, which is why
        # the pairing is per thread rather than per message_sid.
        {"timestamp": _ts(1), "event_type": "wa_reply_sent", "session_id": B,
         "message_sid": "SMoutbound2", "delivery": "rest"},
    ])
    stats = app.build_report_stats(7)
    assert stats["wa_answers_lost"] == 1


def test_a_suppressed_answer_is_not_a_lost_one():
    _seed([
        {"timestamp": _ts(1), "event_type": "wa_reply_deferred", "session_id": A,
         "message_sid": "SMinbound1"},
        {"timestamp": _ts(1), "event_type": "wa_reply_suppressed", "session_id": A,
         "reason": "opted_out"},
    ])
    stats = app.build_report_stats(7)
    assert stats["wa_answers_lost"] == 0
    # The STOP itself logs wa_opted_out for the thread, so the dropped in-flight
    # answer must not be counted a second time.
    assert stats["wa_silent_total"] == 0


# ── 7) Hand-built stat dicts still render ───────────────────────────────────
def test_renderers_tolerate_a_stats_dict_without_the_new_keys():
    minimal = {
        "window_days": 7,
        "since": _ts(7),
        "widget_impressions": 0,
        "raw_page_loads": 0,
        "widget_opened_sessions": 0,
        "conversations_started": 0,
        "engagement_rate": 0.0,
        "contact_leads": 0,
        "conversation_to_lead_rate": 0.0,
        "trial_link_clicks": 0,
        "booking_link_shown_sessions": 0,
        "handoffs": 0,
        "handoff_rate": 0.0,
        "lead_lines": [],
    }
    text = app.format_report_text(minimal)
    assert "- Answers started but never sent: 0" in text
    assert "Times the bot stayed silent by design" in app.format_report_html(minimal)
