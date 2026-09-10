"""Public endpoints must never act on a WhatsApp session id.

11 Sep 2026 website-vs-WhatsApp diff, finding #1: /api/chat and /api/event
accepted any client-supplied session id, including "wa-<mobile>", which is
minted only by the signed Twilio webhook. Anyone who guessed a customer's
mobile could read their WhatsApp thread back through the model and append
turns to it. Finding #36: the webhook answered messages addressed to any
number on the Twilio account.
"""
import base64
import hashlib
import hmac
import importlib
import os
import sys

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

TEST_TOKEN = "test-auth-token-for-signatures"
WEBHOOK_PATH = "/twilio-wa-webhook"


@pytest.fixture()
def client(monkeypatch, tmp_path):
    for name, filename, empty in [
        ("CONVERSATION_LOG_FILE", "conversation_logs.jsonl", ""),
        ("EVENTS_FILE", "events.jsonl", ""),
        ("LEADS_FILE", "leads.json", "[]"),
    ]:
        path = tmp_path / filename
        path.write_text(empty)
        monkeypatch.setattr(app, name, path)
    monkeypatch.setattr(app, "WA_STATE_FILE", tmp_path / "wa_state.json")
    monkeypatch.setattr(app, "TWILIO_AUTH_TOKEN", TEST_TOKEN)
    monkeypatch.setattr(app, "TWILIO_WA_WEBHOOK_URL", "")
    monkeypatch.setattr(app, "generate_ai_reply", lambda m, s: ("Deterministic test reply", "test"))
    monkeypatch.setattr(app, "should_use_local_tone_handler", lambda m, s, **k: False)
    monkeypatch.setattr(app, "notify_lead_summary_async", lambda *a, **k: None)
    monkeypatch.setattr(app, "_wa_async_dispatch", lambda target, args: target(*args))
    monkeypatch.setattr(app, "send_whatsapp_via_twilio", lambda to, body: (True, "SMfake"))
    monkeypatch.setattr(app, "is_rate_limited", lambda *a, **k: False)
    app.conversations.clear()
    app._twilio_wa_seen_sids.clear()
    return TestClient(app.app)


def _sign(params: dict) -> str:
    base = app.TWILIO_WA_FALLBACK_HOSTS[0]
    payload = base + WEBHOOK_PATH + "".join(k + str(params[k]) for k in sorted(params))
    return base64.b64encode(hmac.new(TEST_TOKEN.encode(), payload.encode(), hashlib.sha1).digest()).decode()


def _wa_post(client, body, sender="whatsapp:+61400111222", to="whatsapp:+61499000000", sid="SMbound1"):
    params = {"From": sender, "To": to, "Body": body, "MessageSid": sid, "NumMedia": "0"}
    return client.post(WEBHOOK_PATH, data=params, headers={"X-Twilio-Signature": _sign(params)})


def test_public_chat_cannot_open_a_whatsapp_thread(client):
    # A real WhatsApp customer has a thread with private content.
    _wa_post(client, "I hurt my knee last year, is that a problem?")
    assert any(m["role"] == "user" and "knee" in m["content"]
               for m in app.load_conversation("wa-61400111222"))

    # A public caller who guessed the mobile gets a FRESH anonymous session,
    # never that thread.
    r = client.post("/api/chat", json={"message": "what did I tell you earlier?",
                                       "session_id": "wa-61400111222"})
    assert r.status_code == 200
    returned = r.json()["session_id"]
    assert returned.startswith("s-") and returned != "wa-61400111222"
    thread = app.load_conversation("wa-61400111222")
    assert not any("what did I tell you" in m["content"] for m in thread), \
        "public caller appended a turn to the WhatsApp thread"
    assert app.load_conversation(returned)[-2]["content"] == "what did I tell you earlier?"


def test_public_chat_reserved_prefix_is_case_insensitive(client):
    r = client.post("/api/chat", json={"message": "hi", "session_id": "WA-61400111222"})
    assert r.json()["session_id"].startswith("s-")


def test_public_chat_keeps_ordinary_client_ids(client):
    r = client.post("/api/chat", json={"message": "hi", "session_id": "widget-abc123"})
    assert r.json()["session_id"] == "widget-abc123"


def test_public_event_cannot_touch_a_whatsapp_session(client):
    _wa_post(client, "hello", sid="SMbound2")
    before = app.EVENTS_FILE.read_text()
    r = client.post("/api/event", json={"event_type": "trial_link_clicked",
                                        "session_id": "wa-61400111222",
                                        "metadata": {"url": app.TRIAL_LINK}})
    assert r.status_code == 200
    after = app.EVENTS_FILE.read_text()
    new_lines = after[len(before):]
    assert "wa-61400111222" not in new_lines
    assert "session_id_rejected_reserved" in new_lines
    assert "trial_link_clicked" not in new_lines


def test_webhook_ignores_messages_for_another_number(client, monkeypatch):
    monkeypatch.setattr(app, "TWILIO_WA_FROM", "+61499000000")
    r = _wa_post(client, "hi", to="whatsapp:+15550001111", sid="SMbound3")
    assert r.status_code == 200 and "<Message>" not in r.text
    assert app.load_conversation("wa-61400111222") == []
    assert "wa_twilio_wrong_recipient" in app.EVENTS_FILE.read_text()
    # The configured sender still works.
    r = _wa_post(client, "hi", to="whatsapp:+61499000000", sid="SMbound4")
    assert app.load_conversation("wa-61400111222")[-1]["role"] == "assistant"
