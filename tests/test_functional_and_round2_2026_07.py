"""Round-2 security + functional-QA regression tests (2026-07-02).

Safety/security:
  - eating-disorder guard (detector, reply routing, benign non-matches)
  - private onboarding email excluded from the LLM source corpus
  - merge_lead: no cross-session overwrite of ANY field; same-session refreshes
  - /api/event: forged trial_link_clicked from a no-history session mints no lead
  - output-side prompt-leak guard
  - security response headers

Functional QA (answering-properly):
  A youth pricing carries context ("how much for him?")
  B "what days do you run sessions?" -> full week, not just HiiT'N'Run
  C parking question answered, not treated as a location choice
  D nervous follow-up gets reassurance, not the handoff terminal
  E casual/visiting/drop-in gets the $37 answer
  F name capture survives a leading filler ("I'm keen, I'm Sarah")
"""
import os
import sys
import tempfile
import importlib
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _k in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_KEY",
           "OUTDOOR_SQUAD_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY",
           "OUTDOOR_SQUAD_OPENAI_API_KEY", "OPENAI_API_KEY",
           "OUTDOOR_SQUAD_GEMINI_API_KEY", "GEMINI_API_KEY"):
    os.environ.pop(_k, None)

import app  # noqa: E402
importlib.reload(app)
from fastapi.testclient import TestClient  # noqa: E402

_TMP = Path(tempfile.mkdtemp(prefix="os-func-test-"))
app.LEADS_FILE = _TMP / "leads.json"; app.LEADS_FILE.write_text("[]")
app.EVENTS_FILE = _TMP / "events.jsonl"; app.EVENTS_FILE.write_text("")
app.CONVERSATION_LOG_FILE = _TMP / "conversation_logs.jsonl"; app.CONVERSATION_LOG_FILE.write_text("")
client = TestClient(app.app)


def _reply(msg, sid):
    """Deterministic reply for one message with history preserved in-session."""
    hist = app.load_conversation(sid)
    hist.append({"role": "user", "content": msg})
    out = app.demo_fallback_reply(msg, session_id=sid)
    out = app.prevent_repetitive_reply(out, msg, sid)
    hist.append({"role": "assistant", "content": out})
    return out


# ── eating-disorder guard ─────────────────────────────────────────────────────
def test_ed_detector_catches_disclosures():
    for m in ["I have an eating disorder", "I'm bulimic", "recovering from anorexia",
              "my daughter stopped eating, she's 15", "I make myself sick after meals",
              "I've got body dysmorphia", "I use laxatives to lose weight"]:
        assert app.mentions_eating_disorder(m.lower()), m


def test_ed_detector_ignores_benign_food_talk():
    for m in ["I stopped eating meat, I'm vegetarian", "my kid won't eat his veggies",
              "I want to lose weight and tone up", "I binge watch netflix", "is there a meal plan"]:
        assert not app.mentions_eating_disorder(m.lower()), m


def test_ed_disclosure_gets_caring_handoff_not_pitch():
    for m in ["my daughter is 14 and has an eating disorder, can she train",
              "I'm bulimic and want to tone up",
              "I'm recovering from anorexia, is this safe?"]:
        r = _reply(m, f"ed-{hash(m)}").lower()
        assert "butterfly foundation" in r          # caring resource
        assert "$25/wk" not in r and "$51/wk" not in r and "meal plan" not in r


# ── private onboarding email excluded from LLM corpus ─────────────────────────
def test_internal_email_not_in_source_corpus():
    titles = [d["title"] for d in app.SOURCE_DOCS]
    assert not any("nicholas-email" in t for t in titles)
    # legit content still present
    assert any("lyn-updated-faq" in t for t in titles)


# ── merge_lead cross-session integrity ────────────────────────────────────────
def test_merge_lead_no_cross_session_overwrite():
    existing = {"session_id": "sA", "name": "Alice", "email": "fam@x.com",
                "route": "SPT", "handoff_summary": "Alice knee Redfern"}
    incoming = {"session_id": "sB", "name": "Bob", "email": "fam@x.com",
                "route": "YTP", "handoff_summary": "Bob kids Camperdown"}
    m = app.merge_lead(existing, incoming)
    assert m["name"] == "Alice" and m["route"] == "SPT" and "Alice" in m["handoff_summary"]


def test_merge_lead_same_session_refreshes_operational():
    existing = {"session_id": "sA", "route": "SPT"}
    incoming = {"session_id": "sA", "route": "YTP", "handoff_summary": "updated"}
    m = app.merge_lead(existing, incoming)
    assert m["route"] == "YTP" and m["handoff_summary"] == "updated"


# ── /api/event forged trial click mints no lead ───────────────────────────────
def test_forged_trial_click_no_history_mints_no_lead():
    app.LEADS_FILE.write_text("[]")
    r = client.post("/api/event", json={"event_type": "trial_link_clicked",
                                        "session_id": "no-history-forger",
                                        "metadata": {"url": "https://momence.com/x"}})
    assert r.status_code == 200
    import json
    assert json.loads(app.LEADS_FILE.read_text()) == []


# ── output-side prompt-leak guard ─────────────────────────────────────────────
def test_prompt_leak_guard_blocks_regurgitated_prompt():
    out = app.clean_agent_reply("Sure! Required brand voice reference:\n- Sound like Nick")
    assert "behind the curtain" in out.lower()
    # normal replies pass through untouched
    assert "curtain" not in app.clean_agent_reply("Squad Ascent is $51/wk.").lower()


# ── security headers ──────────────────────────────────────────────────────────
def test_security_headers_present():
    h = client.get("/api/health").headers
    assert h.get("x-content-type-options") == "nosniff"
    assert h.get("referrer-policy")


# ── A: youth pricing context ──────────────────────────────────────────────────
def test_youth_price_carries_context():
    sid = "func-A"
    _reply("do you do anything for kids? my son's 13", sid)
    r = _reply("how much is it for him?", sid)
    assert "$25/wk" in r and "$51/wk" not in r


def test_adult_price_unaffected():
    r = _reply("how much does it cost?", "func-A2")
    assert "$51/wk" in r and "$25/wk per kid" not in r


# ── B: timetable "run" collision ──────────────────────────────────────────────
def test_run_sessions_gives_full_week():
    r = app.timetable_reply(app.normalise_chat_text("what days do you run sessions"), "func-B")
    low = r.lower()
    assert "mornings" in low and "saturday" in low  # full-week summary


def test_hiit_question_still_filters():
    r = app.timetable_reply(app.normalise_chat_text("when is hiit on"), "func-B2")
    assert "hiit'n'run" in r.lower()


# ── C: parking answered ───────────────────────────────────────────────────────
def test_parking_question_answered():
    sid = "func-C"
    _reply("where are you based?", sid)
    r = _reply("is there parking at Camperdown?", sid)
    assert "australia st" in r.lower()


# ── D: nervous follow-up reassurance ──────────────────────────────────────────
def test_nervous_followup_reassured():
    sid = "func-D"
    _reply("honestly I'm super unfit and nervous about being judged", sid)
    r = _reply("will there be really fit people making me feel bad?", sid).lower()
    assert "outside what robo-nick can reliably do" not in r
    assert "judge" in r or "mixed crew" in r or "warm-up" in r


# ── E: casual / visiting / drop-in ────────────────────────────────────────────
def test_casual_dropin_answered():
    r = _reply("I'm visiting Sydney for 2 weeks, can I just drop in to a few classes?", "func-E")
    assert "$37" in r and "membership" in r.lower()


# ── anti-invention: session length + equipment (LLM tail) ─────────────────────
def test_session_length_not_invented():
    for q in ["how long does each class go for?", "how long are the sessions?", "session length?"]:
        r = _reply(q, f"len-{hash(q)}")
        assert "45" not in r and "60 min" not in r  # no invented duration
        assert "timetable" in r.lower() or "team can confirm" in r.lower()


def test_equipment_answer_grounded():
    for q in ["what equipment do you provide?", "do I need to bring my own gear?"]:
        r = _reply(q, f"eq-{hash(q)}").lower()
        assert "provided" in r and ("drink bottle" in r or "towel" in r)
        assert "trap bar" not in r and "barbell" not in r and "trx" not in r  # no invented inventory


# ── F: name capture past a leading filler ─────────────────────────────────────
def test_name_capture_past_leading_filler():
    assert app.extract_contact_name("I'm keen, I'm Sarah, 0412 345 678") == "Sarah"
    assert app.extract_contact_name("my name is Jacobo, 0412 345 678") == "Jacobo"
    assert app.extract_contact_name("I'm flat out, a@b.com") is None
    assert app.extract_contact_name("I'm recovering from anorexia, a@b.com") is None
