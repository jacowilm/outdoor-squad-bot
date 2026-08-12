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
        {"timestamp": _ts(2), "event_type": "human_handoff_requested", "session_id": "widget-a3", "alert_eligible": True},
        {"timestamp": _ts(2), "event_type": "lead_summary_notification_sent", "session_id": "widget-a3", "reason": "explicit_human_request"},
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


def test_greeting_ab_split_and_report_section():
    rows = []
    for i in range(3):
        sid = f"widget-va{i}"
        rows.append({"timestamp": _ts(1), "event_type": "widget_impression", "session_id": sid, "teaser_variant": "control"})
        rows.append({"timestamp": _ts(1), "event_type": "teaser_shown", "session_id": sid, "teaser_variant": "control"})
    rows.append({"timestamp": _ts(1), "event_type": "widget_opened", "session_id": "widget-va0", "teaser_variant": "control"})
    for i in range(2):
        sid = f"widget-vb{i}"
        rows.append({"timestamp": _ts(1), "event_type": "widget_impression", "session_id": sid, "teaser_variant": "nick"})
        rows.append({"timestamp": _ts(1), "event_type": "teaser_shown", "session_id": sid, "teaser_variant": "nick"})
        rows.append({"timestamp": _ts(1), "event_type": "widget_opened", "session_id": sid, "teaser_variant": "nick"})
    _seed_events(rows)
    stats = app.build_report_stats(days=7)
    assert stats["teaser_variants"]["control"] == {"visitors": 3, "opened": 1, "conversations": 0}
    assert stats["teaser_variants"]["nick"] == {"visitors": 2, "opened": 2, "conversations": 0}
    text = app.format_report_text(stats)
    assert "GREETING TEST" in text
    assert "Original greeting: 3 visitors, 1 chats opened (33%)" in text
    assert "Nick's greeting line: 2 visitors, 2 chats opened (100%)" in text


def test_report_includes_widget_version_rollout_for_all_human_visitors():
    rows = [
        {"timestamp": _ts(1), "event_type": "widget_impression", "session_id": "widget-version-current", "widget_version": "2026-08-12"},
        {"timestamp": _ts(1), "event_type": "conversation_started", "session_id": "widget-version-current", "widget_version": "2026-08-12"},
        {"timestamp": _ts(1), "event_type": "widget_impression", "session_id": "widget-version-old", "widget_version": "2026-08-07"},
        {"timestamp": _ts(1), "event_type": "widget_opened", "session_id": "widget-version-old", "widget_version": "2026-08-07"},
        {"timestamp": _ts(1), "event_type": "widget_impression", "session_id": "widget-version-unstamped"},
        {"timestamp": _ts(1), "event_type": "widget_opened", "session_id": "widget-version-unstamped"},
    ]
    _seed_events(rows)

    stats = app.build_report_stats(days=7)
    assert stats["widget_versions"] == {
        "2026-08-12": 1,
        "2026-08-07": 1,
        "unstamped / cached older copy": 1,
    }
    text = app.format_report_text(stats)
    assert "WIDGET VERSION ROLLOUT" in text
    assert "2026-08-12: 1 visitor(s)" in text
    assert "unstamped / cached older copy: 1 visitor(s)" in text


def test_report_restates_four_non_overlapping_weeks_with_one_locked_visitor_definition():
    rows = []
    for index, days_ago in enumerate((1, 8, 15, 22)):
        sid = f"widget-baseline-{index}"
        rows.extend([
            {"timestamp": _ts(days_ago), "event_type": "widget_impression", "session_id": sid},
            {"timestamp": _ts(days_ago), "event_type": "conversation_started", "session_id": sid},
        ])
    rows.append({"timestamp": _ts(2), "event_type": "widget_impression", "session_id": "widget-baseline-crawler"})
    _seed_events(rows)

    stats = app.build_report_stats(days=7)
    baseline = stats["traffic_baseline_4w"]
    assert len(baseline) == 4
    assert [week["real_visitors"] for week in baseline] == [1, 1, 1, 1]
    assert all(week["definition"] == app.REAL_VISITOR_DEFINITION for week in baseline)
    text = app.format_report_text(stats)
    assert "FOUR-WEEK TRAFFIC BASELINE" in text
    assert "Definition locked:" in text


def test_no_greeting_section_without_both_variants():
    _seed_funnel()
    text = app.format_report_text(app.build_report_stats(days=7))
    assert "GREETING TEST" not in text


def test_shipped_lines_windowed_and_report_section():
    cl = _tmpdir / "changelog.json"
    cl.write_text(json.dumps([
        {"date": _ts(1)[:10], "line": "New thing went live"},
        {"date": "2020-01-01", "line": "Ancient thing"},
    ]))
    old = app.CHANGELOG_FILE
    app.CHANGELOG_FILE = cl
    try:
        _seed_funnel()
        stats = app.build_report_stats(days=7)
        assert stats["shipped_lines"] == [f"{_ts(1)[:10]} — New thing went live"]
        text = app.format_report_text(stats)
        assert "WENT LIVE THIS WEEK" in text
        assert "New thing went live" in text
        assert "Ancient thing" not in text
    finally:
        app.CHANGELOG_FILE = old


def test_missing_changelog_is_safe():
    old = app.CHANGELOG_FILE
    app.CHANGELOG_FILE = _tmpdir / "nope.json"
    try:
        _seed_funnel()
        stats = app.build_report_stats(days=7)
        assert stats["shipped_lines"] == []
        assert "WENT LIVE THIS WEEK" not in app.format_report_text(stats)
    finally:
        app.CHANGELOG_FILE = old


def test_teaser_events_are_allowlisted():
    for event_type in ("teaser_shown", "teaser_clicked", "teaser_dismissed"):
        resp = client.post(
            "/api/event",
            json={"event_type": event_type, "session_id": "widget-teaser1"},
        )
        assert resp.status_code == 200
    lines = [json.loads(l) for l in app.EVENTS_FILE.read_text().splitlines() if l.strip()]
    mine = [l["event_type"] for l in lines if l.get("session_id") == "widget-teaser1"]
    assert mine == ["teaser_shown", "teaser_clicked", "teaser_dismissed"]


def test_report_stats_window_and_widget_filtering():
    _seed_funnel()
    stats = app.build_report_stats(days=7)
    # widget-a4 is a bare single impression with no interaction — the crawler
    # signature — so it is excluded from visitors; raw_page_loads still sees it.
    assert stats["widget_impressions"] == 3  # widget-old + QA noise + crawler excluded
    assert stats["raw_page_loads"] == 4
    assert stats["widget_opened_sessions"] == 3
    assert stats["conversations_started"] == 3  # e2e-leadtest + widget-old excluded
    assert stats["contact_leads"] == 2  # trial-link-clicked + QA lead excluded
    assert stats["trial_link_clicks"] == 1
    assert stats["booking_link_shown_sessions"] == 1
    assert stats["human_requests"] == 1
    assert stats["handoff_alerts_sent"] == 1


def test_report_rates():
    _seed_funnel()
    stats = app.build_report_stats(days=7)
    assert stats["engagement_rate"] == 1.0  # 3 conversations / 3 real visitors
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
    assert "Explicit requests to speak with Nick/Lyn: 1" in text
    assert "Owner alerts successfully sent: 1" in text
    assert "outdoor-squad-bot.onrender.com/admin" in text


def test_report_sms_digest_is_short_and_complete():
    _seed_funnel()
    stats = app.build_report_stats(days=7)
    sms = app.format_report_sms(stats)
    assert len(sms) <= 320
    for fragment in ["3 real visitors", "3 chats", "2 leads", "1 trial clicks", "1 human requests", "1 alerts sent"]:
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


def test_wa_onboard_requires_admin_and_reports_unconfigured():
    assert client.get("/wa-onboard").status_code == 401
    resp = client.get("/wa-onboard", headers=AUTH)
    assert resp.status_code == 200
    assert "Not configured yet" in resp.text


def test_wa_webhook_verify_handshake():
    app.WA_WEBHOOK_VERIFY_TOKEN = "test-verify-tok"
    try:
        ok = client.get(
            "/wa-webhook",
            params={"hub.mode": "subscribe", "hub.verify_token": "test-verify-tok", "hub.challenge": "12345"},
        )
        assert ok.status_code == 200 and ok.text == "12345"
        bad = client.get(
            "/wa-webhook",
            params={"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "x"},
        )
        assert bad.status_code == 403
    finally:
        app.WA_WEBHOOK_VERIFY_TOKEN = ""


def test_wa_webhook_signature_enforced_and_events_logged():
    app.WA_APP_SECRET = "test-secret"
    try:
        payload = json.dumps({
            "entry": [{"changes": [{"field": "messages", "value": {
                "metadata": {"phone_number_id": "123"},
                "messages": [{"type": "text"}],
            }}]}]
        })
        import hashlib as _h, hmac as _hm
        sig = "sha256=" + _hm.new(b"test-secret", payload.encode(), _h.sha256).hexdigest()
        bad = client.post("/wa-webhook", content=payload, headers={"x-hub-signature-256": "sha256=nope"})
        assert bad.status_code == 403
        good = client.post("/wa-webhook", content=payload, headers={"x-hub-signature-256": sig})
        assert good.status_code == 200
        lines = [json.loads(l) for l in app.EVENTS_FILE.read_text().splitlines() if l.strip()]
        wa = [l for l in lines if l.get("event_type") == "wa_messages"]
        assert wa and wa[-1]["message_count"] == 1 and wa[-1]["message_type"] == "text"
    finally:
        app.WA_APP_SECRET = ""


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


# --- PostgREST row-cap pagination (2026-07-27) ---------------------------------
# Supabase truncates any response to 1000 rows and reports no error, so the old
# `limit=5000` read only ever saw the newest 1000 events. Once the events table
# passed 1000 rows that silently shortened every report window: a days=21 report
# covered ~10 real days (924 impressions reported vs 1857 actual).


class _FakePagedSupabase:
    """Stands in for supabase_request, enforcing PostgREST's 1000-row ceiling."""

    def __init__(self, total_rows):
        self.rows = [{"timestamp": f"2026-07-{(i % 28) + 1:02d}T00:00:00", "n": i}
                     for i in range(total_rows)]
        self.calls = []

    def __call__(self, method, table, *, params=None, json_body=None, prefer=None):
        params = params or {}
        limit = min(int(params.get("limit", 1000)), app.SUPABASE_PAGE_SIZE)
        offset = int(params.get("offset", 0))
        self.calls.append((offset, limit))
        return self.rows[offset:offset + limit]


def test_paged_select_reads_past_the_1000_row_cap(monkeypatch):
    fake = _FakePagedSupabase(2048)
    monkeypatch.setattr(app, "supabase_request", fake)
    rows = app.supabase_select_paged("t", {"select": "*"}, 5000)
    # All 2048 rows, not the first 1000.
    assert len(rows) == 2048
    assert [r["n"] for r in rows[:3]] == [0, 1, 2]
    assert rows[-1]["n"] == 2047
    # Stopped as soon as a short page came back — no wasted extra request.
    assert fake.calls == [(0, 1000), (1000, 1000), (2000, 1000)]


def test_paged_select_honours_the_cap():
    fake = _FakePagedSupabase(10000)
    import app as _app
    original = _app.supabase_request
    _app.supabase_request = fake
    try:
        rows = _app.supabase_select_paged("t", {"select": "*"}, 1500)
    finally:
        _app.supabase_request = original
    # Never reads more than the caller's ceiling (the OOM-era memory bound).
    assert len(rows) == 1500
    assert fake.calls == [(0, 1000), (1000, 500)]


def test_paged_select_handles_empty_table():
    fake = _FakePagedSupabase(0)
    import app as _app
    original = _app.supabase_request
    _app.supabase_request = fake
    try:
        assert _app.supabase_select_paged("t", {"select": "*"}, 5000) == []
    finally:
        _app.supabase_request = original


# --- crawler filter (2026-07-27) ------------------------------------------------
# 98% of raw sessions were crawlers: exactly one widget_impression, no
# sessionStorage, no interaction. They inflated the owner email's "visits" to
# ~1,900/3wk when real reach was ~110. A session is human if it viewed 2+
# pages in one visit or fired any human-signal event.


def test_single_bare_impression_is_not_human():
    assert not app.is_human_session(
        [{"event_type": "widget_impression", "session_id": "widget-c1"}]
    )


def test_multi_page_session_is_human():
    assert app.is_human_session(
        [
            {"event_type": "widget_impression", "session_id": "widget-h1"},
            {"event_type": "widget_impression", "session_id": "widget-h1"},
        ]
    )


def test_any_interaction_makes_a_single_page_session_human():
    for signal in ("teaser_shown", "widget_opened", "conversation_started", "message_sent"):
        assert app.is_human_session(
            [
                {"event_type": "widget_impression", "session_id": "widget-h2"},
                {"event_type": signal, "session_id": "widget-h2"},
            ]
        ), signal


def test_crawler_sessions_excluded_from_report_but_kept_in_raw_page_loads():
    rows = [
        # 5 crawler hits: one impression each, nothing else
        *[
            {"timestamp": _ts(1), "event_type": "widget_impression", "session_id": f"widget-bot{i}"}
            for i in range(5)
        ],
        # 1 human: stayed 10s+ on a single page
        {"timestamp": _ts(1), "event_type": "widget_impression", "session_id": "widget-real"},
        {"timestamp": _ts(1), "event_type": "teaser_shown", "session_id": "widget-real"},
    ]
    _seed_events(rows)
    stats = app.build_report_stats(days=7)
    assert stats["widget_impressions"] == 1
    assert stats["raw_page_loads"] == 6
    text = app.format_report_text(stats)
    assert "Real visitors who saw the chat bubble: 1" in text
    assert "Raw page loads including them: 6" in text
    # counting-change caveat so a jump around 24 Jul isn't read as growth
    assert "better counting, not" in text
