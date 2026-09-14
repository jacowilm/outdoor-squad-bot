"""Coverage for the shared final-answer topic-link footer (attach_topic_footer,
wired into prevent_repetitive_reply — the single chokepoint every /api/chat and
WhatsApp reply path funnels through) plus the 2026-09-14 SPT3x/YTP pricing-ladder
fix. Companion to test_robonick_prices_links_2026_09.py, which covers the two
direct call sites (main-doors pricing answer, timetable_reply) in isolation.

Scope here: YTP-specific and SPT-deflection price answers, timetable no-match,
locations/trial, repetition handling, a real signed-WhatsApp-webhook integration
test through the REAL routing gate, a sensitive/mixed-topic negative case, and a
stubbed-LLM-output negative case. No live provider calls anywhere.
"""

import base64
import hashlib
import hmac
import importlib
import os
import sys
import tempfile
from pathlib import Path

import pytest

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

from fastapi.testclient import TestClient  # noqa: E402

_TMP = Path(tempfile.mkdtemp(prefix="os-footer-test-"))
app.LEADS_FILE = _TMP / "leads.json"; app.LEADS_FILE.write_text("[]")
app.EVENTS_FILE = _TMP / "events.jsonl"; app.EVENTS_FILE.write_text("")
app.CONVERSATION_LOG_FILE = _TMP / "conversation_logs.jsonl"; app.CONVERSATION_LOG_FILE.write_text("")
client = TestClient(app.app)

MEMBERSHIP_URL = "https://www.outdoorsquad.com.au/membership-options"
TIMETABLE_URL = "https://www.outdoorsquad.com.au/timetable-and-classes"
LOCATIONS_URL = "https://www.outdoorsquad.com.au/bootcamp-locations"
TRIAL_URL = "https://www.outdoorsquad.com.au/new-page-squad-intro-offer"


def direct_reply(msg, sid):
    """Real deterministic routing (demo_fallback_reply), through the real
    final-answer chokepoint (prevent_repetitive_reply) — the same pattern
    tests/test_functional_and_round2_2026_07.py already uses."""
    hist = app.load_conversation(sid)
    hist.append({"role": "user", "content": msg})
    out = app.demo_fallback_reply(msg, session_id=sid)
    out = app.prevent_repetitive_reply(out, msg, sid)
    hist.append({"role": "assistant", "content": out})
    return out


# ── YTP-specific and SPT-deflection price answers ─────────────────────────────

def test_ytp_specific_price_answer_gets_membership_footer():
    sid = "footer-ytp-A"
    direct_reply("do you do anything for kids? my son's 13", sid)
    r = direct_reply("how much is it for him?", sid)

    assert "$25/wk" in r
    assert MEMBERSHIP_URL in r
    assert r.count(MEMBERSHIP_URL) == 1
    # Still an answer first, link second.
    assert r.index("$25/wk") < r.index(MEMBERSHIP_URL)


def test_spt_deflection_answer_gets_membership_footer():
    # The group-or-SPT deflection reply already states both SPT tiers
    # ($125/wk and $175/wk) but never carried a link.
    sid = "footer-spt-deflect"
    direct_reply("is it worth it or should I just do group classes", sid)
    r = direct_reply("what would spt even cost me", sid)

    assert "$175/wk" in r
    assert MEMBERSHIP_URL in r
    assert r.count(MEMBERSHIP_URL) == 1


# ── timetable no-match branch ──────────────────────────────────────────────────

def test_timetable_no_match_combo_gets_timetable_footer():
    sid = "footer-timetable-nomatch"
    # Redfern has no evening sessions — the "had_filter and not filtered"
    # branch, which only ever carried the Momence booking link inline.
    r = app.timetable_reply(app.normalise_chat_text("evening classes at redfern"), sid)
    r = app.prevent_repetitive_reply(r, "evening classes at redfern", sid)

    assert "redfern doesn't run evening sessions" in r.lower()
    assert TIMETABLE_URL in r
    assert r.count(TIMETABLE_URL) == 1


# ── locations / trial topics ───────────────────────────────────────────────────

def test_venue_address_question_gets_locations_footer():
    sid = "footer-locations"
    r = direct_reply("where exactly is the camperdown session?", sid)

    assert "mallett st" in r.lower()
    assert LOCATIONS_URL in r
    assert r.count(LOCATIONS_URL) == 1


def test_trial_question_gets_trial_footer():
    sid = "footer-trial"
    r = direct_reply("how does the free trial work", sid)

    assert TRIAL_URL in r
    assert r.count(TRIAL_URL) == 1


# ── repetition handling ────────────────────────────────────────────────────────

def test_repeated_pricing_question_keeps_full_ladder_and_single_footer():
    sid = "footer-repeat"
    first = direct_reply("What are your prices?", sid)
    second = direct_reply("what are your prices?", sid)

    assert "$175/wk" in first and "youth training program" in first.lower()

    # The repeat detector rewrites the SECOND answer (non_repeating_followup
    # -> the "Short version" ladder), but it must be rewritten to a reply
    # that STILL carries every current tier and exactly one footer, not a
    # stripped-down version that dropped SPT3x/Youth/Student again.
    assert second != first
    assert "$175/wk" in second  # SPT 3x + Group
    assert "$25/wk per kid" in second  # Youth Training Program
    assert "Squad Student $25/wk" in second
    assert second.count(MEMBERSHIP_URL) == 1


# ── negative: sensitive/mixed-topic query must never carry a footer ────────────

def test_injury_plus_price_question_gets_no_footer():
    sid = "footer-negative-injury"
    r = direct_reply("I tweaked my back, how much does SPT cost?", sid)

    assert MEMBERSHIP_URL not in r
    assert "individual" in r.lower() or "physio" in r.lower() or "practitioner" in r.lower()


def test_contact_capture_moment_gets_no_footer():
    # attach_topic_footer must not interrupt a contact-capture / handoff ask
    # even when the triggering question was about price.
    contact_ask_reply = (
        "Honest answer: that one's outside what Robo-Nick can reliably do. "
        "Humanoid-Nick kept the improv rights for himself.\n\n"
        "Drop your first name + mobile and he or Lyn will sort it properly, "
        "or grab the free trial whenever you're ready, and it should cost you nothing to ask about pricing here."
    )
    out = app.attach_topic_footer(contact_ask_reply, "how much does it cost?", "footer-negative-contact")
    assert out == contact_ask_reply
    assert MEMBERSHIP_URL not in out


# ── stubbed LLM output (no live provider calls) ────────────────────────────────

def test_stubbed_ai_reply_on_non_topic_message_gets_no_footer(monkeypatch):
    # should_use_local_tone_handler is the REAL routing gate, deliberately
    # left unstubbed. Only the provider call itself is stubbed, so a message
    # with no price/timetable/location/trial signal genuinely reaches the AI
    # path and must pass through untouched.
    def _stub_provider(message, session_id):
        return ("Sounds like a solid week. Keep the consistency up.", "stub")

    monkeypatch.setattr(app, "generate_ai_reply", _stub_provider)

    sid = "footer-llm-nontopic"
    message = "Really appreciated how patient the coaching was today, made a big difference for me"
    assert app.should_use_local_tone_handler(message, sid) is False

    reply, provider = app.generate_ai_reply(message, sid)
    out = app.prevent_repetitive_reply(reply, message, sid)

    assert provider == "stub"
    assert out == reply
    assert MEMBERSHIP_URL not in out and TIMETABLE_URL not in out


def test_attach_topic_footer_directly_covers_any_future_llm_callsite():
    # Direct unit coverage of the shared function itself: if a future
    # provider callsite routes a genuine pricing answer through
    # attach_topic_footer (not just prevent_repetitive_reply), the footer
    # still attaches once, appended after the answer.
    llm_style_answer = (
        "Sure thing — Squad Ascent is $51 a week for unlimited coached group classes, "
        "and there's a free trial if you want to test it out before committing to anything."
    )
    out = app.attach_topic_footer(llm_style_answer, "what are your prices", "footer-direct-llm")

    assert out.startswith(llm_style_answer)
    assert MEMBERSHIP_URL in out
    assert out.count(MEMBERSHIP_URL) == 1


# ── real signed-WhatsApp-webhook integration (real routing gate) ──────────────
# Deterministic replies ride TwiML INLINE in the webhook response (not the
# deferred REST path — that's the AI-answer path), so this parses the actual
# <Message> element with ElementTree, matching
# tests/test_rc_identity_routing_2026_09.py's established pattern, rather
# than asserting against a REST-send capture list.

import xml.etree.ElementTree as ET  # noqa: E402

WEBHOOK_PATH = "/twilio-wa-webhook"
TEST_TOKEN = "test-auth-token-for-footer-signatures"


def _sign(base: str, params: dict, token: str = TEST_TOKEN) -> str:
    payload = base + WEBHOOK_PATH + "".join(k + str(params[k]) for k in sorted(params))
    return base64.b64encode(hmac.new(token.encode(), payload.encode(), hashlib.sha1).digest()).decode()


def _twiml_message_text(response_text: str) -> str:
    root = ET.fromstring(response_text)
    node = root.find("Message")
    return node.text or "" if node is not None else ""


@pytest.fixture()
def wa_client(monkeypatch, tmp_path):
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
    # Real routing gate: should_use_local_tone_handler is intentionally left
    # unstubbed. The provider itself is stubbed to raise, matching
    # test_rc_identity_routing_2026_09.py's pattern — a pricing question is
    # deterministic and answered inline, so this call is never reached; if a
    # gate regression ever routed it to the AI path, the raise (not swallowed
    # here, since the inline path doesn't run through _wa_answer_one's
    # try/except) fails the test loudly.
    def _unexpected_ai_call(message, session_id):
        raise AssertionError(
            f"generate_ai_reply was called for {message!r} — this pricing "
            "question should have resolved through the local deterministic handler."
        )
    monkeypatch.setattr(app, "generate_ai_reply", _unexpected_ai_call)
    monkeypatch.setattr(app, "notify_lead_summary_async", lambda *a, **k: None)
    return TestClient(app.app)


def _post(client, params, signature=None, base=None):
    base = base or app.TWILIO_WA_FALLBACK_HOSTS[0]
    sig = signature if signature is not None else _sign(base, params)
    return client.post(WEBHOOK_PATH, data=params, headers={"X-Twilio-Signature": sig})


def _params(body, sid, sender="whatsapp:+61400777888"):
    return {"From": sender, "To": "whatsapp:+61499000000", "Body": body,
            "MessageSid": sid, "NumMedia": "0"}


def test_real_whatsapp_pricing_reply_has_full_ladder_and_footer(wa_client):
    r = _post(wa_client, _params("What are you prices?", sid="SMfooter1"))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/xml")

    body = _twiml_message_text(r.text)
    assert body, f"expected an inline deterministic TwiML reply, got: {r.text!r}"
    assert "$51/wk" in body
    assert "$175/wk" in body
    assert "youth training program" in body.lower()
    assert MEMBERSHIP_URL in body
    assert body.count(MEMBERSHIP_URL) == 1
    assert len(body) <= app.WA_BODY_LIMIT


# ── genuine positive stubbed-LLM answer through /api/chat (provider mocked
# only — the routing gate is real; this message deliberately carries no
# local-handler trigger so it actually reaches generate_ai_reply) ────────────

def test_stubbed_llm_answer_through_api_chat_is_delivered_unmodified(monkeypatch):
    substantive_stub = (
        "Sure — the coaching here focuses on compound strength work and steady "
        "conditioning, built around whatever you're carrying into each session."
    )
    monkeypatch.setattr(app, "generate_ai_reply", lambda message, session_id: (substantive_stub, "stub"))
    message = "What's your general approach to programming for someone returning to exercise?"
    assert app.should_use_local_tone_handler(message, "footer-api-chat-llm") is False

    r = client.post("/api/chat", json={"message": message, "session_id": "footer-api-chat-llm"})
    assert r.status_code == 200
    body = r.json()["reply"]
    assert "compound strength work" in body  # the stubbed provider's actual fact survives
    assert MEMBERSHIP_URL not in body and TIMETABLE_URL not in body  # no topic match, no footer


def test_stubbed_llm_reply_that_answers_with_a_real_price_gets_footer(monkeypatch):
    # Same proven non-local-routing message, but this time the AI's answer
    # itself states an actual $51/wk price while answering the broader
    # question — no price wording in the USER's message at all. The footer
    # must still attach, via the answer-based (not message-based) topic
    # selection, exactly once.
    substantive_stub = (
        "Sure — the coaching here focuses on compound strength work and steady "
        "conditioning. Most people land on Squad Ascent at $51/wk for unlimited "
        "group classes, built around whatever you're carrying into each session."
    )
    monkeypatch.setattr(app, "generate_ai_reply", lambda message, session_id: (substantive_stub, "stub"))
    message = "What's your general approach to programming for someone returning to exercise?"
    assert app.should_use_local_tone_handler(message, "footer-api-chat-llm-price") is False

    r = client.post("/api/chat", json={"message": message, "session_id": "footer-api-chat-llm-price"})
    assert r.status_code == 200
    payload = r.json()
    assert payload["ai_provider"] == "stub"  # confirms the provider path was actually taken
    body = payload["reply"]
    assert "$51/wk" in body
    assert MEMBERSHIP_URL in body
    assert body.count(MEMBERSHIP_URL) == 1


# ── negative: sensitive and error-fallback replies through /api/chat ──────────

def test_sensitive_injury_price_question_through_api_chat_gets_no_footer():
    r = client.post("/api/chat", json={
        "message": "I tweaked my back at work, roughly what does SPT cost?",
        "session_id": "footer-api-chat-injury",
    })
    assert r.status_code == 200
    body = r.json()["reply"]
    assert MEMBERSHIP_URL not in body
    assert "individual" in body.lower() or "physio" in body.lower() or "practitioner" in body.lower()


def test_ai_backend_error_fallback_gets_no_footer(monkeypatch):
    def _boom(message, session_id):
        raise RuntimeError("simulated provider outage")
    monkeypatch.setattr(app, "generate_ai_reply", _boom)
    monkeypatch.setattr(app, "should_use_outage_fallback", lambda message: False)
    # The parent's full-suite run sets OUTDOOR_SQUAD_ENABLE_DEMO_FALLBACK=1,
    # which would route this exception to demo_fallback_reply instead of the
    # plain error line this test exercises. Force it off for this isolated
    # test only — no production outage-behaviour change.
    monkeypatch.delenv("OUTDOOR_SQUAD_ENABLE_DEMO_FALLBACK", raising=False)
    # A non-vague, non-local-handler message so it actually reaches the
    # try/except around generate_ai_reply in the /api/chat route.
    message = "What's your general approach to programming for someone returning to exercise?"
    assert app.should_use_local_tone_handler(message, "footer-api-chat-error") is False

    r = client.post("/api/chat", json={"message": message, "session_id": "footer-api-chat-error"})
    assert r.status_code == 200
    body = r.json()["reply"]
    assert "trouble reaching the ai backend" in body.lower()
    assert MEMBERSHIP_URL not in body and TIMETABLE_URL not in body
