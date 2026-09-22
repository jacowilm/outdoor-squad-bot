"""Greeting A/B retirement (2026-09-22): Nick's line is now the sole default
teaser greeting, no more random assignment. Covers:
  - the served widget.js ships Nick's line as the static default and has no
    random/session-bucketed variant assignment left
  - the weekly report's historical A/B section still reads old mixed-variant
    events correctly (readability preserved)
  - once traffic is all-'nick' (post-retirement), the report's A/B section
    stops rendering on its own, since it needs >=2 variants
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
os.environ["OUTDOOR_SQUAD_ADMIN_PASSWORD"] = "greeting-test-pw"

import app  # noqa: E402

importlib.reload(app)
app.SUPABASE_URL = ""
app.SUPABASE_KEY = None
app.REPORT_EMAIL_TO = ""
app.LEAD_SUMMARY_RESEND_API_KEY = ""
app.ADMIN_PASSWORD = "greeting-test-pw"

_tmpdir = Path(tempfile.mkdtemp(prefix="os-greeting-test-"))
app.LEADS_FILE = _tmpdir / "leads.json"
app.EVENTS_FILE = _tmpdir / "events.jsonl"
app.CONVERSATION_LOG_FILE = _tmpdir / "conversation_logs.jsonl"

from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(app.app)
AUTH = {"Authorization": "Basic " + base64.b64encode(b"outdoorsquad:greeting-test-pw").decode()}


def _seed_events(rows):
    app.EVENTS_FILE.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def _ts(days_ago: float) -> str:
    return (datetime.now() - timedelta(days=days_ago)).isoformat()


def test_served_widget_js_defaults_to_nicks_greeting():
    resp = client.get("/widget.js")
    assert resp.status_code == 200
    body = resp.text
    # Nick's line is now the static default text in the HTML template, not a
    # runtime swap.
    assert "G'day — Robo-Nick here. The real Nick's mid-session" in body
    # The old control-variant opening line must be gone entirely.
    assert "Got a question about times, prices or where to start?" not in body
    # Assignment is a fixed stamp now: no more coin flip / sticky bucketing.
    assert "const TEASER_VARIANT = 'nick';" in body
    assert "Math.random() < 0.5 ? 'control' : 'nick'" not in body
    assert "os-teaser-variant" not in body


def test_historical_ab_events_still_read_correctly_in_report():
    """Old mixed-variant events (from before the 2026-09-22 retirement) must
    still tabulate correctly — this is the same fixture/assertions as the
    original A/B test, proving the retirement didn't touch report logic."""
    rows = []
    for i in range(3):
        sid = f"widget-ga{i}"
        rows.append({"timestamp": _ts(1), "event_type": "widget_impression", "session_id": sid, "teaser_variant": "control"})
        rows.append({"timestamp": _ts(1), "event_type": "teaser_shown", "session_id": sid, "teaser_variant": "control"})
    rows.append({"timestamp": _ts(1), "event_type": "widget_opened", "session_id": "widget-ga0", "teaser_variant": "control"})
    for i in range(2):
        sid = f"widget-gb{i}"
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


def test_post_retirement_all_nick_traffic_drops_ab_section():
    """Once every in-window event is stamped 'nick' (the post-retirement
    reality), the report has only one bucket and the A/B section must not
    render — a clean retirement, not a permanently-stuck test banner."""
    rows = []
    for i in range(4):
        sid = f"widget-post{i}"
        rows.append({"timestamp": _ts(1), "event_type": "widget_impression", "session_id": sid, "teaser_variant": "nick"})
    rows.append({"timestamp": _ts(1), "event_type": "widget_opened", "session_id": "widget-post0", "teaser_variant": "nick"})
    _seed_events(rows)
    stats = app.build_report_stats(days=7)
    assert list(stats["teaser_variants"].keys()) == ["nick"]
    text = app.format_report_text(stats)
    assert "GREETING TEST" not in text
