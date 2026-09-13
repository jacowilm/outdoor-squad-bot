"""Webhook-level regression guards for the 2026-09-13 executor pass.

Unlike tests/test_twilio_wa_webhook_2026_08.py, these do NOT stub
should_use_local_tone_handler — they exercise the REAL gate end-to-end through
the signed /twilio-wa-webhook so a routing regression (a keyword branch
swallowing an identity/youth/cold-open question, or a price answer losing its
booking link / student tier / gaining a "Roughly" hedge) shows up as a failing
assertion on the actual TwiML the customer would receive, not a mocked stand-in.

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


def _reply_text(client, body, sid, sender):
    r = _post(client, _params(body, sid, sender))
    assert r.status_code == 200
    assert "<Message>" in r.text, (
        f"expected an inline deterministic TwiML reply for {body!r}, got: {r.text!r}"
    )
    return app.load_conversation("wa-" + "".join(c for c in sender if c.isdigit()))[-1]["content"]


# ---- RC findings: real gate must still produce the tuned answer -----------

def test_pricing_question_keeps_booking_link_student_tier_no_roughly_hedge(client):
    reply = _reply_text(client, "roughly what does it cost?", "SMrc1", "whatsapp:+61400200001")
    assert app.TRIAL_LINK in reply
    assert "Squad Student" in reply
    assert "roughly" not in reply.lower()


def test_timetable_question_includes_booking_link(client):
    reply = _reply_text(client, "what time are classes on Tuesday?", "SMrc2", "whatsapp:+61400200002")
    assert app.TRIAL_LINK in reply
    assert "tuesday" in reply.lower()


def test_parking_question_gets_real_parking_answer(client):
    reply = _reply_text(client, "is there parking at Camperdown?", "SMrc3", "whatsapp:+61400200003")
    assert "parking" in reply.lower()
    assert "australia st" in reply.lower()


# ---- identity / youth / cold-open regressions (2026-09-13 executor pass) --

def test_are_you_a_whatsapp_bot_gets_identity_answer_not_social_links(client):
    reply = _reply_text(client, "are you a WhatsApp bot?", "SMid1", "whatsapp:+61400200004")
    assert "robo-nick" in reply.lower()
    assert "instagram.com" not in reply
    assert "facebook.com" not in reply


def test_messaging_about_son_he_is_12_is_youth_not_aging(client):
    reply = _reply_text(client, "I'm messaging about my son, he's 12", "SMid2", "whatsapp:+61400200005")
    assert "groceries when you" not in reply
    assert "long game" not in reply
    assert "youth" in reply.lower() or "10" in reply


def test_cold_open_is_this_the_outdoor_squad_introduces_robo_nick(client):
    reply = _reply_text(client, "hi, is this the Outdoor Squad?", "SMid3", "whatsapp:+61400200006")
    assert "robo-nick" in reply.lower()
    assert "outdoor squad" in reply.lower()
