"""Weekly owner stats report — guards (2026-07-03).

Covers: the widget_impression event allowlisting, time-window + widget-session
filtering in build_report_stats, rate math, the /api/reports/weekly endpoint
(auth + dry-run), scheduler target math, and the SMS digest format.
"""

import base64
import importlib
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Hermetic env: no Supabase, no AI keys, no report auto-scheduler, known admin creds.
for key in [
    "SUPABASE_URL",
    "SUPABASE_SERVICE_ROLE_KEY",
    "SUPABASE_KEY",
    "OUTDOOR_SQUAD_ANTHROPIC_API_KEY",
    "ANTHROPIC_API_KEY",
    "OUTDOOR_SQUAD_OPENAI_API_KEY",
    "OPENAI_API_KEY",
    "OUTDOOR_SQUAD_GEMINI_API_KEY",
    "OUTDOOR_SQUAD_RESEND_API_KEY",
    "OUTDOOR_SQUAD_REPORT_EMAIL_TO",
]:
    os.environ.pop(key, None)
os.environ["OUTDOOR_SQUAD_ADMIN_PASSWORD"] = "report-test-pw"

import app  # noqa: E402

importlib.reload(app)
app.SUPABASE_URL = ""
app.SUPABASE_KEY = None
app.REPORT_EMAIL_TO = ""
app.LEAD_SUMMARY_RESEND_API_KEY = ""
app.ADMIN_PASSWORD = "report-test-pw"

_tmpdir = Path(tempfile.mkdtemp(prefix="os-report-test-"))
app.LEADS_FILE = _tmpdir / "leads.json"
app.EVENTS_FILE = _tmpdir / "events.jsonl"
app.CONVERSATION_LOG_FILE = _tmpdir / "conversation_logs.jsonl"

from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(app.app)

AUTH = {"Authorization": "Basic " + base64.b64encode(b"outdoorsquad:report-test-pw").decode()}


def _seed_events(rows):
    app.EVENTS_FILE.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def _ts(days_ago: float) -> str:
    return (datetime.now() - timedelta(days=days_ago)).isoformat()


def _seed_funnel():
    """3 widget conversations, 2 contact leads, 1 handoff, 1 trial click in-window;
    plus out-of-window and non-widget noise that must be excluded."""
    rows = [
        # in-window widget sessions
        {"timestamp": _ts(1), "event_type": "widget_impression", "session_id": "widget-a1"},
        {"timestamp": _ts(1), "event_type": "widget_impression", "session_id": "widget-a2"},
        {"timestamp": _ts(2), "event_type": "widget_impression", "session_id": "widget-a3"},
        {"timestamp": _ts(2), "event_type": "widget_impression", "session_id": "widget-a4"},
        {"timestamp": _ts(1), "event_type": "widget_opened", "session_id": "widget-a1"},
        {"timestamp": _ts(1), "event_type": "widget_opened", "session_id": "widget-a2"},
        {"timestamp": _ts(2), "event_type": "widget_opened", "session_id": "widget-a3"},
        {"timestamp": _ts(1), "event_type": "conversation_started", "session_id": "widget-a1"},
        {"timestamp": _ts(1), "event_type": "conversation_started", "session_id": "widget-a2"},
        {"timestamp": _ts(2), "event_type": "conversation_started", "session_id": "widget-a3"},
        {"timestamp": _ts(1), "event_type": "lead_captured", "session_id": "widget-a1", "route": "trial"},
        {"timestamp": _ts(2), "event_type": "lead_captured", "session_id": "widget-a3", "route": "human handoff"},
        # synthetic trial-click lead event must NOT count as a contact lead
        {"timestamp": _ts(1), "event_type": "lead_captured", "session_id": "widget-a2", "route": "trial-link-clicked"},
        {"timestamp": _ts(1), "event_type": "trial_link_clicked", "session_id": "widget-a2"},
        {"timestamp": _ts(1), "event_type": "booking_link_shown", "session_id": "widget-a1"},
        {"timestamp": _ts(2), "event_type": "human_handoff_suggested", "session_id": "widget-a3"},
        # internal QA traffic (non-widget session ids) — excluded
        {"timestamp": _ts(1), "event_type": "conversation_started", "session_id": "e2e-leadtest"},
        {"timestamp": _ts(1), "event_type": "lead_captured", "session_id": "e2e-leadtest", "route": "trial"},
        # out-of-window widget traffic — excluded
        {"timestamp": _ts(30), "event_type": "widget_impression", "session_id": "widget-old"},
        {"timestamp": _ts(30), "event_type": "conversation_started", "session_id": "widget-old"},
    ]
    _seed_events(rows)


def test_widget_impression_is_allowlisted():
    resp = client.post(
        "/api/event",
        json={"event_type": "widget_impression", "session_id": "widget-imp1", "metadata": {"page": "/"}},
    )
    assert resp.status_code == 200
    lines = [json.loads(l) for l in app.EVENTS_FILE.read_text().splitlines() if l.strip()]
    mine = [l for l in lines if l.get("session_id") == "widget-imp1"]
    assert mine and mine[-1]["event_type"] == "widget_impression"  # not widget_event_other


def test_report_stats_window_and_widget_filtering():
    _seed_funnel()
    stats = app.build_report_stats(days=7)
    assert stats["widget_impressions"] == 4  # widget-old + QA noise excluded
    assert stats["widget_opened_sessions"] == 3
    assert stats["conversations_started"] == 3  # e2e-leadtest + widget-old excluded
    assert stats["contact_leads"] == 2  # trial-link-clicked + QA lead excluded
    assert stats["trial_link_clicks"] == 1
    assert stats["booking_link_shown_sessions"] == 1
    assert stats["handoffs"] == 1


def test_report_rates():
    _seed_funnel()
    stats = app.build_report_stats(days=7)
    assert stats["engagement_rate"] == 0.75  # 3 conversations / 4 impressions
    assert stats["conversation_to_lead_rate"] == 0.667  # 2 / 3
    assert stats["handoff_rate"] == 0.333  # 1 / 3


def test_report_text_content():
    _seed_funnel()
    stats = app.build_report_stats(days=7)
    text = app.format_report_text(stats)
    assert "THE FUNNEL" in text
    assert "Conversations started: 3" in text
    assert "Leads captured (name/phone/email handed over): 2" in text
    assert "67% of conversations" in text
    assert "Passed to Nick/Lyn: 1" in text
    assert "outdoor-squad-bot.onrender.com/admin" in text


def test_report_sms_digest_is_short_and_complete():
    _seed_funnel()
    stats = app.build_report_stats(days=7)
    sms = app.format_report_sms(stats)
    assert len(sms) <= 320
    for fragment in ["4 visits", "3 chats", "2 leads", "1 trial clicks", "1 handoffs"]:
        assert fragment in sms


def test_report_endpoint_requires_admin():
    assert client.get("/api/reports/weekly").status_code == 401
    bad = {"Authorization": "Basic " + base64.b64encode(b"outdoorsquad:wrong").decode()}
    assert client.get("/api/reports/weekly", headers=bad).status_code == 401


def test_report_endpoint_dry_run():
    _seed_funnel()
    resp = client.get("/api/reports/weekly", headers=AUTH)
    assert resp.status_code == 200
    data = resp.json()
    assert data["sent_email"] is False
    assert data["stats"]["conversations_started"] == 3
    assert "THE FUNNEL" in data["report_text"]


def test_send_weekly_report_without_config_is_safe():
    _seed_funnel()
    result = app.send_weekly_report()
    assert result["sent_email"] is False
    assert result["sent_sms"] is False


def test_next_report_time_math():
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Australia/Sydney")
    # Friday 3 Jul 2026 10:00 Sydney -> Monday 6 Jul 08:00
    now = datetime(2026, 7, 3, 10, 0, tzinfo=tz)
    target = app._next_report_time(now)
    assert (target.year, target.month, target.day, target.hour) == (2026, 7, 6, 8)
    assert target.weekday() == 0
    # Monday 07:59 -> same day 08:00
    now = datetime(2026, 7, 6, 7, 59, tzinfo=tz)
    assert app._next_report_time(now).day == 6
    # Monday 08:00 exactly -> NEXT Monday (strictly future)
    now = datetime(2026, 7, 6, 8, 0, tzinfo=tz)
    assert app._next_report_time(now).day == 13
