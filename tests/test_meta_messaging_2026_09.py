"""Facebook Messenger + Instagram DM channel (Stage 2, 27 Sep 2026).

The contract under test:
- /meta-webhook verifies like every Meta webhook (hub.verify_token) and
  authenticates every POST with X-Hub-Signature-256, fail-closed without the
  app secret.
- Inbound text rides the SAME brain, lead capture, mute and per-thread queue
  as WhatsApp, under channel-scoped session ids (fb-<psid>, ig-<igsid>) that
  the public endpoints can never open.
- Each channel's switch defaults OFF, and switched off NOTHING is sent: no
  reply, no owner alert, no CRM push. Deploying activates nothing.
- An echo the bot did not send means Nick replied natively in the inbox, and
  mutes the bot in that thread; the bot's own echoes never do.
- Non-text never reaches the model.
"""
import hashlib
import hmac
import importlib
import json
import os
import sys
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _k in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_KEY",
           "OUTDOOR_SQUAD_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY",
           "OUTDOOR_SQUAD_OPENAI_API_KEY", "OPENAI_API_KEY",
           "OUTDOOR_SQUAD_GEMINI_API_KEY", "GEMINI_API_KEY",
           "META_APP_SECRET", "META_VERIFY_TOKEN", "META_PAGE_ACCESS_TOKEN",
           "META_APP_ID", "META_PAGE_ID", "META_IG_ACCOUNT_ID"):
    os.environ.pop(_k, None)

import app  # noqa: E402

importlib.reload(app)
app.SUPABASE_URL = ""
app.SUPABASE_KEY = None

SECRET = "test-meta-app-secret"
VERIFY = "test-verify-token"
APP_ID = "2139132166979879"
PAGE_ID = "111222333"
IG_ID = "17840000000000000"
PAGE_INBOX_APP_ID = "263902037430900"
PSID = "24000000000000001"
IGSID = "35000000000000002"


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
    monkeypatch.setattr(app, "META_APP_SECRET", SECRET)
    monkeypatch.setattr(app, "META_VERIFY_TOKEN", VERIFY)
    monkeypatch.setattr(app, "META_PAGE_ACCESS_TOKEN", "test-page-token")
    monkeypatch.setattr(app, "META_APP_ID", APP_ID)
    monkeypatch.setattr(app, "META_PAGE_ID", PAGE_ID)
    monkeypatch.setattr(app, "META_IG_ACCOUNT_ID", IG_ID)
    monkeypatch.setattr(app, "ADMIN_USERNAME", "u")
    monkeypatch.setattr(app, "ADMIN_PASSWORD", "p")
    # Deterministic brain, no network.
    ai_calls = []
    monkeypatch.setattr(app, "generate_ai_reply",
                        lambda m, s: (ai_calls.append((m, s)), ("**Classes** run daily. [Book](https://example.com/t)", "test"))[1])
    monkeypatch.setattr(app, "should_use_local_tone_handler", lambda m, s, **k: False)
    # Anything that would reach a real person is recorded, never performed.
    alerts = []
    monkeypatch.setattr(app, "notify_lead_summary_async", lambda *a, **k: alerts.append(("lead", a)))
    monkeypatch.setattr(app, "notify_new_conversation_async", lambda *a, **k: alerts.append(("conv", a)))
    monkeypatch.setattr(app, "maybe_push_lead_to_momence", lambda *a, **k: alerts.append(("momence", a, k)))
    real_human = app.notify_human_request_if_needed
    monkeypatch.setattr(app, "notify_human_request_if_needed",
                        lambda *a, **k: (alerts.append(("human", a)), real_human(*a, **{**k, "internal_qa": True}))[1])
    monkeypatch.setattr(app, "_wa_async_dispatch", lambda target, args: target(*args))
    sent = []
    counter = {"n": 0}

    def _fake_send(channel, recipient, text):
        counter["n"] += 1
        sent.append((channel, recipient, text))
        return True, f"m_test_{counter['n']}"

    monkeypatch.setattr(app, "send_meta_message", _fake_send)
    app.conversations.clear()
    app._meta_seen_mids.clear()
    app._wa_inflight.clear()
    app._wa_pending.clear()
    app._rate_buckets.clear()
    app._wa_setting_cache.clear()
    c = TestClient(app.app)
    c.sent, c.alerts, c.ai_calls = sent, alerts, ai_calls
    yield c
    app._wa_setting_cache.clear()


def _sig(raw: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def _post(client, payload, signature=None):
    raw = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json",
               "X-Hub-Signature-256": _sig(raw) if signature is None else signature}
    return client.post("/meta-webhook", content=raw, headers=headers)


def _fb_text(text, mid="m_in_1", psid=PSID, **extra):
    msg = {"mid": mid, "text": text, **extra}
    return {"object": "page", "entry": [{"id": PAGE_ID, "time": 1, "messaging": [
        {"sender": {"id": psid}, "recipient": {"id": PAGE_ID}, "timestamp": 1, "message": msg}]}]}


def _ig_event(event):
    return {"object": "instagram", "entry": [{"id": IG_ID, "time": 1, "messaging": [
        {"sender": {"id": IGSID}, "recipient": {"id": IG_ID}, "timestamp": 1, **event}]}]}


def _echo(channel, text, mid, app_id=None):
    msg = {"mid": mid, "text": text, "is_echo": True}
    if app_id:
        msg["app_id"] = int(app_id)
    customer = IGSID if channel == "instagram" else PSID
    account = IG_ID if channel == "instagram" else PAGE_ID
    return {"object": "instagram" if channel == "instagram" else "page", "entry": [
        {"id": account, "time": 1, "messaging": [
            {"sender": {"id": account}, "recipient": {"id": customer}, "timestamp": 1, "message": msg}]}]}


def _switch(channel, on=True):
    app.set_wa_setting(app.META_CHANNEL_SWITCH_KEYS[channel], "1" if on else "0")


def _events():
    return [json.loads(line) for line in app.EVENTS_FILE.read_text().splitlines() if line.strip()]


def _event_types():
    return [e.get("event_type") for e in _events()]


# ── Verification handshake ────────────────────────────────────────────────


def test_verify_echoes_challenge_only_for_the_right_token(client):
    ok = client.get("/meta-webhook", params={"hub.mode": "subscribe", "hub.verify_token": VERIFY,
                                             "hub.challenge": "1158201444"})
    assert ok.status_code == 200 and ok.text == "1158201444"
    bad = client.get("/meta-webhook", params={"hub.mode": "subscribe", "hub.verify_token": "nope",
                                              "hub.challenge": "1"})
    assert bad.status_code == 403
    wrong_mode = client.get("/meta-webhook", params={"hub.mode": "unsubscribe", "hub.verify_token": VERIFY,
                                                     "hub.challenge": "1"})
    assert wrong_mode.status_code == 403
    non_ascii = client.get("/meta-webhook", params={"hub.mode": "subscribe", "hub.verify_token": "t\u00f6ken",
                                                    "hub.challenge": "1"})
    assert non_ascii.status_code == 403


def test_verify_refuses_everything_without_a_configured_token(client, monkeypatch):
    monkeypatch.setattr(app, "META_VERIFY_TOKEN", "")
    r = client.get("/meta-webhook", params={"hub.mode": "subscribe", "hub.verify_token": "",
                                            "hub.challenge": "1"})
    assert r.status_code == 403


# ── Signature ─────────────────────────────────────────────────────────────


def test_missing_or_bad_signature_is_rejected_and_nothing_is_stored(client):
    raw = json.dumps(_fb_text("hi")).encode()
    r = client.post("/meta-webhook", content=raw, headers={"Content-Type": "application/json"})
    assert r.status_code == 403
    r = _post(client, _fb_text("hi"), signature="sha256=" + "0" * 64)
    assert r.status_code == 403
    r = _post(client, _fb_text("hi"), signature=_sig(raw, secret="some-other-secret"))
    assert r.status_code == 403
    assert app.load_conversation("fb-" + PSID) == []
    assert "meta_bad_signature" in _event_types()


def test_fail_closed_without_app_secret(client, monkeypatch):
    monkeypatch.setattr(app, "META_APP_SECRET", "")
    raw = json.dumps(_fb_text("hi")).encode()
    # Even a payload "signed" with an empty key is refused.
    r = client.post("/meta-webhook", content=raw,
                    headers={"X-Hub-Signature-256": _sig(raw, secret=""), "Content-Type": "application/json"})
    assert r.status_code == 503
    assert app.load_conversation("fb-" + PSID) == []
    assert not app.meta_signature_valid(raw, _sig(raw, secret=""))


def test_valid_signature_is_acknowledged_and_other_objects_ignored(client):
    r = _post(client, {"object": "whatsapp_business_account", "entry": []})
    assert r.status_code == 200 and r.json()["status"] == "ignored"
    raw = b"not json"
    r = client.post("/meta-webhook", content=raw, headers={"X-Hub-Signature-256": _sig(raw)})
    assert r.status_code == 400


# ── Launch gate: channel OFF by default, nothing leaves ───────────────────


def test_channel_off_by_default_stores_and_sends_nothing(client):
    assert not app.meta_channel_enabled("messenger")
    assert not app.meta_channel_enabled("instagram")
    r = _post(client, _fb_text("Can I speak to Nick? my email is jo@example.com"))
    assert r.status_code == 200
    thread = app.load_conversation("fb-" + PSID)
    assert thread and thread[-1]["role"] == "user" and "speak to Nick" in thread[-1]["content"]
    assert client.sent == [], "the channel is off: nothing may be sent"
    assert client.ai_calls == [], "no reply is even written while off"
    assert client.alerts == [], "no owner alert, no verbose alert, no CRM push while off"
    types = _event_types()
    assert "meta_channel_off_skip" in types and "message_received" in types


def test_channel_off_media_and_echo_send_nothing(client):
    _post(client, _ig_event({"message": {"mid": "m_img", "attachments": [{"type": "image", "payload": {"url": "x"}}]}}))
    assert client.sent == []


def test_switch_off_mid_generation_suppresses_the_answer(client, monkeypatch):
    _switch("messenger", True)

    def _slow_ai(m, s):
        _switch("messenger", False)  # Nick flips it while the answer is being written
        return ("Late answer", "test")

    monkeypatch.setattr(app, "generate_ai_reply", _slow_ai)
    _post(client, _fb_text("when are classes?"))
    assert client.sent == []
    assert any(e.get("event_type") == "meta_reply_suppressed" and e.get("reason") == "channel_off"
               for e in _events())


# ── Channel ON: the same brain answers ────────────────────────────────────


def test_messenger_text_is_answered_plain_text_with_intro(client):
    _switch("messenger", True)
    r = _post(client, _fb_text("when are classes?"))
    assert r.status_code == 200
    assert len(client.sent) == 1
    channel, recipient, text = client.sent[0]
    assert channel == "messenger" and recipient == PSID
    assert "**" not in text and "[Book]" not in text and "Book: https://example.com/t" in text
    assert client.ai_calls and client.ai_calls[0][1] == "fb-" + PSID
    thread = app.load_conversation("fb-" + PSID)
    assert thread[-1]["role"] == "assistant" and thread[-1]["content"] == text
    assert "meta_reply_sent" in _event_types()


def test_instagram_text_is_answered_on_the_ig_session(client):
    _switch("instagram", True)
    _post(client, _ig_event({"message": {"mid": "m_ig_1", "text": "how much is it?"}}))
    assert client.sent and client.sent[0][:2] == ("instagram", IGSID)
    assert app.load_conversation("ig-" + IGSID)[-1]["role"] == "assistant"
    # Switches are independent.
    assert not app.meta_channel_enabled("messenger")


def test_scripted_first_answer_gets_the_introduction(client, monkeypatch):
    _switch("messenger", True)
    monkeypatch.setattr(app, "should_use_local_tone_handler", lambda m, s, **k: True)
    monkeypatch.setattr(app, "demo_fallback_reply", lambda m, session_id=None: "Classes run daily.")
    _post(client, _fb_text("parking?", mid="m_s1"))
    assert client.sent[0][2].startswith(app.WA_DETERMINISTIC_INTRO)
    _post(client, _fb_text("and on weekends?", mid="m_s2"))
    assert not client.sent[1][2].startswith(app.WA_DETERMINISTIC_INTRO)


def test_meta_prompt_is_used_for_meta_sessions(client):
    messages = app.build_agent_messages("hi", "ig-" + IGSID)
    system = " ".join(m["content"] for m in messages if m["role"] == "system")
    assert "Instagram DM" in system and "Plain text only" in system
    assert "Channel: you are replying on WhatsApp" not in system


def test_duplicate_delivery_is_processed_once(client):
    _switch("messenger", True)
    _post(client, _fb_text("hello there", mid="m_dup"))
    _post(client, _fb_text("hello there", mid="m_dup"))
    users = [m for m in app.load_conversation("fb-" + PSID) if m["role"] == "user"]
    assert len(users) == 1
    assert len(client.sent) == 1


def test_wrong_page_is_ignored(client):
    _switch("messenger", True)
    payload = _fb_text("hi")
    payload["entry"][0]["id"] = "999999"
    _post(client, payload)
    assert app.load_conversation("fb-" + PSID) == [] and client.sent == []
    assert "meta_wrong_account" in _event_types()


def test_messages_while_answering_are_queued_and_coalesced(client, monkeypatch):
    _switch("messenger", True)
    sid = "fb-" + PSID
    # A worker already owns this thread.
    token = app._wa_claim_inflight(sid)
    _post(client, _fb_text("first", mid="m_q1"))
    _post(client, _fb_text("second", mid="m_q2"))
    assert client.sent == [] and len(app._wa_pending.get(sid, [])) == 2
    # The owning worker finishes and drains both as one turn.
    app._wa_generate_and_send("earlier", sid, "", False, "m_q0", token, answer_one=app._meta_answer_one)
    assert len(client.sent) == 2
    assert client.ai_calls[-1][0] == "first\nsecond"


# ── Echoes: native replies mute, our own never do ─────────────────────────


def test_bot_own_messenger_echo_never_mutes_or_replies(client):
    _switch("messenger", True)
    _post(client, _fb_text("when are classes?"))
    bot_text = client.sent[0][2]
    _post(client, _echo("messenger", bot_text, "m_test_1", app_id=APP_ID))
    assert not app.wa_muted("fb-" + PSID)
    assert len(client.sent) == 1, "an echo is never answered"
    assert "meta_native_reply_detected" not in _event_types()


def test_instagram_echo_without_app_id_is_matched_by_our_ledger(client):
    _switch("instagram", True)
    _post(client, _ig_event({"message": {"mid": "m_ig_2", "text": "prices?"}}))
    bot_text = client.sent[0][2]
    # Matched by message id...
    _post(client, _echo("instagram", bot_text, "m_test_1"))
    assert not app.wa_muted("ig-" + IGSID)
    # ...and by text when the echo beats the Send API response (unknown mid).
    _post(client, _echo("instagram", bot_text, "m_not_yet_recorded"))
    assert not app.wa_muted("ig-" + IGSID)


def test_native_page_inbox_reply_mutes_and_hand_back_restores(client):
    _switch("messenger", True)
    sid = "fb-" + PSID
    _post(client, _fb_text("hi, question about kids classes", mid="m_n1"))
    assert len(client.sent) == 1
    # Nick answers from the Facebook Page inbox (Meta's Page inbox app id).
    _post(client, _echo("messenger", "Hey, Nick here, happy to help", "m_nick_1", app_id=PAGE_INBOX_APP_ID))
    assert app.wa_muted(sid)
    assert any(m.get("by") == "owner" and "Nick here" in m["content"] for m in app.load_conversation(sid))
    assert "meta_native_reply_detected" in _event_types()
    # The customer writes again: stored, not answered.
    _post(client, _fb_text("thanks Nick", mid="m_n2"))
    assert len(client.sent) == 1
    assert "meta_bot_muted_skip" in _event_types()
    # Hand back through the same endpoint as WhatsApp.
    r = client.post("/api/wa/mute", json={"session_id": sid, "clear": True}, auth=("u", "p"))
    assert r.status_code == 200 and r.json()["muted"] is False
    _post(client, _fb_text("one more question", mid="m_n3"))
    assert len(client.sent) == 2


def test_native_instagram_reply_mutes(client):
    _switch("instagram", True)
    _post(client, _ig_event({"message": {"mid": "m_ig_3", "text": "hi"}}))
    _post(client, _echo("instagram", "Nick here, typed in the IG app", "m_nick_ig"))
    assert app.wa_muted("ig-" + IGSID)


def test_native_reply_while_answer_in_flight_suppresses_it(client, monkeypatch):
    _switch("messenger", True)

    def _slow_ai(m, s):
        app._meta_handle_echo("messenger", s, {"mid": "m_nick_x", "text": "I've got this one",
                                              "is_echo": True, "app_id": int(PAGE_INBOX_APP_ID)})
        return ("Bot answer", "test")

    monkeypatch.setattr(app, "generate_ai_reply", _slow_ai)
    _post(client, _fb_text("question", mid="m_race"))
    assert client.sent == []
    assert any(e.get("event_type") == "meta_reply_suppressed" and e.get("reason") == "muted"
               for e in _events())


# ── Non-text is safe ──────────────────────────────────────────────────────


def test_photo_gets_one_text_only_line_and_never_reaches_the_model(client):
    _switch("instagram", True)
    photo = {"message": {"mid": "m_p1", "attachments": [{"type": "image", "payload": {"url": "https://x"}}]}}
    _post(client, _ig_event(photo))
    assert [s[2] for s in client.sent] == [app.META_MEDIA_ACK_TEXT]
    photo["message"]["mid"] = "m_p2"
    _post(client, _ig_event(photo))
    assert len(client.sent) == 1, "one text-only line per 12h, not one per photo"
    assert client.ai_calls == []


@pytest.mark.parametrize("event", [
    {"message": {"mid": "m_st", "attachments": [{"type": "image", "payload": {"sticker_id": 369239263222822}}]}},
    {"message": {"mid": "m_sm", "attachments": [{"type": "story_mention", "payload": {"url": "https://x"}}]}},
    {"message": {"mid": "m_del", "is_deleted": True}},
    {"message": {"mid": "m_uns", "is_unsupported": True}},
    {"message": {"mid": "m_empty"}},
    {"reaction": {"mid": "m_x", "action": "react", "reaction": "love"}},
    {"read": {"mid": "m_x"}},
    {"delivery": {"mids": ["m_x"]}},
    {"referral": {"ref": "ad", "source": "ADS"}},
    {"something_new": {"a": 1}},
])
def test_gestures_and_receipts_send_nothing_and_never_crash(client, event):
    _switch("instagram", True)
    r = _post(client, _ig_event(event))
    assert r.status_code == 200
    assert client.sent == [] and client.ai_calls == []
    assert "meta_event_error" not in _event_types()


def test_story_reply_is_stored_for_nick_not_answered(client):
    _switch("instagram", True)
    _post(client, _ig_event({"message": {"mid": "m_sr", "text": "is this at Camperdown?",
                                         "reply_to": {"story": {"url": "https://cdn", "id": "1"}}}}))
    assert client.sent == [] and client.ai_calls == []
    assert app.load_conversation("ig-" + IGSID)[-1]["content"] == "is this at Camperdown?"
    assert "meta_story_reply_left_for_owner" in _event_types()


def test_postback_title_is_answered_as_text(client):
    _switch("messenger", True)
    payload = {"object": "page", "entry": [{"id": PAGE_ID, "time": 1, "messaging": [
        {"sender": {"id": PSID}, "recipient": {"id": PAGE_ID}, "timestamp": 1,
         "postback": {"mid": "m_pb", "title": "What are your prices?", "payload": "PRICES"}}]}]}
    _post(client, payload)
    assert client.ai_calls and client.ai_calls[0][0] == "What are your prices?"
    assert len(client.sent) == 1


def test_malformed_event_is_isolated(client):
    _switch("messenger", True)
    payload = _fb_text("second event still answered", mid="m_ok")
    payload["entry"][0]["messaging"].insert(0, {"sender": "not-a-dict", "message": {"mid": "m_bad", "text": 5}})
    r = _post(client, payload)
    assert r.status_code == 200
    assert len(client.sent) == 1


# ── Opt-out ───────────────────────────────────────────────────────────────


def test_stop_is_acknowledged_once_then_silence(client, monkeypatch):
    _switch("messenger", True)
    monkeypatch.setattr(app, "should_use_local_tone_handler",
                        lambda m, s, **k: app.is_opt_out_message(app.normalise_chat_text(m)))
    _post(client, _fb_text("stop messaging me", mid="m_o1"))
    assert len(client.sent) == 1 and "stop here" in client.sent[0][2]
    assert app.get_wa_setting("opted_out:fb-" + PSID) == "1"
    _post(client, _fb_text("how much is membership?", mid="m_o2"))
    assert len(client.sent) == 1


# ── 24-hour window ────────────────────────────────────────────────────────


def test_answer_is_not_sent_outside_the_24h_window(client):
    _switch("messenger", True)
    sid = "fb-" + PSID
    stale = (datetime.now() - timedelta(hours=25)).isoformat()
    app.conversations[sid] = [{"role": "user", "content": "old question", "at": stale}]
    app._meta_answer_one("old question", sid, "", False, "m_old")
    assert client.sent == []
    assert any(e.get("event_type") == "meta_reply_suppressed" and e.get("reason") == "window_closed"
               for e in _events())
    assert app.meta_window_state(sid)["window_open"] is False


def test_manual_reply_respects_switch_window_and_mutes(client):
    sid = "fb-" + PSID
    body = {"session_id": sid, "message": "Hi, Nick here"}
    r = client.post("/api/wa/reply", json=body, auth=("u", "p"))
    assert r.status_code == 409 and r.json()["error"] == "channel_off"
    _switch("messenger", True)
    app.conversations[sid] = [{"role": "user", "content": "q",
                               "at": (datetime.now() - timedelta(hours=30)).isoformat()}]
    r = client.post("/api/wa/reply", json=body, auth=("u", "p"))
    assert r.status_code == 422 and r.json()["error"] == "outside_24h_window"
    assert client.sent == []
    app.conversations[sid] = [{"role": "user", "content": "q", "at": datetime.now().isoformat()}]
    r = client.post("/api/wa/reply", json=body, auth=("u", "p"))
    assert r.status_code == 200 and r.json()["muted"] is True
    assert client.sent == [("messenger", PSID, "Hi, Nick here")]
    assert app.wa_muted(sid)
    # Its echo comes back from our own app: recognised, not recorded twice.
    before = len(app.load_conversation(sid))
    _post(client, _echo("messenger", "Hi, Nick here", "m_test_1", app_id=APP_ID))
    assert len(app.load_conversation(sid)) == before


# ── Session isolation from the public endpoints ──────────────────────────


@pytest.mark.parametrize("sid", ["fb-" + PSID, "ig-" + IGSID, "FB-" + PSID, "Ig-" + IGSID])
def test_public_chat_cannot_open_a_meta_thread(client, sid):
    real = "fb-" + PSID if sid.lower().startswith("fb-") else "ig-" + IGSID
    app.conversations[real] = [{"role": "user", "content": "my private injury details"}]
    r = client.post("/api/chat", json={"message": "what did I tell you earlier?", "session_id": sid})
    assert r.status_code == 200
    returned = r.json()["session_id"]
    assert returned.startswith("s-")
    assert not any("what did I tell you" in m["content"] for m in app.load_conversation(real))


@pytest.mark.parametrize("sid", ["fb-" + PSID, "ig-" + IGSID])
def test_public_event_cannot_touch_a_meta_session(client, sid):
    before = app.EVENTS_FILE.read_text()
    r = client.post("/api/event", json={"event_type": "trial_link_clicked", "session_id": sid,
                                        "metadata": {"url": app.TRIAL_LINK}})
    assert r.status_code == 200
    new_lines = app.EVENTS_FILE.read_text()[len(before):]
    assert sid not in new_lines and "session_id_rejected_reserved" in new_lines


def test_admin_meta_endpoints_require_auth(client):
    assert client.get("/api/meta/conversations").status_code == 401
    r = client.get("/api/meta/conversations", auth=("u", "p"))
    assert r.status_code == 200
    assert r.json()["channels_enabled"] == {"messenger": False, "instagram": False}


# ── Switches, admin, health ───────────────────────────────────────────────


def test_kill_endpoint_flips_one_meta_channel_and_leaves_whatsapp_alone(client):
    wa_before = app.wa_channel_enabled()
    r = client.post("/api/wa/kill", json={"channel": "instagram", "enabled": True}, auth=("u", "p"))
    assert r.status_code == 200 and r.json() == {"ok": True, "channel": "instagram", "enabled": True}
    assert app.meta_channel_enabled("instagram") and not app.meta_channel_enabled("messenger")
    assert app.wa_channel_enabled() == wa_before
    r = client.post("/api/wa/kill", json={"channel": "tiktok", "enabled": True}, auth=("u", "p"))
    assert r.status_code == 400
    r = client.post("/api/wa/kill", json={"channel": "instagram", "enabled": False}, auth=("u", "p"))
    assert r.json()["enabled"] is False and not app.meta_channel_enabled("instagram")


def test_mute_endpoint_accepts_meta_sessions_and_still_rejects_widget(client):
    r = client.post("/api/wa/mute", json={"session_id": "ig-" + IGSID, "minutes": 10}, auth=("u", "p"))
    assert r.status_code == 200 and app.wa_muted("ig-" + IGSID)
    r = client.post("/api/wa/mute", json={"session_id": "widget-abc"}, auth=("u", "p"))
    assert r.status_code == 400


def test_health_reports_meta_state_as_booleans_only(client):
    body = client.get("/api/health").json()
    assert body["meta_webhook_configured"] is True
    assert body["meta_send_configured"] is True
    assert body["meta_channels_enabled"] == {"messenger": False, "instagram": False}
    assert SECRET not in json.dumps(body) and VERIFY not in json.dumps(body)


def test_data_deletion_page_is_public_and_factual(client):
    r = client.get("/data-deletion")
    assert r.status_code == 200
    assert app.HUMAN_EMAIL in r.text and "privacy-policy" in r.text
    assert "\u2014" not in r.text


# ── Transport details ─────────────────────────────────────────────────────


def test_long_answer_is_split_under_the_instagram_byte_limit():
    body = "\n\n".join(["Paragraph " + "\U0001f3cb" * 60 + " end."] * 8)
    parts = app.meta_split_body(body)
    assert len(parts) > 1
    assert all(len(p.encode("utf-8")) <= app.META_TEXT_LIMIT_BYTES for p in parts)
    one_line = "x" * 2500
    assert all(len(p.encode()) <= 1000 for p in app.meta_split_body(one_line))
    # WhatsApp's character splitting is unchanged.
    assert app.split_whatsapp_body("a" * 3200, limit=1600) == ["a" * 1600, "a" * 1600]


def test_render_for_meta_strips_markdown_but_keeps_links():
    out = app.render_for_meta("## Prices\n**Group** is $59.\n\n\n\n[Trial](https://t.example)")
    assert out == "Prices\nGroup is $59.\n\nTrial: https://t.example"


def test_send_meta_message_never_leaks_the_token(monkeypatch):
    monkeypatch.setattr(app, "META_PAGE_ACCESS_TOKEN", "EAAsecret-token-value")

    def _boom(request, timeout=0):
        raise OSError("connection failed for " + request.full_url)

    monkeypatch.setattr(app.urllib.request, "urlopen", _boom)
    ok, detail = app.send_meta_message("messenger", PSID, "hi")
    assert not ok and "EAAsecret-token-value" not in detail


def test_send_meta_message_body_shape(monkeypatch):
    monkeypatch.setattr(app, "META_PAGE_ACCESS_TOKEN", "tok")
    captured = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"recipient_id": "1", "message_id": "m_abc"}'

    def _fake(request, timeout=0):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode())
        return _Resp()

    monkeypatch.setattr(app.urllib.request, "urlopen", _fake)
    assert app.send_meta_message("messenger", PSID, "hi") == (True, "m_abc")
    assert "/me/messages" in captured["url"] and app.META_GRAPH_VERSION in captured["url"]
    assert captured["body"] == {"recipient": {"id": PSID}, "message": {"text": "hi"}, "messaging_type": "RESPONSE"}
    app.send_meta_message("instagram", IGSID, "hi")
    assert "messaging_type" not in captured["body"]


def test_meta_lead_goes_to_momence_with_its_own_channel(client):
    calls = []
    app_push = app.maybe_push_lead_to_momence
    try:
        app.maybe_push_lead_to_momence = lambda lead, sid, **k: calls.append((sid, k.get("source")))
        app._wa_capture_lead("I'm Jo, jo@example.com", "ig-" + IGSID, False, "test")
    finally:
        app.maybe_push_lead_to_momence = app_push
    assert calls == [("ig-" + IGSID, "instagram")]
    lead = next(item for item in app.read_leads() if item.get("session_id") == "ig-" + IGSID)
    assert lead["channel"] == "instagram"
