"""The WhatsApp reply must survive Twilio's 15-second webhook timeout.

Nick's very first message to the number was lost: the answer took ~18s, Twilio
had already hung up (error 11200), and the TwiML reply was discarded. The
webhook now acks instantly and delivers the answer over the REST API.
"""
import importlib
import os
import sys
import tempfile
from pathlib import Path

import pytest

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

_TMP = Path(tempfile.mkdtemp(prefix="os-waasync-"))
app.LEADS_FILE = _TMP / "leads.json"
app.LEADS_FILE.write_text("[]")
app.EVENTS_FILE = _TMP / "events.jsonl"
app.CONVERSATION_LOG_FILE = _TMP / "conversations.jsonl"


@pytest.fixture()
def events(monkeypatch):
    captured = []
    real = app.log_event
    monkeypatch.setattr(
        app, "log_event",
        lambda et, **kw: (captured.append({"event": et, **kw}), real(et, **kw))[0],
    )
    return captured


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "WA_STATE_FILE", tmp_path / "wa_state.json")
    monkeypatch.setattr(app, "_wa_capture_lead", lambda *a, **k: None)
    monkeypatch.setattr(app, "prevent_repetitive_reply", lambda r, m, s: r)
    monkeypatch.setattr(app, "log_chat_message", lambda *a, **k: None)
    monkeypatch.setattr(app, "persist_conversation", lambda *a, **k: None)
    monkeypatch.setattr(app, "log_bot_reply", lambda *a, **k: None)
    app.conversations.clear()
    yield


def test_slow_answer_is_delivered_by_rest(monkeypatch, events):
    """The exact regression: an answer slower than Twilio's timeout still lands."""
    monkeypatch.setattr(app, "generate_ai_reply", lambda m, s: ("Here are the times.", "claude"))
    sent = []
    monkeypatch.setattr(app, "send_whatsapp_via_twilio",
                        lambda to, body: (sent.append((to, body)), (True, "SMx"))[1])
    app._wa_generate_and_send("when are classes", "wa-61400111222", "61400111222", False, "SM1")
    assert sent == [("61400111222", "Here are the times.")]
    ev = [e for e in events if e["event"] == "wa_reply_sent"]
    assert ev and ev[0]["delivery"] == "rest"


def test_undelivered_answer_is_not_logged_as_sent(monkeypatch, events):
    """A generated reply that Twilio refused is a LOST reply, and the event log
    must say so: this whole incident was hidden by success-shaped logging."""
    monkeypatch.setattr(app, "generate_ai_reply", lambda m, s: ("Here are the times.", "claude"))
    monkeypatch.setattr(app, "send_whatsapp_via_twilio",
                        lambda to, body: (False, "HTTP 400: boom"))
    app._wa_generate_and_send("when are classes", "wa-61400111222", "61400111222", False, "SM2")
    assert not [e for e in events if e["event"] == "wa_reply_sent"]
    lost = [e for e in events if e["event"] == "wa_reply_undelivered"]
    assert lost and "HTTP 400" in lost[0]["error"]


def test_brain_failure_still_delivers_a_human_answer(monkeypatch):
    """If the AI dies, the person still gets words, over the same REST path."""
    def _boom(m, s):
        raise RuntimeError("provider down")
    monkeypatch.setattr(app, "generate_ai_reply", _boom)
    monkeypatch.setattr(app, "should_use_outage_fallback", lambda m: False)
    sent = []
    monkeypatch.setattr(app, "send_whatsapp_via_twilio",
                        lambda to, body: (sent.append(body), (True, "SMy"))[1])
    app._wa_generate_and_send("when are classes", "wa-61400111222", "61400111222", False, "SM3")
    assert len(sent) == 1 and sent[0].strip()


def test_reply_is_recorded_in_the_transcript(monkeypatch):
    """The answer must reach the transcript too, or the dashboard lies."""
    monkeypatch.undo()
    monkeypatch.setattr(app, "WA_STATE_FILE", Path(tempfile.mkdtemp()) / "s.json")
    monkeypatch.setattr(app, "_wa_capture_lead", lambda *a, **k: None)
    monkeypatch.setattr(app, "prevent_repetitive_reply", lambda r, m, s: r)
    monkeypatch.setattr(app, "log_chat_message", lambda *a, **k: None)
    monkeypatch.setattr(app, "persist_conversation", lambda *a, **k: None)
    monkeypatch.setattr(app, "log_bot_reply", lambda *a, **k: None)
    monkeypatch.setattr(app, "generate_ai_reply", lambda m, s: ("Recorded answer.", "claude"))
    monkeypatch.setattr(app, "send_whatsapp_via_twilio", lambda to, body: (True, "SMz"))
    app.conversations.clear()
    app.conversations["wa-61400555444"] = [{"role": "user", "content": "hi"}]
    app._wa_generate_and_send("hi", "wa-61400555444", "61400555444", False, "SM4")
    history = app.load_conversation("wa-61400555444")
    assert history[-1] == {"role": "assistant", "content": "Recorded answer."}
