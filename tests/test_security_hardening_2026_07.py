"""Regression tests for the 2026-07-02 security-hardening round.

Covers six fixes found by the multi-agent audit + independent probing:
  A) ReDoS in guard_operational_claims / clean_agent_reply (bounded, linear)
  B) session_id minting so a blank/omitted id can't share the "default" bucket
  C) /api/event event-type whitelist (public callers can't forge outcome metrics)
  D) broadened redact_contact (international/dotted/spaced visitor phones)
  E) extract_contact_name rejecting scheduling/filler words as names
  F) merge_lead never overwriting a name across a cross-session contact match
"""
import os
import sys
import time
import tempfile
import importlib
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Force the local-file storage path (no Supabase) for deterministic behaviour,
# and disable every AI provider so tests never make (slow, billable) real calls.
for _k in (
    "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_KEY",
    "OUTDOOR_SQUAD_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY",
    "OUTDOOR_SQUAD_OPENAI_API_KEY", "OPENAI_API_KEY",
    "OUTDOOR_SQUAD_GEMINI_API_KEY", "GEMINI_API_KEY",
):
    os.environ.pop(_k, None)

import app  # noqa: E402
importlib.reload(app)
from fastapi.testclient import TestClient  # noqa: E402

# Redirect ALL local data files to a throwaway temp dir so the suite never
# pollutes the working copy's real leads/events/logs (which back the local
# /admin view). Prod uses Supabase, so this only matters for local runs.
_TMP = Path(tempfile.mkdtemp(prefix="os-sec-test-"))
app.LEADS_FILE = _TMP / "leads.json"
app.LEADS_FILE.write_text("[]")
app.EVENTS_FILE = _TMP / "events.jsonl"
app.EVENTS_FILE.write_text("")
app.CONVERSATION_LOG_FILE = _TMP / "conversation_logs.jsonl"
app.CONVERSATION_LOG_FILE.write_text("")

client = TestClient(app.app)


# ── A) ReDoS ────────────────────────────────────────────────────────────────
def test_guard_operational_claims_is_bounded_and_fast():
    # Warm up, then feed a pathological punctuation-free price string.
    for _ in range(2):
        app.clean_agent_reply("warm")
    bait = "pricing " + "x " * 4000  # ~8000 chars
    start = time.perf_counter()
    app.clean_agent_reply(bait)
    elapsed_ms = (time.perf_counter() - start) * 1000
    # Pre-fix this was 4+ seconds; the bounded regexes + length cap keep it well
    # under a second even on the worst case. Generous ceiling to avoid flakiness.
    assert elapsed_ms < 900, f"clean_agent_reply too slow: {elapsed_ms:.0f}ms"


def test_guard_operational_claims_scaling_is_linear():
    for _ in range(5):
        app.guard_operational_claims("warm " * 20)

    def t(n):
        s = "cost " + "x " * (n // 2)
        # Best-of-N: minimise scheduler/load noise so the ratio check is stable
        # even when the whole suite runs in parallel on a loaded machine.
        best = float("inf")
        for _ in range(7):
            start = time.perf_counter()
            app.guard_operational_claims(s)
            best = min(best, (time.perf_counter() - start) * 1000)
        return best

    small = t(2000)
    big = t(8000)
    # A 4x length increase: linear ~4x, quadratic ~16x. The pre-fix code was
    # ~16x; the bounded regexes keep it ~4x. Generous ceiling to avoid flakiness.
    assert big < small * 10 + 5, f"super-linear scaling: {small:.1f}ms -> {big:.1f}ms"


def test_guard_still_scrubs_price_flexibility_language():
    out = app.guard_operational_claims("Our pricing is flexible depending on what you need.")
    assert "flexible" not in out.lower()
    assert "different membership levels" in out.lower()


def test_guard_leaves_non_price_flexibility_intact():
    # "programming is flexible" must survive — it is not a price claim.
    src = "The programming is flexible and we can work something out for your shoulder."
    assert app.guard_operational_claims(src) == src


# ── B) session_id minting ─────────────────────────────────────────────────────
def test_missing_session_id_mints_fresh_unique_id():
    r1 = client.post("/api/chat", json={"message": "hi there"})
    r2 = client.post("/api/chat", json={"message": "hi there"})
    s1 = r1.json()["session_id"]
    s2 = r2.json()["session_id"]
    assert s1 != "default" and s2 != "default"
    assert s1 != s2
    assert s1.startswith("s-")


def test_blank_session_id_mints_fresh_id():
    for blank in ["", "   ", None]:
        payload = {"message": "hello"}
        if blank is not None:
            payload["session_id"] = blank
        sid = client.post("/api/chat", json=payload).json()["session_id"]
        assert sid != "default"
        assert sid.startswith("s-")


def test_explicit_session_id_is_preserved():
    sid = client.post("/api/chat", json={"message": "hi", "session_id": "widget-abc123"}).json()["session_id"]
    assert sid == "widget-abc123"


# ── C) /api/event whitelist ───────────────────────────────────────────────────
def test_forged_outcome_event_does_not_poison_metrics():
    before = app.build_metrics_payload()["outcomes"]["lead_captured"]
    for i in range(5):
        client.post("/api/event", json={"event_type": "lead_captured", "session_id": f"forge-{i}"})
    after = app.build_metrics_payload()["outcomes"]["lead_captured"]
    assert after == before, "public /api/event was able to inflate lead_captured"


def test_forged_outcome_event_relabelled_generic():
    # Uses a delta: build_metrics_payload() reads the real events store, which may
    # already contain legitimate server-generated outcome events from prior usage.
    before = app.build_metrics_payload()["outcomes"]["human_handoff_suggested"]
    r = client.post("/api/event", json={"event_type": "human_handoff_suggested", "session_id": "forge-x"})
    assert r.status_code == 200
    after = app.build_metrics_payload()["outcomes"]["human_handoff_suggested"]
    assert after == before, "public /api/event was able to inject a human_handoff_suggested outcome"


def test_legit_widget_event_still_accepted():
    r = client.post("/api/event", json={"event_type": "widget_opened", "session_id": "w1"})
    assert r.status_code == 200 and r.json().get("ok") is True


# ── D) broadened redaction ────────────────────────────────────────────────────
def test_redacts_international_and_obfuscated_phones():
    for msg in ["call me +44 7911 123456", "mob 0412.345.678", "reach me +1 415 555 0132", "text 0412 345 678"]:
        assert "[phone]" in app.redact_contact(msg), msg


def test_redaction_keeps_business_phone_and_non_phone_numbers():
    keep = [
        "call the team on 0402 439 361",          # business phone
        "The 28-Day Kickstarter is $397 total.",   # price
        "Redfern NSW 2016, Camperdown NSW 2050.",  # postcodes
        "Buses 413, 440, 480 stop nearby.",        # bus numbers
        "Sat 9:15am, Tue 6:30pm.",                 # times
    ]
    for msg in keep:
        assert "[phone]" not in app.redact_contact(msg), msg


# ── E) name-extraction filler rejection ───────────────────────────────────────
def test_scheduling_and_filler_words_not_captured_as_names():
    for msg in [
        "call me tomorrow on 0412 345 678",
        "this is ridiculous, contact me at bob@example.com",
        "i'm working on my fitness, reach me at a@b.com",
    ]:
        assert app.extract_contact_name(msg) is None, msg


def test_real_names_still_captured():
    assert app.extract_contact_name("my name is Jacobo, phone 0412 345 678") == "Jacobo"
    assert app.extract_contact_name("Hi, I'm David and my email is d@x.com") == "David"


# ── F) merge_lead name non-destructive ────────────────────────────────────────
def test_merge_lead_does_not_overwrite_name_across_sessions():
    existing = {"session_id": "aaa", "name": "Alice", "email": "family@example.com"}
    incoming = {"session_id": "bbb", "name": "Bob", "email": "family@example.com"}
    merged = app.merge_lead(existing, incoming)
    assert merged["name"] == "Alice", "second visitor's name clobbered the first"


def test_merge_lead_fills_missing_name():
    existing = {"session_id": "aaa", "email": "x@example.com"}
    incoming = {"session_id": "aaa", "name": "Charlie", "email": "x@example.com"}
    merged = app.merge_lead(existing, incoming)
    assert merged["name"] == "Charlie"
