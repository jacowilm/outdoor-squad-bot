"""Episode boundaries for WhatsApp threads (11 Sep 2026 diff, findings #2 and #9).

A WhatsApp thread is one phone number for life, so every "once per session"
rule quietly meant "once per customer, ever": the person who asked for Nick in
July never reached him again, the follow-up nudge was spent for good, and
someone coming back months later was answered as if they were message six.
An inbound after WA_EPISODE_GAP_HOURS of silence now opens a new episode.
"""
import base64
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
    monkeypatch.setattr(app, "generate_ai_reply",
                        lambda m, s: ("G'day, Robo-Nick here. What can I sort out?", "test"))
    monkeypatch.setattr(app, "_wa_async_dispatch", lambda target, args: target(*args))
    alerts = []
    monkeypatch.setattr(app, "notify_lead_summary_async", lambda li, **k: alerts.append(dict(li)))
    monkeypatch.setattr(app, "maybe_push_lead_to_momence", lambda *a, **k: None)
    monkeypatch.setattr(app, "is_rate_limited", lambda *a, **k: False)
    sent = []
    monkeypatch.setattr(app, "send_whatsapp_via_twilio",
                        lambda to, body: (sent.append((to, body)), (True, "SMfake"))[1])
    app.conversations.clear()
    app._twilio_wa_seen_sids.clear()
    app._wa_setting_cache.clear()
    client = TestClient(app.app)
    client.sent = sent
    client.alerts = alerts
    return client


def _sign(params):
    base = app.TWILIO_WA_FALLBACK_HOSTS[0]
    payload = base + WEBHOOK_PATH + "".join(k + str(params[k]) for k in sorted(params))
    return base64.b64encode(hmac.new(TEST_TOKEN.encode(), payload.encode(), hashlib.sha1).digest()).decode()


_n = [0]
SENDER = "whatsapp:+61400222111"
SID = "wa-61400222111"


def post(client, body, sender=SENDER, message_sid=None):
    _n[0] += 1
    params = {"From": sender, "To": "whatsapp:+61499000000", "Body": body,
              "MessageSid": message_sid or f"SMep{_n[0]:05d}", "NumMedia": "0"}
    return client.post(WEBHOOK_PATH, data=params, headers={"X-Twilio-Signature": _sign(params)})


def backdate(session_id=SID, hours=40):
    """Age the newest inbound so the next one opens a new episode."""
    stamp = (datetime.now() - timedelta(hours=hours)).isoformat(timespec="seconds")
    for turn in reversed(app.conversations.get(session_id, [])):
        if turn.get("role") == "user":
            turn["at"] = stamp
            return stamp
    raise AssertionError("no user turn to backdate")


def events():
    return [json.loads(line) for line in app.EVENTS_FILE.read_text().splitlines() if line.strip()]


def started(session_id=SID):
    return [e for e in events()
            if e.get("event_type") == "conversation_started" and e.get("session_id") == session_id]


# ---- 1: the human-request claim is per episode ---------------------------------

def test_human_request_alerts_once_per_episode_and_again_after_the_gap(wa):
    post(wa, "can I chat to a real human?")
    post(wa, "can I chat to a real human?")
    assert len(wa.alerts) == 1, wa.alerts
    assert app.get_wa_setting(f"handoff:{SID}") == "1"
    assert app.wa_episode_key(SID) == SID

    backdate()
    post(wa, "can I chat to a real human?")
    assert len(wa.alerts) == 2, wa.alerts
    assert app.wa_episode_key(SID) == f"{SID}#e2"
    assert app.get_wa_setting(f"handoff:{SID}#e2") == "1"
    # The episode-1 claim and marker are kept, not deleted: they are the audit
    # trail of what Nick was told in the first conversation.
    assert app.get_wa_setting(f"handoff:{SID}") == "1"
    assert f'"{SID}"' in app.HUMAN_REQUEST_CLAIMS_FILE.read_text()
    opener = started()
    assert len(opener) == 2
    assert opener[0]["episode"] == 1 and opener[1]["episode"] == 2
    assert opener[1]["channel"] == "whatsapp"


# ---- 2: the vague ladder restarts at rung one ----------------------------------

def test_returning_vague_opener_is_a_first_contact_again(wa):
    # The ladder answers inside the request as TwiML; only the AI tail is sent
    # over REST, which is why these assertions read two different surfaces.
    post(wa, "hi")
    assert "Fair. Easiest place" in post(wa, "hmm").text
    assert "Still in the fog" in post(wa, "hmm").text

    backdate()
    before = len(wa.sent)
    assert app.wa_first_contact_greeting("hi", SID) is False  # pre-boundary state
    assert "<Message>" not in post(wa, "hi").text
    assert len(wa.sent) == before + 1
    assert "Robo-Nick here" in wa.sent[-1][1], "a returning hi must reach the AI, not the ladder"
    assert app.wa_episode_number(SID) == 2
    assert app.demo_fallback_reply("hmm", session_id=SID).startswith("Fair. Easiest place")


# ---- 3: the repetition guard only looks inside the episode ---------------------

def test_repetition_guard_is_scoped_to_the_episode():
    sid = "wa-61400222333"
    draft = ("Camperdown runs 6am and 5:30pm most weekdays, Redfern runs 6am and 6pm, "
             "and Saturday mornings are at Camperdown. Sessions are 45 minutes and all "
             "levels are welcome, so pick whichever is closer to walk into.")
    app.conversations[sid] = [{"role": "assistant", "content": draft},
                              {"role": "user", "content": "what about the timetable"}]
    assert app.prevent_repetitive_reply(draft, "what about the timetable", sid) != draft

    app.conversations[sid] = [{"role": "assistant", "content": draft},
                              {"role": "user", "content": "what about the timetable",
                               "episode": 2, "at": app.now_iso()}]
    assert app.prevent_repetitive_reply(draft, "what about the timetable", sid) == draft
    app.conversations.pop(sid, None)


# ---- 4: an opt-out is permanent, episodes or not -------------------------------

def test_opt_out_survives_a_new_episode(wa):
    post(wa, "hi")
    app.set_wa_setting(f"opted_out:{SID}", "1")
    backdate()
    post(wa, "hi again")
    assert app.wa_episode_number(SID) == 2
    assert app.get_wa_setting(f"opted_out:{SID}") == "1"
    later = datetime.now() + timedelta(minutes=app.WA_NUDGE_MINUTES + 10)
    assert SID not in app.wa_sessions_needing_nudge(later)


# ---- 5: the nudge resets per episode but is capped for life --------------------

def test_nudge_is_due_again_in_a_new_episode_until_the_lifetime_cap(wa):
    post(wa, "hi")
    app.set_wa_setting(f"nudged:{SID}", "1")
    later = datetime.now() + timedelta(minutes=app.WA_NUDGE_MINUTES + 10)
    assert SID not in app.wa_sessions_needing_nudge(later)

    backdate()
    post(wa, "hello again")
    assert app.wa_episode_number(SID) == 2
    assert SID in app.wa_sessions_needing_nudge(later)

    app.set_wa_setting(f"nudge_count:{SID}", str(app.WA_NUDGE_MAX_PER_THREAD))
    assert SID not in app.wa_sessions_needing_nudge(later), \
        "three follow-ups is the whole life of a thread"


# ---- 6: the follow-up is claimed before it is sent -----------------------------

def _run_nudge_once(monkeypatch, session_id, send):
    monkeypatch.setattr(app, "wa_nudge_hours_open", lambda *a, **k: True)
    monkeypatch.setattr(app, "wa_sessions_needing_nudge", lambda *a, **k: [session_id])
    monkeypatch.setattr(app, "send_whatsapp_via_twilio", send)
    ticks = {"n": 0}

    def fake_sleep(_seconds):
        ticks["n"] += 1
        if ticks["n"] > 1:
            raise RuntimeError("stop the loop")

    monkeypatch.setattr(app.time, "sleep", fake_sleep)
    with pytest.raises(RuntimeError):
        app._wa_nudge_loop()


def test_nudge_claim_is_written_before_the_send_and_only_rolled_back_on_a_missing_credential(
        wa, monkeypatch, tmp_path):
    sid = "wa-61400222444"
    app.conversations[sid] = [{"role": "user", "content": "hi", "at": app.now_iso()}]
    _run_nudge_once(monkeypatch, sid, lambda to, body: (False, "HTTP 500: upstream"))
    assert app.get_wa_setting(f"nudged:{sid}") == "1", "a real failure still spends the attempt"
    assert app.get_wa_setting(f"nudge_count:{sid}") == "1"

    other = "wa-61400222555"
    app.conversations[other] = [{"role": "user", "content": "hi", "at": app.now_iso()}]
    _run_nudge_once(monkeypatch, other, lambda to, body: (False, "twilio sending not configured"))
    assert app.get_wa_setting(f"nudged:{other}") == ""
    assert app.get_wa_setting(f"nudge_count:{other}") == "0"


# ---- 7: undelivered is per episode ---------------------------------------------

def test_undelivered_marker_is_written_and_cleared_on_the_episode_key(wa, monkeypatch):
    sid = "wa-61400222666"
    app.conversations[sid] = [{"role": "user", "content": "prices?", "episode": 2,
                               "at": app.now_iso()}]
    monkeypatch.setattr(app, "send_whatsapp_via_twilio", lambda to, body: (False, "HTTP 500"))
    app._wa_generate_and_send("prices?", sid, "61400222666", False, "SMund1")
    assert app.get_wa_setting(f"undelivered:{sid}#e2") == "1"
    assert app.get_wa_setting(f"undelivered:{sid}") == ""

    monkeypatch.setattr(app, "send_whatsapp_via_twilio", lambda to, body: (True, "SMok"))
    app._wa_generate_and_send("timetable?", sid, "61400222666", False, "SMund2")
    assert app.get_wa_setting(f"undelivered:{sid}#e2") == ""


# ---- 8: the owner alert and the Momence push are per episode -------------------

def _alert_sandbox(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "SUPABASE_URL", "")
    monkeypatch.setattr(app, "SUPABASE_KEY", None)
    monkeypatch.setattr(app, "WA_STATE_FILE", tmp_path / "wa_state.json")
    app._wa_setting_cache.clear()
    monkeypatch.setattr(app, "lead_summary_delivery_configured", lambda: True)
    sent = []
    monkeypatch.setattr(app, "send_lead_summary_email",
                        lambda li: (sent.append(("email", li.get("phone"))), True)[1])
    monkeypatch.setattr(app, "send_lead_summary_phone",
                        lambda li: (sent.append(("phone", li.get("phone"))), True)[1])
    return sent


def test_owner_alert_and_momence_push_fire_again_in_a_new_episode(monkeypatch, tmp_path):
    sid = "wa-61400222777"
    sent = _alert_sandbox(monkeypatch, tmp_path)
    app.conversations[sid] = [{"role": "user", "content": "hi", "at": app.now_iso()}]
    lead = {"session_id": sid, "phone": "+61400222777", "email": "kim@example.com"}
    assert app.notify_lead_summary(dict(lead), reason="wa_ai_contact_capture") is True
    assert app.notify_lead_summary(dict(lead), reason="wa_ai_contact_capture") is False
    assert len(sent) == 2

    pushes = []
    monkeypatch.setattr(app, "push_lead_to_momence",
                        lambda li, **k: (pushes.append(li.get("email")), {"ok": True})[1])
    app._push_lead_guarded(dict(lead), source="whatsapp", session_id=sid)
    app._push_lead_guarded(dict(lead), source="whatsapp", session_id=sid)
    assert len(pushes) == 1

    app.conversations[sid] = [{"role": "user", "content": "back again", "episode": 2,
                               "at": app.now_iso()}]
    assert app.notify_lead_summary(dict(lead), reason="wa_ai_contact_capture") is True
    app._push_lead_guarded(dict(lead), source="whatsapp", session_id=sid)
    assert len(pushes) == 2, "Momence dedupes by email server-side, so a second push is safe"
    app.conversations.pop(sid, None)


# ---- 9: the model still sees the background, without the bookkeeping ----------

def test_agent_messages_cross_the_boundary_but_carry_only_role_and_content():
    sid = "wa-61400222888"
    app.conversations[sid] = [
        {"role": "user", "content": "I'm Kim, keen on Redfern", "at": "2026-07-01T09:00:00"},
        {"role": "assistant", "content": "G'day Kim, Redfern it is."},
        {"role": "user", "content": "hi", "episode": 2, "at": app.now_iso()},
    ]
    messages = app.build_agent_messages("hi", sid)
    non_system = [m for m in messages if m["role"] != "system"]
    assert any("I'm Kim" in m["content"] for m in non_system), "background survives the boundary"
    assert all(set(m) == {"role", "content"} for m in non_system)
    systems = [m["content"] for m in messages if m["role"] == "system"]
    assert any("FIRST reply" in s for s in systems), "episode two is met with an introduction"
    assert any("previous conversation" in s.lower() for s in systems)
    app.conversations.pop(sid, None)


# ---- 10: the website path is untouched -----------------------------------------

def test_episode_helpers_are_inert_on_a_website_session(wa):
    app.conversations.pop("widget-x", None)
    assert app.wa_episode_key("widget-x") == "widget-x"
    assert app.wa_episode_number("widget-x") == 1
    assert app.wa_episode_for_inbound([], "widget-x") == 1
    assert "widget-x" not in app.conversations, "no episode helper may fetch a widget thread"
    assert app.episode_history("widget-x") is app.load_conversation("widget-x")
    app.conversations.pop("widget-x", None)


def test_website_turns_are_not_stamped(wa, monkeypatch):
    monkeypatch.setattr(app, "generate_ai_reply", lambda m, s: ("Web answer.", "test"))
    res = wa.post("/api/chat", json={"message": "hi", "session_id": "widget-web1"})
    assert res.status_code == 200
    turns = app.load_conversation(res.json()["session_id"])
    assert turns and all("at" not in t and "episode" not in t for t in turns)


# ---- 11: a trim must not silently un-reset the episode -------------------------

def test_trim_carries_the_episode_marker_onto_the_first_kept_turn(monkeypatch):
    monkeypatch.setattr(app, "CONVERSATION_STATE_MAX_MESSAGES", 4)
    messages = [
        {"role": "user", "content": "old one", "at": "2026-07-01T09:00:00"},
        {"role": "user", "content": "new one", "episode": 2, "at": "2026-09-10T09:00:00"},
        {"role": "assistant", "content": "welcome back"},
        {"role": "user", "content": "prices?"},
        {"role": "assistant", "content": "here they are"},
        {"role": "user", "content": "timetable?"},
    ]
    kept = app.trim_conversation_state(list(messages))
    assert len(kept) == 4
    assert kept[0]["role"] == "assistant" and kept[0]["episode"] == 2
    sid = "wa-61400222999"
    app.conversations[sid] = kept
    assert len(app.episode_history(sid)) == 4, "the whole kept slice is still episode two"
    app.conversations.pop(sid, None)


# ---- 12: threads stored before 11 Sep carry no "at" -----------------------------

def test_legacy_turns_fall_back_to_the_transcript(wa):
    sid = "wa-61400223111"
    app.conversations[sid] = [{"role": "user", "content": "hi"},
                              {"role": "assistant", "content": "G'day"}]
    assert app.wa_episode_for_inbound(app.conversations[sid], sid) == 0, \
        "no timestamp anywhere means we cannot claim a gap"

    old = (datetime.now() - timedelta(hours=40)).isoformat(timespec="seconds")
    app.CONVERSATION_LOG_FILE.write_text(json.dumps(
        {"session_id": sid, "role": "user", "content": "hi", "timestamp": old}) + "\n")
    assert app.wa_episode_for_inbound(app.conversations[sid], sid) == 2
    app.conversations.pop(sid, None)


# ---- 13: a Twilio retry must not open a second episode -------------------------

def test_twilio_retry_of_the_opener_logs_one_conversation_started(wa):
    post(wa, "hi", message_sid="SMretry1")
    post(wa, "hi", message_sid="SMretry1")
    assert len(started()) == 1


# ---- 14: old episode markers are pruned from the local file --------------------

def test_prune_drops_markers_two_episodes_back(wa):
    for episode in (1, 2, 3):
        app.set_wa_setting(f"nudged:{SID}#e{episode}", "1")
        app.set_wa_setting(f"lead_alerted:{SID}#e{episode}", "x")
    app.set_wa_setting(f"opted_out:{SID}", "1")
    app._wa_prune_episode_keys(SID, 4)
    keys = set(json.loads(app.WA_STATE_FILE.read_text()))
    assert f"nudged:{SID}#e1" not in keys and f"lead_alerted:{SID}#e2" not in keys
    assert f"nudged:{SID}#e3" in keys and f"lead_alerted:{SID}#e3" in keys
    assert f"opted_out:{SID}" in keys, "lifetime flags are never pruned"


# ---- 15: the report counts conversations, not phone numbers --------------------

def test_report_counts_each_episode_as_a_conversation(wa):
    stamp = datetime.now().isoformat(timespec="seconds")
    rows = [
        {"timestamp": stamp, "event_type": "conversation_started", "session_id": SID, "episode": 1},
        {"timestamp": stamp, "event_type": "conversation_started", "session_id": SID, "episode": 2},
        {"timestamp": stamp, "event_type": "message_received", "session_id": SID},
    ]
    app.EVENTS_FILE.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    stats = app.build_report_stats(7)
    assert stats["wa_conversations"] == 2


# ---- 16: the 24 h window is untouched ------------------------------------------

def test_window_still_anchors_on_the_newest_inbound_after_a_boundary(wa):
    post(wa, "hi")
    backdate()
    post(wa, "hello again")
    assert app.wa_episode_number(SID) == 2
    window = app.wa_window_state(SID)
    assert window["window_open"] is True
    assert window["minutes_remaining"] > 23 * 60
