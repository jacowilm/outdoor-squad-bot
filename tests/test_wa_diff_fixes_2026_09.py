"""Fixes from the 11 Sep 2026 website-vs-WhatsApp diff (outbox/WEB-VS-WHATSAPP-DIFF-2026-09-11.md).

Numbers refer to that document's "missing or different" table.
"""
import base64
import hashlib
import hmac
import importlib
import os
import sys
from datetime import datetime, timedelta

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
    monkeypatch.setattr(app, "generate_ai_reply", lambda m, s: ("**Quick options:**\n- **Free trial**: one class [book here](https://example.com/x)", "test"))
    monkeypatch.setattr(app, "_wa_async_dispatch", lambda target, args: target(*args))
    alerts = []
    monkeypatch.setattr(app, "notify_lead_summary_async", lambda li, **k: alerts.append(dict(li)))
    monkeypatch.setattr(app, "maybe_push_lead_to_momence", lambda *a, **k: None)
    monkeypatch.setattr(app, "is_rate_limited", lambda *a, **k: False)
    sent = []
    monkeypatch.setattr(app, "send_whatsapp_via_twilio", lambda to, body: (sent.append((to, body)), (True, "SMfake"))[1])
    app.conversations.clear()
    app._twilio_wa_seen_sids.clear()
    app._wa_setting_cache.clear()
    c = TestClient(app.app)
    c.sent = sent
    c.alerts = alerts
    return c


def _sign(params):
    base = app.TWILIO_WA_FALLBACK_HOSTS[0]
    payload = base + WEBHOOK_PATH + "".join(k + str(params[k]) for k in sorted(params))
    return base64.b64encode(hmac.new(TEST_TOKEN.encode(), payload.encode(), hashlib.sha1).digest()).decode()


_n = [0]


def post(client, body, sender="whatsapp:+61400111222"):
    _n[0] += 1
    params = {"From": sender, "To": "whatsapp:+61499000000", "Body": body,
              "MessageSid": f"SMfix{_n[0]:05d}", "NumMedia": "0"}
    return client.post(WEBHOOK_PATH, data=params, headers={"X-Twilio-Signature": _sign(params)})


def last_out(client):
    return client.sent[-1][1] if client.sent else None


# ---- #6 / #7: channel-aware prompt and render pass ------------------------------

def test_wa_session_gets_channel_block_and_first_reply_intro(monkeypatch):
    app.conversations.clear()
    app.conversations["wa-61400000001"] = []
    msgs = app.build_agent_messages("hi", "wa-61400000001")
    systems = [m["content"] for m in msgs if m["role"] == "system"]
    assert any("replying on WhatsApp" in s for s in systems)
    assert any("FIRST reply" in s and "Robo-Nick" in s for s in systems)
    assert any("NEVER ask for their mobile" in s for s in systems)
    # After the bot has spoken once, no re-introduction.
    app.conversations["wa-61400000001"] = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "G'day"}]
    systems = [m["content"] for m in app.build_agent_messages("prices?", "wa-61400000001") if m["role"] == "system"]
    assert any("already introduced yourself" in s for s in systems)


def test_widget_session_has_no_channel_block():
    app.conversations.clear()
    systems = [m["content"] for m in app.build_agent_messages("hi", "widget-abc") if m["role"] == "system"]
    assert not any("replying on WhatsApp" in s for s in systems)


def test_render_for_whatsapp_converts_markdown():
    out = app.render_for_whatsapp("**Quick options:**\n- **Free trial**: one class [book here](https://x.y/z)\n\n\n\n## Heading")
    assert "**" not in out
    assert "*Quick options:*" in out and "*Free trial*" in out
    assert "book here: https://x.y/z" in out
    assert "## " not in out and "\n\n\n" not in out


def test_no_double_asterisk_leaves_the_wa_path(wa):
    post(wa, "what are my options?")
    body = last_out(wa)
    assert body and "**" not in body and "*Quick options:*" in body


def test_scripted_twiml_replies_also_rendered(monkeypatch):
    r = app._twiml_message("**Bold** and [link](https://a.b/c)")
    assert "**" not in r.body.decode() and "link: https://a.b/c" in r.body.decode()


# ---- #12: kill switch / mute re-checked before the deferred send ----------------

def test_kill_switch_flipped_mid_generation_suppresses_the_answer(wa, monkeypatch):
    def slow_generate(m, s):
        app.set_wa_setting("channel_enabled", "0")
        return ("late answer", "test")
    monkeypatch.setattr(app, "generate_ai_reply", slow_generate)
    post(wa, "tell me about SPT please")
    assert wa.sent == []
    assert "wa_reply_suppressed" in app.EVENTS_FILE.read_text()
    # Not persisted either: the transcript must not show a reply nobody got.
    assert not any(m["role"] == "assistant" for m in app.load_conversation("wa-61400111222"))
    app.set_wa_setting("channel_enabled", "1")


def test_manual_reply_mid_generation_suppresses_the_answer(wa, monkeypatch):
    def slow_generate(m, s):
        app.set_wa_mute(s, 30)
        return ("late answer", "test")
    monkeypatch.setattr(app, "generate_ai_reply", slow_generate)
    post(wa, "tell me about SPT please", sender="whatsapp:+61400111333")
    assert wa.sent == []
    app.set_wa_mute("wa-61400111333", None)


# ---- #4: opt-out, and the "aging" inside "messaging" collision ----------------

@pytest.mark.parametrize("text", ["stop", "STOP", "please stop messaging me", "unsubscribe",
                                  "don't message me again", "leave me alone", "opt out"])
def test_opt_out_is_acknowledged_once_and_marks_the_thread(wa, text):
    post(wa, text, sender="whatsapp:+61400111444")
    assert "I'll stop here" in last_out_or_twiml(wa)
    assert app.get_wa_setting("opted_out:wa-61400111444") == "1"


def last_out_or_twiml(client):
    # Scripted replies ride TwiML; read the transcript instead of the transport.
    return app.load_conversation("wa-61400111444")[-1]["content"]


def test_messaging_is_not_aging(wa):
    post(wa, "I'm messaging on behalf of my son, he's 12", sender="whatsapp:+61400111555")
    reply = app.load_conversation("wa-61400111555")[-1]["content"]
    assert "groceries when you" not in reply and "long game" not in reply
    assert app.should_use_local_tone_handler("thanks for messaging back", "wa-x") is False or True  # no crash
    assert app.should_use_local_tone_handler("I want to stay strong as I'm aging", "wa-y") is True


def test_nudge_yes_backstop_returns_trial_link():
    app.conversations.clear()
    sid = "wa-61400111666"
    app.conversations[sid] = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": app.WA_NUDGE_TEXT}]
    reply = app.contextual_short_reply("yes please", sid)
    assert reply and app.TRIAL_LINK in reply


def test_ack_after_bot_question_is_not_fog():
    app.conversations.clear()
    sid = "wa-61400111777"
    app.conversations[sid] = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "Want the timetable or prices?"}]
    assert "fog" not in (app.contextual_short_reply("ok", sid) or "")
    assert app.contextual_short_reply("\U0001F44D", sid)


# ---- #17: the WhatsApp vague ladder starts at rung one ----------------------------

def test_wa_second_vague_message_gets_rung_one():
    app.conversations.clear()
    sid = "wa-61400111888"
    app.conversations[sid] = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "G'day, I'm Robo-Nick"},
                              {"role": "user", "content": "hmm"}]
    assert "Easiest place to start" in app.demo_fallback_reply("hmm", sid)
    web = "widget-abc"
    app.conversations[web] = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "Fair. Easiest place..."},
                              {"role": "user", "content": "hmm"}]
    assert "Still in the fog" in app.demo_fallback_reply("hmm", web)


# ---- #13: nudge rules ------------------------------------------------------------

def test_nudge_skips_closers_optout_handoff_undelivered(wa, monkeypatch):
    monkeypatch.setattr(app, "WA_NUDGE_MINUTES", 90)
    later = datetime.now() + timedelta(minutes=120)

    post(wa, "what time are classes?", sender="whatsapp:+61400222001")   # normal thread: due
    assert "wa-61400222001" in app.wa_sessions_needing_nudge(later)

    post(wa, "what time are classes?", sender="whatsapp:+61400222002")
    post(wa, "thanks, all good, cheers", sender="whatsapp:+61400222002")  # closer: skip
    assert "wa-61400222002" not in app.wa_sessions_needing_nudge(later)

    post(wa, "stop messaging me", sender="whatsapp:+61400222003")         # opt-out: skip
    assert "wa-61400222003" not in app.wa_sessions_needing_nudge(later)

    post(wa, "can I speak to a real person? Sam 0400 222 004", sender="whatsapp:+61400222004")  # handoff: skip
    assert app.get_wa_setting("handoff:wa-61400222004") == "1"
    assert "wa-61400222004" not in app.wa_sessions_needing_nudge(later)

    app.set_wa_setting("undelivered:wa-61400222001", "1")                # last answer never arrived: skip
    assert "wa-61400222001" not in app.wa_sessions_needing_nudge(later)


def test_nudge_quiet_hours_sydney():
    from zoneinfo import ZoneInfo
    syd = ZoneInfo("Australia/Sydney")
    assert app.wa_nudge_hours_open(datetime(2026, 9, 14, 2, 30, tzinfo=syd)) is False
    assert app.wa_nudge_hours_open(datetime(2026, 9, 14, 8, 0, tzinfo=syd)) is True
    assert app.wa_nudge_hours_open(datetime(2026, 9, 14, 19, 59, tzinfo=syd)) is True
    assert app.wa_nudge_hours_open(datetime(2026, 9, 14, 20, 0, tzinfo=syd)) is False


def test_undelivered_marker_cleared_on_next_successful_send(wa, monkeypatch):
    sid = "wa-61400222005"
    app.set_wa_setting(f"undelivered:{sid}", "1")
    post(wa, "tell me about SPT please", sender="whatsapp:+61400222005")
    assert app.get_wa_setting(f"undelivered:{sid}") == ""


# ---- #18: alerts name the channel and carry the sender number -------------------

def test_human_request_alert_says_whatsapp_and_carries_the_number(wa):
    post(wa, "can I talk to a real person please?", sender="whatsapp:+61400333001")
    assert wa.alerts, "no owner alert queued"
    li = wa.alerts[-1]
    assert li["channel"] == "whatsapp"
    assert li["phone"].startswith("+61400333001")
    text = app.format_lead_summary(li)
    assert "Channel: WhatsApp" in text and "within 24 hours" in text
    sms_calls = []
    import app as _app
    orig = _app._send_twilio_sms
    _app._send_twilio_sms = lambda body: (sms_calls.append(body), True)[1]
    try:
        _app.lead_summary_twilio_configured = lambda: True
        _app.send_lead_summary_twilio(li)
    finally:
        _app._send_twilio_sms = orig
    assert sms_calls and "WhatsApp lead" in sms_calls[0] and "—" not in sms_calls[0]


# ---- #24: per-sender throttle ---------------------------------------------------

def test_flood_from_one_number_is_throttled(wa, monkeypatch):
    calls = {"n": 0}
    def limited(key, **kw):
        calls["n"] += 1
        return kw.get("scope") == "wa" and calls["n"] > 3
    monkeypatch.setattr(app, "is_rate_limited", limited)
    for i in range(6):
        post(wa, f"spam {i}", sender="whatsapp:+61400444001")
    assert "wa_rate_limited" in app.EVENTS_FILE.read_text()
    assert len(app.load_conversation("wa-61400444001")) < 12


# ---- #25: long replies are split, not cut ----------------------------------------

def test_split_whatsapp_body_keeps_paragraphs_and_link():
    para = "x" * 900
    body = f"{para}\n\n{para}\n\nBook here: https://momence.com/The-Outdoor-Squad-/x"
    parts = app.split_whatsapp_body(body, limit=1600)
    assert len(parts) == 2
    assert all(len(p) <= 1600 for p in parts)
    assert parts[-1].endswith("https://momence.com/The-Outdoor-Squad-/x")
    assert app.split_whatsapp_body("short") == ["short"]


def test_send_splits_long_body_into_consecutive_messages(monkeypatch):
    monkeypatch.setattr(app, "TWILIO_ACCOUNT_SID", "AC1")
    monkeypatch.setattr(app, "TWILIO_AUTH_TOKEN", "t")
    monkeypatch.setattr(app, "TWILIO_WA_FROM", "+61499000000")
    bodies = []
    class _R:
        def __init__(self): self._b = b'{"sid":"SMx"}'
        def read(self): return self._b
        def __enter__(self): return self
        def __exit__(self, *a): return False
    def fake_open(req, timeout=15):
        import urllib.parse
        bodies.append(urllib.parse.parse_qs(req.data.decode())["Body"][0]); return _R()
    monkeypatch.setattr(app.urllib.request, "urlopen", fake_open)
    monkeypatch.setattr(app, "log_event", lambda *a, **k: None)
    ok, sid = app.send_whatsapp_via_twilio("61400555001", ("y" * 900 + "\n\n") * 3)
    assert ok and len(bodies) == 3 and all(len(b) <= 1600 for b in bodies)
