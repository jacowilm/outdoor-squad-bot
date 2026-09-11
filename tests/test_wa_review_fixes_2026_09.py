"""Review of the 11 Sep 2026 WhatsApp diff work: the fixes that fix the fixes.

Every guard shipped that day was re-checked against the real code paths, and
eleven of them did not do what their comment said. Each test below pins one of
those, the way it fails in production rather than the way the original test
stubbed it:

  * save_lead's missing-column fail-soft could never fire, because httpx's
    error string carries the status line and not the PostgREST reason.
  * the episode key collapsed to episode 1 whenever the conversation cache was
    cold, which is the normal state on the nudge sweep and after a deploy.
  * a Twilio "accepted" wiped the failure flags the receipt for that same
    message had just written.
  * the opt-out was only checked in the AI worker, so the instant scripted
    replies answered after STOP.
  * the dashboard badge walk matched from the oldest end of the ledger.
  * the contact-ask rewrite fired on ordinary prose, and the strip deleted
    email asks and returned a lone mobile ask untouched.
  * the settings snapshot read 2000 unordered rows and said nothing when it
    truncated.
  * a send Twilio refused left no trace on the thread.
"""
import base64
import hashlib
import hmac
import importlib
import json
import os
import shutil
import subprocess
import sys
import textwrap
from datetime import datetime, timedelta

import httpx
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
SENDER = "whatsapp:+61400333222"
SID = "wa-61400333222"


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
    monkeypatch.setattr(app, "generate_ai_reply",
                        lambda m, s: ("G'day, Robo-Nick here. What can I sort out?", "test"))
    monkeypatch.setattr(app, "_wa_async_dispatch", lambda target, args: target(*args))
    monkeypatch.setattr(app, "notify_lead_summary_async", lambda li, **k: None)
    monkeypatch.setattr(app, "maybe_push_lead_to_momence", lambda *a, **k: None)
    monkeypatch.setattr(app, "is_rate_limited", lambda *a, **k: False)
    sent = []
    monkeypatch.setattr(app, "send_whatsapp_via_twilio",
                        lambda to, body: (sent.append((to, body)), (True, "SMout1"))[1])
    app.conversations.clear()
    app._twilio_wa_seen_sids.clear()
    app._wa_setting_cache.clear()
    client = TestClient(app.app)
    client.sent = sent
    return client


def _sign(params, path=WEBHOOK_PATH):
    base = app.TWILIO_WA_FALLBACK_HOSTS[0]
    payload = base + path + "".join(k + str(params[k]) for k in sorted(params))
    return base64.b64encode(
        hmac.new(TEST_TOKEN.encode(), payload.encode(), hashlib.sha1).digest()).decode()


_n = [0]


def post(client, body, sender=SENDER):
    _n[0] += 1
    params = {"From": sender, "To": "whatsapp:+61499000000", "Body": body,
              "MessageSid": f"SMrev{_n[0]:05d}", "NumMedia": "0"}
    return client.post(WEBHOOK_PATH, data=params,
                       headers={"X-Twilio-Signature": _sign(params)})


def events(event_type=None):
    rows = [json.loads(line) for line in app.EVENTS_FILE.read_text().splitlines() if line.strip()]
    return [r for r in rows if event_type is None or r.get("event_type") == event_type]


def backdate(session_id=SID, hours=40):
    stamp = (datetime.now() - timedelta(hours=hours)).isoformat(timespec="seconds")
    for turn in reversed(app.conversations.get(session_id, [])):
        if turn.get("role") == "user":
            turn["at"] = stamp
            return stamp
    raise AssertionError("no user turn to backdate")


def go_cold():
    """Exactly what a deploy, or an hour of somebody else's traffic, does."""
    app.conversations.clear()
    app.conversation_last_access.clear()
    app._wa_setting_cache.clear()


# ---- (1) the lead fail-soft fires against a REAL PostgREST 400 --------------

def _postgrest(monkeypatch, missing=(), logged=None):
    """The real httpx layer, answering 400 PGRST204 for a missing column."""
    posted = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=[])
        body = json.loads((request.content or b"{}").decode() or "{}")
        if app.SUPABASE_TABLES["leads"] not in str(request.url):
            # Events go to Supabase too once it is enabled, so they are read
            # here rather than from EVENTS_FILE.
            if logged is not None and app.SUPABASE_TABLES["events"] in str(request.url):
                logged.append(body)
            return httpx.Response(201, content=b"")
        for column in missing:
            if column in body:
                return httpx.Response(400, json={
                    "code": "PGRST204",
                    "message": (f"Could not find the '{column}' column of "
                                "'outdoor_squad_leads' in the schema cache"),
                })
        posted.append(body)
        return httpx.Response(201, content=b"")

    monkeypatch.setattr(app, "SUPABASE_URL", "https://project.supabase.co")
    monkeypatch.setattr(app, "SUPABASE_KEY", "service-role-key")
    monkeypatch.setattr(app, "_supabase_http", httpx.Client(transport=httpx.MockTransport(handler)))
    return posted


def test_a_postgrest_400_carries_its_reason_out_of_supabase_request(monkeypatch):
    _postgrest(monkeypatch, missing=("channel",))
    with pytest.raises(Exception) as caught:
        app.supabase_request("POST", app.SUPABASE_TABLES["leads"],
                             json_body={"channel": "website", "name": "x"})
    assert "Could not find the 'channel' column" in str(caught.value)
    assert app._supabase_missing_lead_column(caught.value, {"channel": "website"}) == "channel"


def test_a_missing_column_costs_the_field_not_the_lead_through_the_real_client(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "LEADS_FILE", tmp_path / "leads.json")
    app.LEADS_FILE.write_text("[]")
    monkeypatch.setattr(app, "EVENTS_FILE", tmp_path / "events.jsonl")
    app.EVENTS_FILE.write_text("")
    # BOTH new columns missing: the migration adds two, and the old two-pass
    # loop could only ever strip one.
    logged = []
    posted = _postgrest(monkeypatch, missing=("channel", "phone_typed"), logged=logged)
    app.save_lead({"session_id": SID, "channel": "whatsapp", "phone": "+61400333222",
                   "phone_typed": "0412 345 678", "name": "Sarah"})
    assert len(posted) == 1
    assert posted[0]["name"] == "Sarah" and posted[0]["phone"] == "+61400333222"
    assert "channel" not in posted[0] and "phone_typed" not in posted[0]
    assert json.loads(app.LEADS_FILE.read_text()) == [], "the lead reached Supabase"
    degraded = [e["metadata"]["column"] for e in logged
                if e.get("event_type") == "lead_storage_degraded"]
    assert sorted(degraded) == ["channel", "phone_typed"]
    assert [e for e in logged if e.get("event_type") == "lead_storage_error"] == []


# ---- (2) the episode key survives a cold conversation cache -----------------

def test_episode_key_is_the_same_key_after_a_restart(wa):
    post(wa, "hi")
    backdate()
    post(wa, "can I chat to a real human?")
    assert app.wa_episode_key(SID) == f"{SID}#e2"
    assert app.get_wa_setting(f"handoff:{SID}#e2") == "1"

    go_cold()
    assert app.wa_episode_number(SID) == 2
    assert app.wa_episode_key(SID) == f"{SID}#e2"


def test_a_cold_sweep_still_sees_the_handoff_and_the_spent_follow_up(wa):
    post(wa, "hi")
    backdate()
    post(wa, "can I chat to a real human?")
    later = datetime.now() + timedelta(minutes=app.WA_NUDGE_MINUTES + 10)
    assert SID not in app.wa_sessions_needing_nudge(later)

    # The thread is evicted before it is even eligible: the cache TTL is an
    # hour and the follow-up waits ninety minutes.
    go_cold()
    assert SID not in app.wa_sessions_needing_nudge(later), \
        "a cold sweep must not nudge someone Nick is about to phone"


def test_a_cold_sweep_is_not_silenced_by_the_previous_episodes_marker(wa):
    post(wa, "hi")
    app.set_wa_setting(f"nudged:{SID}", "1")
    backdate()
    post(wa, "hello again")
    later = datetime.now() + timedelta(minutes=app.WA_NUDGE_MINUTES + 10)
    assert SID in app.wa_sessions_needing_nudge(later)

    go_cold()
    assert SID in app.wa_sessions_needing_nudge(later), \
        "episode 1's spent follow-up must not outlive its episode"


def test_a_late_delivery_receipt_lands_on_the_live_episode(wa):
    post(wa, "hi")
    backdate()
    post(wa, "hello again")
    go_cold()
    app._wa_apply_delivery_receipt(SID, "SMlate", "failed", "63016")
    assert app.get_wa_setting(f"undelivered:{SID}#e2") == "1"
    assert app.get_wa_setting(f"undelivered:{SID}") == ""


# ---- (3) an "accepted" never wipes this message's own failure ---------------

def test_a_receipt_that_beats_the_worker_is_not_cleared_by_the_send(wa, monkeypatch):
    def _send(to_digits, body):
        # Twilio accepts, then posts the terminal receipt while the worker is
        # still writing the transcript.
        app._wa_apply_delivery_receipt(SID, "SMout1", "failed", "63016")
        return True, "SMout1"

    monkeypatch.setattr(app, "send_whatsapp_via_twilio", _send)
    post(wa, "what's the vibe like at Camperdown, competitive or relaxed?")
    ledger = app.wa_delivery_ledger(SID)
    assert ledger and ledger[-1]["status"] == "failed" and ledger[-1]["error"] == "63016"
    assert app.get_wa_setting(f"undelivered:{SID}") == "1", "the ledger and the flag must agree"
    assert app.get_wa_setting(f"intro_failed:{SID}") == "1"
    assert app.wa_needs_intro(SID) is True
    later = datetime.now() + timedelta(minutes=app.WA_NUDGE_MINUTES + 10)
    assert SID not in app.wa_sessions_needing_nudge(later)


def test_a_clean_send_still_clears_a_stale_flag(wa):
    app.set_wa_setting(f"undelivered:{SID}", "1")
    post(wa, "what's the vibe like at Camperdown, competitive or relaxed?")
    assert app.get_wa_setting(f"undelivered:{SID}") == ""


# ---- (4) the opt-out is checked before the instant scripted replies ---------

def test_the_scripted_branch_goes_quiet_after_stop(wa):
    assert "I'll stop here" in post(wa, "stop").text
    assert app.get_wa_setting(f"opted_out:{SID}") == "1"

    before = len(wa.sent)
    for question in ["how much is a membership?", "what times do you run?",
                     "where do you meet?", "is there parking?"]:
        body = post(wa, question).text
        assert "<Message>" not in body, f"{question!r} was answered after STOP: {body}"
    assert len(wa.sent) == before, "nothing went out over REST either"
    suppressed = [e for e in events("wa_reply_suppressed") if e["reason"] == "opted_out"]
    assert len(suppressed) == 4
    assert app.get_wa_setting(f"opted_out:{SID}") == "1"


def test_a_fresh_stop_is_still_acknowledged_once(wa):
    post(wa, "stop")
    # Someone who writes STOP twice still gets the one-line acknowledgement,
    # never silence they might read as the bot ignoring them.
    assert "I'll stop here" in post(wa, "please stop").text


# ---- (5) the dashboard badges are matched from the newest end --------------

_LADDER = "The main doors are:\n\n- Free trial: $0"
_NUDGE = app.WA_NUDGE_TEXT


def _badges(ledger, rows):
    """Run the dashboard's own badge walk, lifted out of app.py, under node."""
    node = shutil.which("node")
    if not node:  # pragma: no cover - developer machines without node
        pytest.skip("node is needed to run the dashboard's own javascript")
    source = open(app.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
    start = source.index("      const ledger = thread.deliveries || [];")
    end = source.index("      document.getElementById('waMessages').innerHTML", start)
    walk = source[start:end]
    script = (
        "function esc(s){return String(s);}\n"
        "const thread = " + json.dumps({"session_id": "wa-1", "deliveries": ledger}) + ";\n"
        "const rows = " + json.dumps(rows) + ";\n"
        "function waThreadMessages(){return rows;}\n"
        + textwrap.dedent(walk) +
        "console.log(JSON.stringify(waRows.map(function(m,i){"
        "return m.role === 'user' ? '' : (waBadges[i] || '');})));\n"
    )
    out = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_a_repeated_reply_older_than_the_ledger_does_not_steal_the_badge():
    # Ten entries is the whole ledger, so the two oldest assistant rows are off
    # it; both say exactly what the newest two say, which is what scripted copy
    # does.
    ledger = [{"sid": f"SM{i}", "group": f"SM{i}", "kind": "reply", "status": "delivered",
               "error": "", "preview": f"Answer number {i} for the"} for i in range(8)]
    ledger.append({"sid": "SM8", "group": "SM8", "kind": "nudge", "status": "delivered",
                   "error": "", "preview": _NUDGE[:40]})
    ledger.append({"sid": "SM9", "group": "SM9", "kind": "reply", "status": "failed",
                   "error": "63016", "preview": _LADDER[:40]})
    rows = [{"role": "user", "content": "prices?"},
            {"role": "assistant", "content": _LADDER},
            {"role": "assistant", "content": _NUDGE}]
    rows += [{"role": "assistant", "content": f"Answer number {i} for the squad"} for i in range(8)]
    rows += [{"role": "assistant", "content": _NUDGE},
             {"role": "user", "content": "prices again?"},
             {"role": "assistant", "content": _LADDER}]
    badges = _badges(ledger, rows)
    assert "Not delivered" in badges[-1] and "63016" in badges[-1]
    assert badges[1] == "", "the copy from weeks ago must not wear the newest badge"
    assert "Delivered" in badges[-3], "the follow-up keeps its own badge"
    assert badges[2] == ""


# ---- (6) the rewrite only rewrites sentences that ASK ----------------------

@pytest.mark.parametrize("sentence", [
    "Your mobile number is only used so Nick can text you back, and we never send it anywhere else.",
    "Nobody will pop your mobile number on a mailing list.",
    "Your mobile number only goes to Nick and Lyn, and we never share it with anyone else.",
    "I'll flag your question with Nick, and your mobile number stays with the team.",
    "We never share your phone number with anyone outside the Squad.",
])
def test_a_sentence_that_only_mentions_the_number_is_left_alone(sentence):
    assert app.wa_rewrite_contact_asks(sentence) == sentence
    # And the same sentence next to a real ask, which is where it usually sits.
    both = sentence + " Drop your first name + mobile and Lyn will sort it."
    rewritten = app.wa_rewrite_contact_asks(both)
    assert sentence in rewritten and "+ mobile" not in rewritten


@pytest.mark.parametrize("ask", [
    "Drop your mobile number and I'll get Nick onto it.",
    "Send me your mobile number and Lyn will call you.",
    "Happy to sort that, just share your phone number and we'll call.",
])
def test_a_real_ask_still_becomes_a_first_name_ask(ask):
    out = app.wa_rewrite_contact_asks(ask)
    assert "first name" in out and "number" not in out


# ---- (7) the settings snapshot is filtered and paged -----------------------

def test_the_settings_snapshot_pages_past_the_old_two_thousand_row_cap(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "WA_STATE_FILE", tmp_path / "wa_state.json")
    app._wa_setting_cache.clear()
    stored = [{"key": f"{app._WA_SETTING_PREFIX}filler:{i}", "value": "1",
               "updated_at": "2026-07-01T00:00:00"} for i in range(2400)]
    stored.append({"key": f"{app._WA_SETTING_PREFIX}opted_out:{SID}", "value": "1",
                   "updated_at": "2026-09-11T00:00:00"})
    stored.append({"key": "unrelated::thing", "value": "1", "updated_at": "2026-09-11T00:00:00"})
    calls = []

    def fake_request(method, table, *, params=None, json_body=None, prefer=None):
        calls.append(dict(params or {}))
        assert params.get("key") == f"like.{app._WA_SETTING_PREFIX}*", "filter it server side"
        rows = [r for r in stored if r["key"].startswith(app._WA_SETTING_PREFIX)]
        offset, limit = int(params["offset"]), int(params["limit"])
        return rows[offset:offset + limit]

    monkeypatch.setattr(app, "supabase_enabled", lambda: True)
    monkeypatch.setattr(app, "supabase_request", fake_request)
    snapshot = app.wa_settings_snapshot()
    assert len(calls) > 1, "one flat read is how rows fell off the end"
    assert snapshot.get(f"opted_out:{SID}") == "1"
    assert snapshot.get("filler:2399") == "1"
    assert "unrelated::thing" not in snapshot


# ---- (8) a refused send shows on the thread -------------------------------

def test_a_refused_send_shows_as_not_delivered_on_the_dashboard(wa, monkeypatch):
    monkeypatch.setattr(app, "send_whatsapp_via_twilio",
                        lambda to, body: (False, 'HTTP 400: {"code":63024,"message":"no"}'))
    post(wa, "what's the vibe like at Camperdown, competitive or relaxed?")
    ledger = app.wa_delivery_ledger(SID)
    assert ledger and ledger[-1]["status"] == "failed"
    assert ledger[-1]["sid"].startswith("failed:"), "no Twilio SID exists for a refusal"
    assert ledger[-1]["kind"] == "intro"
    assert ledger[-1]["error"] == "63024", "the code Nick can look up, not the raw body"
    # No transcript row exists for a refused send, so the entry carries no
    # preview and cannot take the badge off the last message that did land.
    assert ledger[-1]["preview"] == ""
    thread = next(t for t in app.wa_dashboard_payload()["conversations"]
                  if t["session_id"] == SID)
    assert thread["last_outbound_failed"] is True
    # Send-then-persist still holds: no answer is invented in the transcript.
    assert not [t for t in app.load_conversation(SID) if t.get("role") == "assistant"]


def test_a_refusal_does_not_steal_the_badge_from_the_message_that_landed(wa, monkeypatch):
    # The same answer twice: the first lands, the second is refused. Robo-Nick
    # repeats himself by design, so the previews are identical.
    outcomes = [(True, "SMa"), (False, 'HTTP 400: {"code":63024}')]
    calls = []

    def _send(to_digits, body):
        calls.append(body)
        return outcomes[min(len(calls) - 1, 1)]

    monkeypatch.setattr(app, "send_whatsapp_via_twilio", _send)
    post(wa, "what's the vibe like, competitive or relaxed?")
    post(wa, "and is it the same at Redfern?")
    thread = next(t for t in app.wa_dashboard_payload()["conversations"]
                  if t["session_id"] == SID)
    rows = [{"role": r["role"], "content": r["content"]}
            for r in app.read_conversation_logs() if r["session_id"] == SID]
    badges = _badges(thread["deliveries"], rows)
    assert thread["last_outbound_failed"] is True, "the header still says it failed"
    assert not any("Not delivered" in b for b in badges), \
        "the message the customer DID receive must not be marked as failed"


# ---- (9) the email ask survives, the lone number ask does not --------------

def test_a_whatsapp_reply_can_still_ask_for_an_email(wa):
    app.conversations[SID] = [{"role": "user", "content": "I'd like to book the intro class"}]
    draft = ("Good pick, the intro class is free.\n\n"
             "What's your email address so Lyn can send the booking confirmation?")
    out = app.enforce_contact_and_handoff_progression(draft, SID)
    assert "email address" in out, "the email is the one detail WhatsApp does not give us"
    assert "intro class is free" in out


def test_a_reply_that_is_nothing_but_a_number_ask_is_replaced_not_sent(wa):
    app.conversations[SID] = [{"role": "user", "content": "can someone call me about the SPT"}]
    for draft in ["What's the best mobile number to reach you on?",
                  "What's your mobile number?",
                  "Can I grab your mobile number?",
                  "Could you give me your mobile number and I'll pass it on?"]:
        out = app.enforce_contact_and_handoff_progression(draft, SID)
        assert out.strip()
        assert "mobile" not in out.lower() and "phone number" not in out.lower(), out
        assert "first name" in out.lower(), out

    app.conversations[SID] = [{"role": "assistant", "content": "What's your first name?"},
                              {"role": "user", "content": "Sarah"}]
    out = app.enforce_contact_and_handoff_progression("What's your mobile number?", SID)
    assert out.strip() and "mobile" not in out.lower()
    assert "first name" not in out.lower(), "her name is known, so do not ask again"


def test_a_half_sent_answer_is_badged_red_on_the_row_the_customer_got(wa, monkeypatch):
    body = ("Camperdown runs 6am and 5:30pm. " * 60).strip() + "\n\n" + "Redfern runs 6am and 6pm."
    assert len(app.split_whatsapp_body(body)) == 2
    calls = []

    def _send(to_digits, part):
        calls.append(part)
        return (True, "SMpart1") if len(calls) == 1 else (False, 'HTTP 400: {"code":63016}')

    monkeypatch.setattr(app, "send_whatsapp_via_twilio", _send)
    outcome, _detail, sent_parts, _attempts = app._wa_send_and_register(
        SID, "61400333222", body, "reply")
    assert (outcome, sent_parts) == ("partial", 1)
    ledger = app.wa_delivery_ledger(SID)
    assert [e["status"] for e in ledger] == ["accepted", "failed"]
    assert ledger[1]["group"] == "SMpart1", "one answer, one group, worst status wins"
    rows = [{"role": "user", "content": "what times do you run?"},
            {"role": "assistant", "content": calls[0]}]
    badges = _badges(ledger, rows)
    assert "Not delivered" in badges[1] and "63016" in badges[1]
