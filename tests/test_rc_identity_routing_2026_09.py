"""Webhook-level regression guards for the 2026-09-13 executor pass.

Unlike tests/test_twilio_wa_webhook_2026_08.py, these do NOT stub
should_use_local_tone_handler — they exercise the REAL gate end-to-end through
the signed /twilio-wa-webhook so a routing regression (a keyword branch
swallowing an identity/youth/cold-open question, or a price answer losing its
booking link / student tier / gaining a "Roughly" hedge) shows up as a failing
assertion on the actual TwiML the customer would receive.

Assertions read the ACTUAL <Message> text out of the XML response body (via
xml.etree.ElementTree), not just the stored transcript copy — a bug that
corrupted the TwiML but not the persisted copy would otherwise pass silently
(2026-09-13 parent review finding #2).

generate_ai_reply is stubbed only as a safety net: every message below is
expected to resolve locally, so the stub asserts it is never invoked (no paid
AI calls, no live transport, nothing sent to a real WhatsApp number).
"""

import base64
import hashlib
import hmac
import importlib
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

for key in [
    "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_KEY",
    "OUTDOOR_SQUAD_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY",
    "OUTDOOR_SQUAD_OPENAI_API_KEY", "OPENAI_API_KEY",
    "OUTDOOR_SQUAD_GEMINI_API_KEY",
]:
    os.environ.pop(key, None)

import app  # noqa: E402

importlib.reload(app)
app.SUPABASE_URL = ""

WEBHOOK_PATH = "/twilio-wa-webhook"
TEST_TOKEN = "test-auth-token-for-signatures"


def _sign(base: str, params: dict, token: str = TEST_TOKEN) -> str:
    payload = base + WEBHOOK_PATH + "".join(k + str(params[k]) for k in sorted(params))
    return base64.b64encode(hmac.new(token.encode(), payload.encode(), hashlib.sha1).digest()).decode()


def _params(body, sid, sender):
    return {"From": sender, "To": "whatsapp:+61499000000", "Body": body,
            "MessageSid": sid, "NumMedia": "0"}


def _post(client, params):
    sig = _sign(app.TWILIO_WA_FALLBACK_HOSTS[0], params)
    return client.post(WEBHOOK_PATH, data=params, headers={"X-Twilio-Signature": sig})


def _twiml_message_text(response_text: str) -> str:
    """Parse the actual XML body and return the <Message> element's text, or
    "" if there is none. Raises if the body is not well-formed XML — a
    malformed TwiML response must fail the test, not be silently skipped."""
    root = ET.fromstring(response_text)
    node = root.find("Message")
    return node.text or "" if node is not None else ""


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "TWILIO_AUTH_TOKEN", TEST_TOKEN)
    monkeypatch.setattr(app, "TWILIO_WA_WEBHOOK_URL", "")
    monkeypatch.setattr(app, "WA_MUTE_FILE", tmp_path / "wa_mute.json")
    for name, filename, empty in [
        ("CONVERSATION_LOG_FILE", "conversation_logs.jsonl", ""),
        ("EVENTS_FILE", "events.jsonl", ""),
        ("LEADS_FILE", "leads.json", "[]"),
    ]:
        path = tmp_path / filename
        path.write_text(empty)
        monkeypatch.setattr(app, name, path)
    app._twilio_wa_seen_sids.clear()
    app.conversations.clear()

    def _unexpected_ai_call(message, session_id):
        raise AssertionError(
            f"generate_ai_reply was called for {message!r} — this message should "
            "have resolved through should_use_local_tone_handler / demo_fallback_reply."
        )
    monkeypatch.setattr(app, "generate_ai_reply", _unexpected_ai_call)
    monkeypatch.setattr(app, "notify_lead_summary_async", lambda *a, **k: None)
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        app, "send_whatsapp_via_twilio",
        lambda to_digits, body: (sent.append((to_digits, body)), (True, "SMfake"))[1],
    )
    test_client = TestClient(app.app)
    test_client.wa_sent = sent
    return test_client


_counter = 0


def _reply_text(client, body, sender):
    """POST one message with a fresh MessageSid/sender pair and return the
    ACTUAL text parsed out of the TwiML <Message> element."""
    global _counter
    _counter += 1
    r = _post(client, _params(body, f"SM{_counter:06d}", sender))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/xml")
    text = _twiml_message_text(r.text)
    assert text, (
        f"expected an inline deterministic TwiML reply for {body!r}, got: {r.text!r}"
    )
    return text


def _sender(n: int) -> str:
    return f"whatsapp:+6140020{n:04d}"


# ---- RC findings: real gate must still produce the tuned answer, split one
# assertion per finding so a single regression names exactly what broke -----

def test_pricing_answer_includes_booking_link(client):
    reply = _reply_text(client, "roughly what does it cost?", _sender(1))
    assert app.TRIAL_LINK in reply


def test_pricing_answer_includes_student_tier(client):
    reply = _reply_text(client, "roughly what does it cost?", _sender(2))
    assert "Squad Student" in reply


def test_pricing_answer_has_no_roughly_hedge(client):
    reply = _reply_text(client, "roughly what does it cost?", _sender(3))
    assert "roughly" not in reply.lower()


def test_general_timetable_answer_includes_booking_link(client):
    # Exact phrase from the is_timetable_question keyword list, not a
    # day-specific query, so this exercises the GENERAL timetable branch.
    reply = _reply_text(client, "what's the timetable?", _sender(4))
    assert app.TRIAL_LINK in reply
    assert "Youth Training Program" in reply


def test_bare_parking_question_gets_real_parking_facts(client):
    # Exact RC phrasing: "Do you have parking?" with no venue named, which
    # must answer with the real both-venues parking facts, not a generic
    # deflection or a made-up single answer.
    reply = _reply_text(client, "Do you have parking?", _sender(5))
    assert "parking" in reply.lower()
    assert "australia st" in reply.lower() or "chalmers st" in reply.lower()


def test_venue_specific_parking_question_gets_that_venues_parking_facts(client):
    reply = _reply_text(client, "is there parking at Camperdown?", _sender(6))
    assert "parking" in reply.lower()
    assert "australia st" in reply.lower()


# ---- identity / cold-open regressions (2026-09-13 executor pass) ----------

def test_are_you_a_whatsapp_bot_gets_identity_answer_not_social_links(client):
    reply = _reply_text(client, "are you a WhatsApp bot?", _sender(7))
    assert "robo-nick" in reply.lower()
    assert "instagram.com" not in reply
    assert "facebook.com" not in reply


def test_cold_open_is_this_the_outdoor_squad_introduces_robo_nick(client):
    reply = _reply_text(client, "hi, is this the Outdoor Squad?", _sender(8))
    assert "robo-nick" in reply.lower()
    assert "outdoor squad" in reply.lower()


def test_messaging_about_son_is_youth_not_aging(client):
    reply = _reply_text(client, "I'm messaging about my son, he's 12", _sender(9))
    assert "groceries when you" not in reply
    assert "long game" not in reply
    # Tightened per parent review: assert the actual youth-program answer,
    # not an incidental digit that could appear in unrelated copy.
    assert "Youth Training Program" in reply


# ---- safety/youth/handoff MUST win over a mixed greeting/identity phrase
# (2026-09-13 parent review finding #1: the first version of the identity and
# cold-open branches sat ABOVE eating-disorder/pregnancy/injury/youth/human-
# handoff guards, so a mixed message like "is this the Outdoor Squad? I have
# an eating disorder" got a cheerful confirmation instead of the safety
# handoff.) -------------------------------------------------------------

def test_cold_open_with_eating_disorder_gets_eating_disorder_handoff(client):
    reply = _reply_text(
        client, "hi, is this the Outdoor Squad? I have an eating disorder", _sender(10)
    )
    assert "butterfly foundation" in reply.lower()
    assert "yep, this is the outdoor squad" not in reply.lower()


def test_identity_question_with_eating_disorder_gets_eating_disorder_handoff(client):
    reply = _reply_text(
        client, "are you a WhatsApp bot? I have an eating disorder", _sender(11)
    )
    assert "butterfly foundation" in reply.lower()
    assert "robo-nick, the automated helper" not in reply.lower()


def test_cold_open_with_pregnancy_gets_pregnancy_handoff(client):
    reply = _reply_text(client, "is this the Outdoor Squad? I am pregnant", _sender(12))
    assert "not a robo-nick call" in reply.lower()
    assert "yep, this is the outdoor squad" not in reply.lower()


def test_cold_open_with_youth_gets_youth_answer(client):
    reply = _reply_text(client, "is this the Outdoor Squad? my son is 12", _sender(13))
    assert "Youth Training Program" in reply
    assert "yep, this is the outdoor squad" not in reply.lower()
