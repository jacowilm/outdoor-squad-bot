"""Send-then-persist, one classified retry, and Twilio delivery receipts.

Items #5 and #14 of the 11 Sep 2026 website-vs-WhatsApp diff
(outbox/WEB-VS-WHATSAPP-DIFF-2026-09-11.md). Before this, a reply was written
into the transcript BEFORE it was sent, so a refused send left Nick's
dashboard showing an answer nobody received, and "sent" only ever meant Twilio
accepted the message, never that WhatsApp delivered it.
"""
import base64
import hashlib
import hmac
import importlib
import json
import os
import sys
import urllib.parse

import pytest
from fastapi.testclient import TestClient

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
# Kept before any fixture replaces the module attribute: the StatusCallback
# test asserts on the payload the REAL sender builds.
REAL_SEND = app.send_whatsapp_via_twilio

TEST_TOKEN = "test-auth-token-for-signatures"
WEBHOOK_PATH = "/twilio-wa-webhook"
STATUS_PATH = "/twilio-wa-status"
SENDER = "whatsapp:+61452006342"
SID = "wa-61452006342"


@pytest.fixture()
def wa(monkeypatch, tmp_path):
    """Real gate, real scripted replies, captured transport, no network."""
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
    monkeypatch.setattr(app, "TWILIO_AUTH_TOKEN", TEST_TOKEN)
    monkeypatch.setattr(app, "TWILIO_WA_WEBHOOK_URL", "")
    monkeypatch.setattr(app, "TWILIO_WA_FROM", "+61499000000")
    monkeypatch.setattr(app, "WA_SEND_RETRY_SECONDS", 0.0)
    monkeypatch.setattr(app, "ADMIN_USERNAME", "u")
    monkeypatch.setattr(app, "ADMIN_PASSWORD", "p")
    monkeypatch.setattr(app, "generate_ai_reply", lambda m, s: ("Here are the times.", "test"))
    monkeypatch.setattr(app, "_wa_async_dispatch", lambda target, args: target(*args))
    monkeypatch.setattr(app, "notify_lead_summary_async", lambda li, **k: None)
    monkeypatch.setattr(app, "maybe_push_lead_to_momence", lambda *a, **k: None)
    monkeypatch.setattr(app, "is_rate_limited", lambda *a, **k: False)
    sent = []
    monkeypatch.setattr(
        app, "send_whatsapp_via_twilio",
        lambda to, body: (sent.append((to, body)), (True, "SMok"))[1],
    )
    app.conversations.clear()
    app._twilio_wa_seen_sids.clear()
    app._wa_setting_cache.clear()
    c = TestClient(app.app)
    c.sent = sent
    return c


def _sign(params, path):
    base = app.TWILIO_WA_FALLBACK_HOSTS[0]
    payload = base + path + "".join(k + str(params[k]) for k in sorted(params))
    return base64.b64encode(
        hmac.new(TEST_TOKEN.encode(), payload.encode(), hashlib.sha1).digest()
    ).decode()


_n = [0]


def post(client, body, sender=SENDER):
    _n[0] += 1
    params = {"From": sender, "To": "whatsapp:+61499000000", "Body": body,
              "MessageSid": f"SMrcpt{_n[0]:05d}", "NumMedia": "0"}
    return client.post(WEBHOOK_PATH, data=params,
                       headers={"X-Twilio-Signature": _sign(params, WEBHOOK_PATH)})


def post_status(client, message_sid, status, error_code="", to=SENDER, signed=True):
    params = {"MessageSid": message_sid, "MessageStatus": status,
              "To": to, "From": "whatsapp:+61499000000", "SmsStatus": status}
    if error_code:
        params["ErrorCode"] = error_code
    signature = _sign(params, STATUS_PATH) if signed else "not-the-signature"
    return client.post(STATUS_PATH, data=params, headers={"X-Twilio-Signature": signature})


def events(event_type=None):
    rows = []
    for line in app.EVENTS_FILE.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if event_type is None or row.get("event_type") == event_type:
            rows.append(row)
    return rows


def scripted_sender(monkeypatch, results):
    """Send stub that plays a scripted list of (ok, detail), repeating the last."""
    calls = []

    def _send(to_digits, body):
        calls.append((to_digits, body))
        return results[min(len(calls) - 1, len(results) - 1)]

    monkeypatch.setattr(app, "send_whatsapp_via_twilio", _send)
    return calls


# ---- the status route itself ------------------------------------------------

def test_status_route_is_fail_closed_signed_and_terminal_only(wa, monkeypatch):
    monkeypatch.setattr(app, "TWILIO_AUTH_TOKEN", "")
    assert post_status(wa, "SM1", "delivered").status_code == 503
    monkeypatch.setattr(app, "TWILIO_AUTH_TOKEN", TEST_TOKEN)
    assert post_status(wa, "SM1", "delivered", signed=False).status_code == 403

    r = post_status(wa, "SM1", "delivered")
    assert r.status_code == 200 and "<Message>" not in r.text
    logged = events("wa_delivery_status")
    assert len(logged) == 1
    assert logged[0]["session_id"] == SID and logged[0]["message_sid"] == "SM1"
    assert logged[0]["status"] == "delivered"

    # A non-terminal transition is acknowledged and then dropped: Twilio fires
    # one of these per message per transition.
    assert post_status(wa, "SM2", "sent").status_code == 200
    assert len(events("wa_delivery_status")) == 1
    assert not app.wa_delivery_ledger(SID) or all(
        e["sid"] != "SM2" for e in app.wa_delivery_ledger(SID))


def test_status_callback_is_passed_to_twilio_when_configured(wa, monkeypatch):
    monkeypatch.setattr(app, "TWILIO_ACCOUNT_SID", "AC1")
    monkeypatch.setattr(app, "TWILIO_WA_WEBHOOK_URL",
                        "https://outdoorsquad.realtiq.ai/twilio-wa-webhook")
    posted = []

    class _R:
        def read(self): return b'{"sid":"SMx"}'
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_open(req, timeout=15):
        posted.append(urllib.parse.parse_qs(req.data.decode()))
        return _R()

    monkeypatch.setattr(app.urllib.request, "urlopen", fake_open)
    # REAL_SEND, not the fixture's stub: this is the payload assertion.
    ok, sid = REAL_SEND("61452006342", "hello")
    assert ok and sid == "SMx"
    assert app.wa_status_callback_url() == "https://outdoorsquad.realtiq.ai/twilio-wa-status"
    assert posted[0]["StatusCallback"] == ["https://outdoorsquad.realtiq.ai/twilio-wa-status"]

    posted.clear()
    monkeypatch.setattr(app, "TWILIO_WA_WEBHOOK_URL", "")
    REAL_SEND("61452006342", "hello")
    assert app.wa_status_callback_url() == ""
    assert "StatusCallback" not in posted[0]


# ---- send first, persist second ---------------------------------------------

def test_failed_send_leaves_no_answer_in_the_transcript(wa, monkeypatch):
    calls = scripted_sender(monkeypatch, [(False, "HTTP 500: twilio is sad")])
    post(wa, "what are my options?")
    assert len(calls) == 2, "one classified retry, then stop"
    history = app.load_conversation(SID)
    assert not [t for t in history if t.get("role") == "assistant"]
    rows = [json.loads(l) for l in app.CONVERSATION_LOG_FILE.read_text().splitlines() if l.strip()]
    assert not [r for r in rows if r.get("role") == "assistant"]
    assert not events("wa_reply_sent") and not events("bot_reply_sent")
    lost = events("wa_reply_undelivered")
    assert lost and lost[0]["attempts"] == 2 and lost[0]["source"] == "rest"
    assert app.get_wa_setting(f"undelivered:{SID}") == "1"
    assert app._twilio_wa_seen_sids[f"SMrcpt{_n[0]:05d}"] is None


def test_failure_classification_decides_the_retry(wa, monkeypatch):
    assert app.wa_send_failure_class("HTTP 400: bad") == "permanent"
    assert app.wa_send_failure_class("HTTP 429: slow down") == "retry"
    assert app.wa_send_failure_class("HTTP 503: unavailable") == "retry"
    assert app.wa_send_failure_class("TimeoutError: timed out") == "unknown"
    assert app.wa_send_failure_class("twilio sending not configured") == "config"

    calls = scripted_sender(monkeypatch, [(False, "HTTP 400: bad number")])
    outcome, _detail, sent_parts, attempts = app._wa_send_and_register(
        SID, "61452006342", "hello", "reply")
    assert (outcome, sent_parts, attempts) == ("failed", 0, 1) and len(calls) == 1


def test_timeout_is_never_retried_and_is_not_an_undelivered_reply(wa, monkeypatch):
    calls = scripted_sender(monkeypatch, [(False, "TimeoutError: read timed out")])
    post(wa, "what are my options?")
    assert len(calls) == 1, "a timeout can hide a message Twilio already accepted"
    assert events("wa_send_unknown")
    assert not events("wa_reply_undelivered")
    history = app.load_conversation(SID)
    assert history[-1]["role"] == "assistant"
    assert app.get_wa_setting(f"undelivered:{SID}") == ""
    assert app.get_wa_setting(f"intro_failed:{SID}") == ""


def test_overloaded_twilio_is_retried_once_and_the_answer_lands(wa, monkeypatch):
    calls = scripted_sender(monkeypatch, [(False, "HTTP 429: too many"), (True, "SM2")])
    post(wa, "what are my options?")
    assert len(calls) == 2
    retried = events("wa_send_retry")
    assert retried and retried[0]["attempt"] == 1
    sent = events("wa_reply_sent")
    assert sent and sent[0]["message_sid"] == "SM2"
    assert app.load_conversation(SID)[-1]["role"] == "assistant"


def test_partial_split_keeps_the_part_that_landed(wa, monkeypatch):
    long_reply = ("y" * 1500 + "\n\n") + ("z" * 1500)
    monkeypatch.setattr(app, "generate_ai_reply", lambda m, s: (long_reply, "test"))
    calls = scripted_sender(monkeypatch, [(True, "SM1"), (False, "HTTP 400: nope")])
    post(wa, "what are my options?")
    assert len(calls) == 2
    history = app.load_conversation(SID)
    assert history[-1]["role"] == "assistant" and history[-1]["content"].startswith("y")
    partial = events("wa_reply_partial")
    assert partial and partial[0]["sent_parts"] == 1 and partial[0]["total_parts"] == 2
    assert app.get_wa_setting(f"undelivered:{SID}") == "1"
    # The intro DID land, so it is not owed again.
    assert app.get_wa_setting(f"intro_failed:{SID}") == ""


def test_split_parts_share_a_group_and_carry_a_redacted_preview(wa, monkeypatch):
    scripted_sender(monkeypatch, [(True, "SM1"), (True, "SM2")])
    body = "Call me on 0452 006 342 about it\n\n" + "z" * 1700
    app._wa_send_and_register(SID, "61452006342", body, "reply")
    ledger = app.wa_delivery_ledger(SID)
    assert [e["sid"] for e in ledger] == ["SM1", "SM2"]
    assert {e["group"] for e in ledger} == {"SM1"}
    assert all(len(e["preview"]) <= app.WA_DELIVERY_PREVIEW for e in ledger)
    assert "0452" not in ledger[0]["preview"] and "[phone]" in ledger[0]["preview"]


# ---- receipts ---------------------------------------------------------------

def test_failed_receipt_marks_the_thread_and_names_the_error_code(wa, monkeypatch):
    scripted_sender(monkeypatch, [(True, "SM1")])
    app._wa_send_and_register(SID, "61452006342", "hello", "reply")
    post_status(wa, "SM1", "failed", error_code="63016")
    entry = app.wa_delivery_ledger(SID)[-1]
    assert entry["status"] == "failed" and entry["error"] == "63016"
    assert app.get_wa_setting(f"undelivered:{SID}") == "1"
    lost = [e for e in events("wa_reply_undelivered") if e.get("source") == "receipt"]
    assert lost and lost[0]["error_code"] == "63016" and lost[0]["kind"] == "reply"


def test_delivered_never_downgrades_a_read_and_a_newer_sid_clears_the_flag(wa, monkeypatch):
    scripted_sender(monkeypatch, [(True, "SM1")])
    app._wa_send_and_register(SID, "61452006342", "first", "reply")
    post_status(wa, "SM1", "read")
    post_status(wa, "SM1", "delivered")
    assert app.wa_delivery_ledger(SID)[-1]["status"] == "read"

    app.set_wa_setting(f"undelivered:{SID}", "1")
    scripted_sender(monkeypatch, [(True, "SM2")])
    app._wa_send_and_register(SID, "61452006342", "second", "reply")
    post_status(wa, "SM2", "delivered")
    assert app.get_wa_setting(f"undelivered:{SID}") == ""

    # A late failure for the OLDER message must not silence a thread that has
    # since been answered.
    post_status(wa, "SM1", "failed", error_code="63024")
    assert app.get_wa_setting(f"undelivered:{SID}") == ""


def test_receipt_that_beats_its_registration_still_blocks_the_intro(wa, monkeypatch):
    post_status(wa, "SM1", "failed", error_code="63016")
    assert app.get_wa_setting(f"intro_failed:{SID}") == ""
    scripted_sender(monkeypatch, [(True, "SM1")])
    app._wa_send_and_register(SID, "61452006342", "G'day, Robo-Nick here", "intro")
    assert app.get_wa_setting(f"intro_failed:{SID}") == "1"
    assert app.wa_delivery_ledger(SID)[-1]["kind"] == "intro"


# ---- introduced on delivered ------------------------------------------------

def test_intro_is_resent_after_a_failed_receipt_but_not_after_a_plain_sent(wa, monkeypatch):
    scripted_sender(monkeypatch, [(True, "SM1")])
    post(wa, "hi")
    ledger = app.wa_delivery_ledger(SID)
    assert ledger and ledger[-1]["kind"] == "intro"

    post_status(wa, "SM1", "failed", error_code="63016")
    assert app.get_wa_setting(f"intro_failed:{SID}") == "1"
    assert app.wa_needs_intro(SID) is True
    assert "FIRST reply" in app.whatsapp_channel_prompt(SID)

    # The next opener is still a first contact, so it reaches the AI again and
    # the introduction is re-sent.
    scripted_sender(monkeypatch, [(True, "SM2")])
    r = post(wa, "hey")
    assert "<Message>" not in r.text, "answered by the AI over REST, not the ladder"
    assert app.wa_delivery_ledger(SID)[-1]["kind"] == "intro"
    post_status(wa, "SM2", "delivered")
    assert app.get_wa_setting(f"introduced:{SID}") == "1"
    assert app.get_wa_setting(f"intro_failed:{SID}") == ""
    assert app.wa_needs_intro(SID) is False


def test_a_missing_receipt_is_not_grounds_to_re_introduce(wa, monkeypatch):
    scripted_sender(monkeypatch, [(True, "SM1")])
    post(wa, "hi")
    post_status(wa, "SM1", "sent")  # accepted, not delivered: no receipt yet
    assert app.get_wa_setting(f"intro_failed:{SID}") == ""
    assert app.wa_needs_intro(SID) is False
    assert "already introduced yourself" in app.whatsapp_channel_prompt(SID)
    # The same opener that gets a re-introduction after a FAILED receipt gets
    # the scripted ladder here, answered inside the request: "sent" with no
    # receipt yet is not grounds to introduce Robo-Nick a second time.
    r = post(wa, "hey")
    assert "<Message>" in r.text
    assert app.wa_delivery_ledger(SID)[-1]["sid"] == "SM1"


# ---- manual replies and the dashboard ---------------------------------------

def test_manual_reply_is_registered_and_a_failed_one_stops_counting(wa, monkeypatch):
    post(wa, "what are my options?")  # opens the 24h window
    scripted_sender(monkeypatch, [(True, "SMman1")])
    r = wa.post("/api/wa/reply", json={"session_id": SID, "message": "Nick here, on it"},
                auth=("u", "p"))
    assert r.status_code == 200 and r.json()["delivery"] == "accepted"
    assert app.wa_delivery_ledger(SID)[-1]["kind"] == "manual"

    app.set_wa_mute(SID, None)
    scripted_sender(monkeypatch, [(True, "SMman2")])
    r = wa.post("/api/wa/reply", json={"session_id": SID, "message": "and one more thing"},
                auth=("u", "p"))
    assert r.status_code == 200
    post_status(wa, "SMman1", "undelivered", error_code="63032")
    stats = app.build_report_stats(7)
    assert stats["wa_manual_replies"] == 1, "the one WhatsApp refused is not a reply he sent"
    assert stats["wa_undelivered"] == 0, "a receipt failure is not Twilio refusing to send"


def test_report_counts_a_rest_failure_as_refused(wa, monkeypatch):
    scripted_sender(monkeypatch, [(False, "HTTP 400: nope")])
    post(wa, "what are my options?")
    assert app.build_report_stats(7)["wa_undelivered"] == 1


def test_dashboard_exposes_delivery_state_and_the_badge_markup_exists(wa, monkeypatch):
    scripted_sender(monkeypatch, [(True, "SM1")])
    post(wa, "what are my options?")
    post_status(wa, "SM1", "failed", error_code="63016")
    thread = next(t for t in app.wa_dashboard_payload()["conversations"]
                  if t["session_id"] == SID)
    assert thread["last_outbound_failed"] is True
    assert thread["deliveries"][-1]["status"] == "failed"
    html = wa.get("/admin", headers={"accept": "text/html"}, auth=("u", "p")).text
    assert "Not delivered" in html and "Last reply not delivered" in html
    assert "Sent, awaiting delivery." in html


def test_report_reader_says_when_the_window_hit_the_row_cap(wa, monkeypatch):
    from datetime import datetime, timedelta
    for i in range(5):
        app.log_event("widget_impression", session_id=f"widget-trunc-{i}")
    monkeypatch.setattr(app, "EVENTS_READ_LIMIT", 3)
    app._report_events_between(datetime.now() - timedelta(days=7), datetime.now())
    assert events("report_events_truncated")
