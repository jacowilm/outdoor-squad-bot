"""Voice + SMS webhooks for the business number.

They exist so the GBP-listed phone can become the Twilio number without
killing the Call button. No missed-call text-back here: that is a separately
quoted product, so the no-answer path only speaks.
"""
import os
import sys
import tempfile
import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _k in (
    "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_KEY",
    "OUTDOOR_SQUAD_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY",
    "OUTDOOR_SQUAD_OPENAI_API_KEY", "OPENAI_API_KEY",
    "OUTDOOR_SQUAD_GEMINI_API_KEY", "GEMINI_API_KEY",
):
    os.environ.pop(_k, None)

import app  # noqa: E402
importlib.reload(app)
app.SUPABASE_URL = ""
app.SUPABASE_KEY = None

_TMP = Path(tempfile.mkdtemp(prefix="os-voicesms-"))
app.LEADS_FILE = _TMP / "leads.json"
app.LEADS_FILE.write_text("[]")
app.EVENTS_FILE = _TMP / "events.jsonl"
app.CONVERSATION_LOG_FILE = _TMP / "conversations.jsonl"

client = TestClient(app.app)


@pytest.fixture(autouse=True)
def _twilio_env(monkeypatch):
    monkeypatch.setattr(app, "TWILIO_AUTH_TOKEN", "test-token")
    yield


def test_voice_rejects_bad_signature(monkeypatch):
    monkeypatch.setattr(app, "twilio_signature_valid", lambda *a: False)
    r = client.post("/twilio-voice-webhook", data={"From": "+61400000000"})
    assert r.status_code == 403


def test_voice_dials_the_forward_target_then_speaks(monkeypatch):
    monkeypatch.setattr(app, "twilio_signature_valid", lambda *a: True)
    monkeypatch.setattr(app, "VOICE_FORWARD_TO", "+61452006342")
    r = client.post("/twilio-voice-webhook", data={"From": "+61400000000"})
    assert r.status_code == 200
    assert 'Dial timeout="25"' in r.text
    assert "+61452006342" in r.text
    assert "<Say>" in r.text
    assert "Sms" not in r.text          # no text-back: separately quoted product


def test_sms_logs_and_emails_without_auto_reply(monkeypatch):
    monkeypatch.setattr(app, "twilio_signature_valid", lambda *a: True)
    monkeypatch.setattr(app, "LEAD_SUMMARY_EMAIL_TO", "owner@example.com")
    sent = []
    monkeypatch.setattr(app, "send_email_resend",
                        lambda subject, body, recipients, html=None: sent.append((subject, body, recipients)) or True)
    r = client.post("/twilio-sms-webhook", data={"From": "+61411222333", "Body": "hola, is there a class today?"})
    assert r.status_code == 200
    assert "<Message>" not in r.text     # empty TwiML = zero outbound SMS cost
    import time
    for _ in range(50):
        if sent: break
        time.sleep(0.05)
    assert sent and "+61411222333" in sent[0][0]
    assert "is there a class today?" in sent[0][1]
    assert sent[0][2] == ["owner@example.com"]


def test_sms_rejects_bad_signature(monkeypatch):
    monkeypatch.setattr(app, "twilio_signature_valid", lambda *a: False)
    r = client.post("/twilio-sms-webhook", data={"From": "+61400000000", "Body": "x"})
    assert r.status_code == 403
