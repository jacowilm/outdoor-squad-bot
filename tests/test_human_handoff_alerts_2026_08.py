"""Regression guards for real human-request alerts and honest owner reporting."""

import importlib
import json
import os
import sys
import tempfile
import threading
import pytest
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
]:
    os.environ.pop(key, None)

import app  # noqa: E402

importlib.reload(app)
app.SUPABASE_URL = ""
app.SUPABASE_KEY = None

_tmpdir = Path(tempfile.mkdtemp(prefix="os-human-alert-test-"))
app.EVENTS_FILE = _tmpdir / "events.jsonl"
app.HUMAN_REQUEST_CLAIMS_FILE = _tmpdir / "human_request_claims.jsonl"
app.HUMAN_REQUEST_CLAIMS_LOCK_FILE = _tmpdir / "human_request_claims.lock"
app.CONVERSATION_LOG_FILE = _tmpdir / "conversation_logs.jsonl"
app.LEADS_FILE = _tmpdir / "leads.json"


@pytest.fixture(autouse=True)
def _reset_human_alert_state():
    app.SUPABASE_URL = ""
    app.SUPABASE_KEY = None
    app.SUPABASE_SERVICE_ROLE_KEY = None
    app.EVENTS_FILE.write_text("")
    app.HUMAN_REQUEST_CLAIMS_FILE.unlink(missing_ok=True)
    app.HUMAN_REQUEST_CLAIMS_LOCK_FILE.unlink(missing_ok=True)
    yield


def _events():
    if not app.EVENTS_FILE.exists():
        return []
    return [json.loads(line) for line in app.EVENTS_FILE.read_text().splitlines() if line.strip()]


def _seed_events(rows):
    app.EVENTS_FILE.write_text("\n".join(json.dumps(row) for row in rows) + "\n")


def _ts(days_ago: float = 0) -> str:
    return (datetime.now() - timedelta(days=days_ago)).isoformat()


def test_explicit_human_request_detector_avoids_identity_and_incidental_nick_mentions():
    assert app.is_explicit_human_request("Can I speak to Nick please?")
    assert app.is_explicit_human_request("I'd rather talk to an actual person")
    assert app.is_explicit_human_request("Could someone call me back?")
    assert app.is_explicit_human_request("Are you a bot? I want to speak to Nick")
    assert app.is_explicit_human_request("Can Nick call me?")
    assert app.is_explicit_human_request("Please connect me with Lyn.")
    assert app.is_explicit_human_request("I want Nick to call me.")
    assert not app.is_explicit_human_request("Are you a real person?")
    assert not app.is_explicit_human_request("What do you call Nick?")
    assert not app.is_explicit_human_request("Nick sounds funny")


def test_real_widget_human_request_queues_one_owner_alert_without_contact_details(monkeypatch):
    session_id = "widget-human-alert-1"
    app.conversations[session_id] = [{"role": "user", "content": "Can I speak to Nick please?"}]
    queued = []
    monkeypatch.setattr(app, "notify_lead_summary_async", lambda payload, *, reason: queued.append((payload, reason)))

    assert app.notify_human_request_if_needed(
        "Can I speak to Nick please?", session_id, trusted_widget=True
    ) is True
    assert app.notify_human_request_if_needed(
        "Can I speak to Nick please?", session_id, trusted_widget=True
    ) is False

    assert len(queued) == 1
    payload, reason = queued[0]
    assert reason == "explicit_human_request"
    assert payload["session_id"] == session_id
    assert payload["route"] == "human handoff"
    assert payload["raw_message"] == "Can I speak to Nick please?"
    assert payload.get("phone") is None
    assert payload.get("email") is None
    assert any(e["event_type"] == "human_handoff_requested" for e in _events())


def test_internal_qa_human_request_is_logged_but_never_notified(monkeypatch):
    session_id = "qa-human-alert-1"
    app.conversations[session_id] = [{"role": "user", "content": "Let me talk to a person"}]
    queued = []
    monkeypatch.setattr(app, "notify_lead_summary_async", lambda payload, *, reason: queued.append((payload, reason)))

    assert app.notify_human_request_if_needed(
        "Let me talk to a person", session_id, trusted_widget=True, internal_qa=True
    ) is True
    assert queued == []
    event = _events()[-1]
    assert event["event_type"] == "human_handoff_requested"
    assert event["notification_skipped"] == "internal_session"


def test_spoofed_widget_prefix_without_server_token_never_notifies(monkeypatch):
    session_id = "widget-spoofed"
    queued = []
    monkeypatch.setattr(app, "notify_lead_summary_async", lambda payload, *, reason: queued.append((payload, reason)))

    assert app.notify_human_request_if_needed("Please connect me with Lyn", session_id) is False
    assert queued == []
    assert _events()[-1]["notification_skipped"] == "untrusted_session"


def test_widget_session_token_is_bound_to_its_server_minted_session(monkeypatch):
    monkeypatch.setattr(app, "WIDGET_SIGNING_KEY", "test-signing-key")
    session_id, token = app.mint_widget_session()

    assert session_id.startswith("widget-")
    assert app.verify_widget_session_token(session_id, token)
    assert not app.verify_widget_session_token("widget-attacker-changed-it", token)
    assert not app.verify_widget_session_token(session_id, token + "x")


def test_concurrent_duplicate_requests_claim_once(monkeypatch):
    session_id = "widget-concurrent"
    queued = []
    barrier = threading.Barrier(8)
    monkeypatch.setattr(app, "notify_lead_summary_async", lambda payload, *, reason: queued.append((payload, reason)))

    def worker():
        barrier.wait()
        app.notify_human_request_if_needed("Can Nick call me?", session_id, trusted_widget=True)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(queued) == 1
    assert any(
        e["event_type"] == "human_handoff_requested"
        and e.get("session_id") == session_id
        and e.get("alert_eligible") is True
        for e in _events()
    )


def test_local_claim_survives_more_than_ten_thousand_later_events(monkeypatch):
    session_id = "widget-old-claim"
    queued = []
    monkeypatch.setattr(app, "notify_lead_summary_async", lambda payload, *, reason: queued.append((payload, reason)))
    assert app.notify_human_request_if_needed("Can Nick call me?", session_id, trusted_widget=True)
    with app.EVENTS_FILE.open("a") as handle:
        for index in range(10001):
            handle.write(json.dumps({"timestamp": _ts(), "event_type": "noise", "session_id": f"n-{index}"}) + "\n")

    assert not app.notify_human_request_if_needed("Can Nick call me?", session_id, trusted_widget=True)
    assert len(queued) == 1


def test_weekly_report_separates_requests_from_successfully_sent_alerts():
    _seed_events([
        {"timestamp": _ts(1), "event_type": "widget_impression", "session_id": "widget-a"},
        {"timestamp": _ts(1), "event_type": "conversation_started", "session_id": "widget-a"},
        {"timestamp": _ts(1), "event_type": "human_handoff_suggested", "session_id": "widget-a"},
        {"timestamp": _ts(1), "event_type": "human_handoff_requested", "session_id": "widget-a", "alert_eligible": True},
        {"timestamp": _ts(1), "event_type": "lead_summary_notification_sent", "session_id": "widget-a", "reason": "explicit_human_request"},
        {"timestamp": _ts(1), "event_type": "widget_impression", "session_id": "widget-b"},
        {"timestamp": _ts(1), "event_type": "conversation_started", "session_id": "widget-b"},
        {"timestamp": _ts(1), "event_type": "human_handoff_suggested", "session_id": "widget-b"},
    ])

    stats = app.build_report_stats(days=7)
    assert stats["human_requests"] == 1
    assert stats["handoff_alerts_sent"] == 1
    text = app.format_report_text(stats)
    assert "Explicit requests to speak with Nick/Lyn: 1" in text
    assert "Owner alerts successfully sent: 1" in text
    assert "Passed to Nick/Lyn" not in text
    assert "2 handoffs" not in app.format_report_sms(stats)
