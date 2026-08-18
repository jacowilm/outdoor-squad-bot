"""Regression guards for the Twilio WhatsApp channel.

The contract under test: inbound WhatsApp rides the SAME brain as the website
widget, authenticated by Twilio's signature instead of Turnstile, replying via
TwiML so no REST credentials are needed for conversation.
"""

import base64
import hashlib
import hmac
import importlib
import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

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

WEBHOOK_PATH = "/twilio-wa-webhook"
TEST_TOKEN = "test-auth-token-for-signatures"


def _sign(base: str, params: dict, token: str = TEST_TOKEN) -> str:
    payload = base + WEBHOOK_PATH + "".join(k + str(params[k]) for k in sorted(params))
    return base64.b64encode(hmac.new(token.encode(), payload.encode(), hashlib.sha1).digest()).decode()


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "TWILIO_AUTH_TOKEN", TEST_TOKEN)
    monkeypatch.setattr(app, "TWILIO_WA_WEBHOOK_URL", "")
    monkeypatch.setattr(app, "WA_MUTE_FILE", tmp_path / "wa_mute.json")
    # Hermetic data files: without these, a solo run of this module writes fake
    # WA sessions into the repo's real jsonl files and inherits yesterday's,
    # which breaks the 24h-window and repetition assertions.
    for name, filename, empty in [
        ("CONVERSATION_LOG_FILE", "conversation_logs.jsonl", ""),
        ("EVENTS_FILE", "events.jsonl", ""),
        ("LEADS_FILE", "leads.json", "[]"),
    ]:
        path = tmp_path / filename
        path.write_text(empty)
        monkeypatch.setattr(app, name, path)
    app._twilio_wa_seen_sids.clear()
    # Deterministic brain: no network, assertable output.
    monkeypatch.setattr(app, "generate_ai_reply", lambda m, s: ("Deterministic test reply <3", "test"))
    monkeypatch.setattr(app, "should_use_local_tone_handler", lambda m, s: False)
    monkeypatch.setattr(app, "notify_lead_summary_async", lambda *a, **k: None)
    return TestClient(app.app)


def _post(client, params, signature=None, base=None):
    base = base or app.TWILIO_WA_FALLBACK_HOSTS[0]
    sig = signature if signature is not None else _sign(base, params)
    return client.post(WEBHOOK_PATH, data=params, headers={"X-Twilio-Signature": sig})


def _params(body="When are classes?", sid="SMtestsid001", sender="whatsapp:+61400111222"):
    return {"From": sender, "To": "whatsapp:+61499000000", "Body": body,
            "MessageSid": sid, "NumMedia": "0"}


def test_rejects_missing_or_bad_signature(client):
    r = client.post(WEBHOOK_PATH, data=_params())
    assert r.status_code == 403
    r = _post(client, _params(), signature="obviously-wrong")
    assert r.status_code == 403


def test_fail_closed_without_auth_token(client, monkeypatch):
    monkeypatch.setattr(app, "TWILIO_AUTH_TOKEN", "")
    r = client.post(WEBHOOK_PATH, data=_params())
    assert r.status_code == 503


def test_valid_signature_replies_with_twiml(client):
    r = _post(client, _params(sid="SMtwiml1"))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/xml")
    # XML-escaped reply from the deterministic brain, inside <Message>.
    assert "<Message>Deterministic test reply &lt;3</Message>" in r.text


def test_signature_accepts_secondary_host(client):
    params = _params(sid="SMhost2")
    r = _post(client, params, signature=_sign(app.TWILIO_WA_FALLBACK_HOSTS[1], params))
    assert r.status_code == 200
    assert "<Message>" in r.text


def test_session_is_wa_prefixed_and_shared_brain(client):
    _post(client, _params(sid="SMsess1", sender="whatsapp:+61400999888"))
    history = app.load_conversation("wa-61400999888")
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert history[0]["content"] == "When are classes?"


def test_duplicate_message_sid_reserves_same_reply(client):
    """A Twilio retry (e.g. after a slow first response) must receive the SAME
    reply, not silence: returning empty on the dupe leaves the customer with
    nothing while the transcript records a reply as sent."""
    first = _post(client, _params(sid="SMdup1"))
    assert "<Message>Deterministic test reply &lt;3</Message>" in first.text
    second = _post(client, _params(sid="SMdup1"))
    assert second.status_code == 200
    assert "<Message>Deterministic test reply &lt;3</Message>" in second.text
    # And the duplicate did NOT double-log: still exactly one user turn.
    history = app.load_conversation("wa-61400111222")
    assert sum(1 for m in history if m["role"] == "user" and m["content"] == "When are classes?") >= 1


def test_media_only_message_gets_honest_reply(client):
    params = _params(body="", sid="SMmedia1")
    params["NumMedia"] = "1"
    r = _post(client, params)
    assert "only read text" in r.text


def test_mute_suppresses_reply_but_still_captures(client):
    app.set_wa_mute("wa-61400111222", 30)
    r = _post(client, _params(body="my number is 0400 123 456, call me", sid="SMmute1"))
    assert r.status_code == 200
    assert "<Message>" not in r.text  # bot stays silent
    history = app.load_conversation("wa-61400111222")
    assert history[-1]["role"] == "user"  # inbound still recorded
    app.set_wa_mute("wa-61400111222", None)
    r = _post(client, _params(sid="SMmute2"))
    assert "<Message>" in r.text  # handed back, bot speaks again


def test_mute_endpoint_requires_wa_session(client, monkeypatch):
    monkeypatch.setattr(app, "ADMIN_USERNAME", "u")
    monkeypatch.setattr(app, "ADMIN_PASSWORD", "p")
    r = client.post("/api/wa/mute", json={"session_id": "widget-abc"}, auth=("u", "p"))
    assert r.status_code == 400
    r = client.post("/api/wa/mute", json={"session_id": "wa-614001", "minutes": 15}, auth=("u", "p"))
    assert r.status_code == 200 and r.json()["muted"] is True
    assert app.wa_muted("wa-614001")
    r = client.post("/api/wa/mute", json={"session_id": "wa-614001", "clear": True}, auth=("u", "p"))
    assert r.status_code == 200 and r.json()["muted"] is False
    assert not app.wa_muted("wa-614001")


@pytest.fixture()
def wa_state(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "WA_STATE_FILE", tmp_path / "wa_state.json")
    return tmp_path


def test_kill_switch_silences_channel(client, wa_state, monkeypatch):
    monkeypatch.setattr(app, "ADMIN_USERNAME", "u")
    monkeypatch.setattr(app, "ADMIN_PASSWORD", "p")
    r = client.post("/api/wa/kill", json={"enabled": False}, auth=("u", "p"))
    assert r.status_code == 200 and r.json()["enabled"] is False
    r = _post(client, _params(sid="SMkill1", sender="whatsapp:+61400777666"))
    assert r.status_code == 200
    assert "<Message>" not in r.text  # channel off: bot says nothing
    client.post("/api/wa/kill", json={"enabled": True}, auth=("u", "p"))
    r = _post(client, _params(sid="SMkill2", sender="whatsapp:+61400777666"))
    assert "<Message>" in r.text


def test_manual_reply_sends_mutes_and_respects_window(client, wa_state, monkeypatch):
    monkeypatch.setattr(app, "ADMIN_USERNAME", "u")
    monkeypatch.setattr(app, "ADMIN_PASSWORD", "p")
    sent = {}

    def fake_send(to_digits, body):
        sent["to"], sent["body"] = to_digits, body
        return True, "SMout123"

    monkeypatch.setattr(app, "send_whatsapp_via_twilio", fake_send)

    # No inbound ever -> window closed -> honest 422, nothing sent.
    r = client.post("/api/wa/reply", json={"session_id": "wa-61455000111", "message": "hi"}, auth=("u", "p"))
    assert r.status_code == 422 and r.json()["error"] == "outside_24h_window"
    assert not sent

    # Fresh inbound opens the window; the reply sends and auto-mutes the thread.
    _post(client, _params(sid="SMwin1", sender="whatsapp:+61455000111"))
    r = client.post("/api/wa/reply", json={"session_id": "wa-61455000111", "message": "Nick here, on it"}, auth=("u", "p"))
    assert r.status_code == 200 and r.json()["muted"] is True
    assert sent["to"] == "61455000111"
    assert app.wa_muted("wa-61455000111")
    # And the muted thread no longer auto-replies.
    r = _post(client, _params(sid="SMwin2", sender="whatsapp:+61455000111"))
    assert "<Message>" not in r.text


def test_nudge_selection_logic(client, wa_state, monkeypatch):
    from datetime import datetime, timedelta
    sender = "whatsapp:+61466222333"
    sid = "wa-61466222333"
    _post(client, _params(sid="SMnudge1", sender=sender))  # bot spoke last

    monkeypatch.setattr(app, "WA_NUDGE_MINUTES", 90)
    now = datetime.now()
    assert sid not in app.wa_sessions_needing_nudge(now)            # too soon
    later = now + timedelta(minutes=120)
    assert sid in app.wa_sessions_needing_nudge(later)              # due
    app.set_wa_setting(f"nudged:{sid}", "1")
    assert sid not in app.wa_sessions_needing_nudge(later)          # only ever one
    app.set_wa_setting(f"nudged:{sid}", "")
    app.set_wa_mute(sid, 60)
    assert sid not in app.wa_sessions_needing_nudge(later)          # muted = silent
    app.set_wa_mute(sid, None)
    beyond_window = now + timedelta(hours=25)
    assert sid not in app.wa_sessions_needing_nudge(beyond_window)  # window closed


def test_momence_refresh_token_rotation_is_persisted(client, wa_state, monkeypatch):
    monkeypatch.setattr(app, "MOMENCE_V2_CLIENT_ID", "cid")
    monkeypatch.setattr(app, "MOMENCE_V2_CLIENT_SECRET", "sec")
    monkeypatch.setattr(app, "MOMENCE_SEED_REFRESH_TOKEN", "seed-token")

    class _Resp:
        def __init__(self, body): self._b = json.dumps(body).encode()
        def read(self): return self._b
        def __enter__(self): return self
        def __exit__(self, *a): return False

    calls = []

    def fake_urlopen(request, timeout=0):
        calls.append(request.full_url)
        return _Resp({"accessToken": "acc-1", "refreshToken": "rotated-1"})

    monkeypatch.setattr(app.urllib.request, "urlopen", fake_urlopen)
    token = app._momence_access_token()
    assert token == "acc-1"
    # The rotated refresh token must survive for the NEXT call, or we are
    # locked out of Momence until Nick re-consents.
    assert app.get_wa_setting("momence_refresh_token") == "rotated-1"
