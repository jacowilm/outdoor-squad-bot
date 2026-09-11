"""Two quick messages get one answer, in order.

Item #10 of the 11 Sep 2026 website-vs-WhatsApp diff
(outbox/WEB-VS-WHATSAPP-DIFF-2026-09-11.md). Every inbound used to spawn its
own daemon thread with no per-thread lock, so a question followed fifteen
seconds later by "hello?" produced two overlapping replies, the second written
without the first, and an instant scripted answer could overtake the AI answer
to the message before it.
"""
import base64
import hashlib
import hmac
import importlib
import json
import os
import sys
import threading
import time

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
SENDER = "whatsapp:+61452117300"
SID = "wa-61452117300"

# Messages the real gate hands to the AI, so the webhook defers and takes the
# in-flight claim. Checked against should_use_local_tone_handler, not guessed.
AI_MESSAGES = [
    "what are my options?",
    "who runs the warm up",
    "is there a limit on numbers",
    "do i need to book ahead",
    "hello?",
]


@pytest.fixture()
def wa(monkeypatch, tmp_path):
    """Real gate, real scripted replies, deferred workers, no network."""
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
    monkeypatch.setattr(app, "notify_lead_summary_async", lambda li, **k: None)
    monkeypatch.setattr(app, "maybe_push_lead_to_momence", lambda *a, **k: None)
    monkeypatch.setattr(app, "is_rate_limited", lambda *a, **k: False)

    ai_calls = []

    def _fake_ai(message, session_id):
        ai_calls.append(message)
        return (f"Answer number {len(ai_calls)} from the Squad.", "test")

    monkeypatch.setattr(app, "generate_ai_reply", _fake_ai)

    workers = []
    monkeypatch.setattr(app, "_wa_async_dispatch",
                        lambda target, args: workers.append((target, args)))
    sent = []
    monkeypatch.setattr(
        app, "send_whatsapp_via_twilio",
        lambda to, body: (sent.append((to, body)), (True, f"SMout{len(sent)}"))[1],
    )
    app.conversations.clear()
    app._twilio_wa_seen_sids.clear()
    app._wa_setting_cache.clear()
    app._wa_inflight.clear()
    app._wa_pending.clear()
    c = TestClient(app.app)
    c.sent = sent
    c.ai_calls = ai_calls
    c.workers = workers

    def run_workers():
        while workers:
            target, args = workers.pop(0)
            target(*args)

    c.run_workers = run_workers
    yield c
    app._wa_inflight.clear()
    app._wa_pending.clear()


def _sign(params):
    base = app.TWILIO_WA_FALLBACK_HOSTS[0]
    payload = base + WEBHOOK_PATH + "".join(k + str(params[k]) for k in sorted(params))
    return base64.b64encode(hmac.new(TEST_TOKEN.encode(), payload.encode(), hashlib.sha1).digest()).decode()


_n = [0]


def post(client, body, sender=SENDER, sid=None):
    _n[0] += 1
    params = {"From": sender, "To": "whatsapp:+61499000000", "Body": body,
              "MessageSid": sid or f"SMq{_n[0]:05d}", "NumMedia": "0"}
    return client.post(WEBHOOK_PATH, data=params, headers={"X-Twilio-Signature": _sign(params)})


def events(client, name):
    rows = []
    for line in app.EVENTS_FILE.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("event_type") == name:
            rows.append(row)
    return rows


def bodies(client):
    return [body for _to, body in client.sent]


# ---- the queue itself ------------------------------------------------------

def test_two_inbounds_during_one_generation_get_one_extra_answer(wa):
    assert post(wa, AI_MESSAGES[0]).status_code == 200
    assert len(wa.workers) == 1 and not wa.sent

    second = post(wa, AI_MESSAGES[1])
    third = post(wa, AI_MESSAGES[2])
    assert second.status_code == 200 and third.status_code == 200
    # Twilio is acked instantly with an empty TwiML for a queued message.
    assert "<Message>" not in second.text and "<Message>" not in third.text
    assert [e["queued"] for e in events(wa, "wa_reply_queued")] == [1, 2]
    # Only the first inbound spawned a worker; nothing has been sent yet.
    assert len(wa.workers) == 1 and not wa.sent

    wa.run_workers()
    assert len(wa.sent) == 2
    assert bodies(wa)[0].startswith("Answer number 1")
    assert bodies(wa)[1].startswith("Answer number 2")
    coalesced = events(wa, "wa_reply_coalesced")
    assert len(coalesced) == 1 and coalesced[0]["count"] == 2
    assert [t["role"] for t in app.load_conversation(SID)] == [
        "user", "user", "user", "assistant", "assistant"]
    assert not app._wa_inflight and not app._wa_pending


def test_three_queued_messages_are_answered_as_one_turn_in_order(wa):
    post(wa, AI_MESSAGES[0])
    post(wa, AI_MESSAGES[1])
    post(wa, AI_MESSAGES[2])
    post(wa, AI_MESSAGES[3])
    wa.run_workers()

    assert len(wa.sent) == 2
    assert wa.ai_calls[0] == AI_MESSAGES[0]
    assert wa.ai_calls[1] == "\n".join(AI_MESSAGES[1:4])
    assert events(wa, "wa_reply_coalesced")[0]["count"] == 3


def test_scripted_answer_waits_behind_the_ai_answer(wa):
    post(wa, AI_MESSAGES[0])
    queued = post(wa, "how much is it?")
    # The price ladder is instant, which is exactly how it used to overtake
    # the AI answer to the message before it.
    assert "<Message>" not in queued.text
    assert not wa.sent

    wa.run_workers()
    assert len(wa.sent) == 2
    assert bodies(wa)[0].startswith("Answer number 1")
    assert "Free trial" in bodies(wa)[1] and "$51/wk" in bodies(wa)[1]
    assert len(wa.ai_calls) == 1
    assert events(wa, "local_tone_handler_used")
    # One AI answer and one scripted answer, not two AI answers.
    assert not events(wa, "wa_reply_coalesced")


# ---- opt-out, kill switch, mute -------------------------------------------

def test_opt_out_is_never_queued_and_stops_the_answer_in_flight(wa):
    post(wa, AI_MESSAGES[0])
    stop = post(wa, "stop")

    assert "stop here" in stop.text
    assert app.get_wa_setting(f"opted_out:{SID}") == "1"
    assert not app._wa_pending.get(SID)
    assert not events(wa, "wa_reply_queued")

    wa.run_workers()
    assert not wa.sent
    suppressed = events(wa, "wa_reply_suppressed")
    assert len(suppressed) == 1 and suppressed[0]["reason"] == "opted_out"


def test_kill_switch_flipped_before_the_drain_sends_nothing(wa, monkeypatch):
    post(wa, AI_MESSAGES[0])
    post(wa, AI_MESSAGES[1])
    monkeypatch.setattr(app, "wa_channel_enabled", lambda: False)

    wa.run_workers()
    assert not wa.sent
    assert events(wa, "wa_reply_suppressed")[0]["reason"] == "channel_off"
    assert not [t for t in app.load_conversation(SID) if t["role"] == "assistant"]


def test_mute_flipped_before_the_drain_sends_nothing(wa, monkeypatch):
    post(wa, AI_MESSAGES[0])
    post(wa, AI_MESSAGES[1])
    monkeypatch.setattr(app, "wa_muted", lambda sid: True)

    wa.run_workers()
    assert not wa.sent
    assert events(wa, "wa_reply_suppressed")[0]["reason"] == "muted"


# ---- stuck claims, retries, floods ----------------------------------------

def test_stale_claim_is_taken_over_and_the_old_worker_sends_nothing(wa):
    post(wa, AI_MESSAGES[0])
    # A worker that crashed without releasing, or one hung past the max age.
    app._wa_inflight[SID] = time.time() - 1000
    post(wa, AI_MESSAGES[1])
    assert len(wa.workers) == 2
    assert not events(wa, "wa_reply_queued")

    wa.run_workers()
    assert len(wa.sent) == 1
    superseded = events(wa, "wa_reply_superseded")
    assert len(superseded) == 1 and superseded[0]["kind"] in ("intro", "reply")
    assert not app._wa_inflight


def test_expired_claim_does_not_wedge_the_thread(wa):
    post(wa, AI_MESSAGES[0])
    app._wa_inflight[SID] = time.time() - (app.WA_INFLIGHT_MAX_AGE_SECONDS + 60)
    post(wa, AI_MESSAGES[1])
    # The second message was answered on its own rather than parked forever.
    assert not app._wa_pending.get(SID)
    assert len(wa.workers) == 2


def test_twilio_retry_of_a_queued_message_does_not_requeue(wa):
    post(wa, AI_MESSAGES[0])
    post(wa, AI_MESSAGES[1], sid="SMretry001")
    assert len(app._wa_pending[SID]) == 1

    retry = post(wa, AI_MESSAGES[1], sid="SMretry001")
    assert retry.status_code == 200 and "<Message>" not in retry.text
    assert len(app._wa_pending[SID]) == 1

    wa.run_workers()
    assert len(wa.sent) == 2


def test_rate_limit_still_applies_before_queueing(wa, monkeypatch):
    post(wa, AI_MESSAGES[0])
    monkeypatch.setattr(app, "is_rate_limited", lambda *a, **k: True)
    flood = post(wa, AI_MESSAGES[1])

    assert "<Message>" not in flood.text
    assert not app._wa_pending.get(SID)
    assert events(wa, "wa_rate_limited")
    assert not events(wa, "wa_reply_queued")


def test_crashed_worker_releases_the_claim_and_reports_the_queue(wa, monkeypatch):
    post(wa, AI_MESSAGES[0])
    post(wa, AI_MESSAGES[1])

    def _boom(to, body):
        raise RuntimeError("twilio exploded")

    monkeypatch.setattr(app, "send_whatsapp_via_twilio", _boom)
    with pytest.raises(RuntimeError):
        wa.run_workers()

    assert not app._wa_inflight and not app._wa_pending
    dropped = events(wa, "wa_queue_dropped")
    assert len(dropped) == 1
    assert dropped[0]["count"] == 1 and dropped[0]["had_opt_out"] == "0"


def test_flood_is_coalesced_from_the_newest_six_and_capped(wa):
    post(wa, AI_MESSAGES[0])
    parked = []
    for i in range(10):
        text = (f"please tell me more about the squad number {i} " * 20)[:500]
        parked.append(text)
        post(wa, text)
    wa.run_workers()

    assert len(wa.sent) == 2
    coalesced = events(wa, "wa_reply_coalesced")[0]
    assert coalesced["count"] == 10
    assert coalesced["chars"] == sum(len(t) for t in parked[-6:])
    drained = wa.ai_calls[1]
    assert len(drained) == app.MAX_MESSAGE_LEN
    assert drained.startswith(parked[-6])
    assert parked[0] not in drained


# ---- the seam itself -------------------------------------------------------

def test_worker_still_runs_without_a_token(wa):
    app._wa_generate_and_send(AI_MESSAGES[0], SID, "61452117300", False, "SMdirect")
    assert len(wa.sent) == 1
    assert not app._wa_inflight and not app._wa_pending


def test_real_threads_keep_the_order_and_release_the_claim(wa, monkeypatch):
    monkeypatch.setattr(
        app, "_wa_async_dispatch",
        lambda target, args: threading.Thread(target=target, args=args, daemon=True).start(),
    )
    gate = threading.Event()
    calls = []

    def _blocking_ai(message, session_id):
        calls.append(message)
        if len(calls) == 1:
            gate.wait(5)
        return (f"Answer number {len(calls)} from the Squad.", "test")

    monkeypatch.setattr(app, "generate_ai_reply", _blocking_ai)

    post(wa, AI_MESSAGES[0])
    deadline = time.time() + 5
    while SID not in app._wa_inflight and time.time() < deadline:
        time.sleep(0.02)
    assert SID in app._wa_inflight

    post(wa, AI_MESSAGES[1])
    assert len(app._wa_pending[SID]) == 1
    gate.set()

    deadline = time.time() + 5
    while (app._wa_inflight or len(wa.sent) < 2) and time.time() < deadline:
        time.sleep(0.02)

    assert len(wa.sent) == 2
    assert bodies(wa)[0].startswith("Answer number 1")
    assert bodies(wa)[1].startswith("Answer number 2")
    assert not app._wa_inflight and not app._wa_pending
