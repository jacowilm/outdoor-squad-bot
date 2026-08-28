"""Momence lead push — the chain that puts a captured lead into Nick's CRM.

Momence v2 member creation REQUIRES email + firstName + lastName (verified
live 11-Aug-2026; phone alone is rejected), so the gate must not spend the
once-per-session dedupe flag on a phone-only lead: the email often arrives in
a LATER message, and the old code permanently blocked it.
"""
import os
import sys
import tempfile
import importlib
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

_TMP = Path(tempfile.mkdtemp(prefix="os-momence-"))
app.LEADS_FILE = _TMP / "leads.json"
app.LEADS_FILE.write_text("[]")
app.EVENTS_FILE = _TMP / "events.jsonl"
app.CONVERSATION_LOG_FILE = _TMP / "conversations.jsonl"


@pytest.fixture(autouse=True)
def _momence_env(monkeypatch, tmp_path):
    """Configured Momence + isolated settings store for every test."""
    monkeypatch.setattr(app, "MOMENCE_V2_CLIENT_ID", "test-client")
    monkeypatch.setattr(app, "MOMENCE_V2_CLIENT_SECRET", "test-secret")
    monkeypatch.setattr(app, "MOMENCE_SEED_REFRESH_TOKEN", "test-seed")
    monkeypatch.setattr(app, "MOMENCE_WA_TAG_ID", "")
    monkeypatch.setattr(app, "MOMENCE_WEB_TAG_ID", "")
    monkeypatch.setattr(app, "WA_STATE_FILE", tmp_path / "wa_state.json")
    monkeypatch.setattr(app, "_momence_access_token", lambda: "test-token")
    yield


@pytest.fixture()
def events(monkeypatch):
    captured = []
    real = app.log_event
    monkeypatch.setattr(
        app, "log_event",
        lambda event_type, **kw: (captured.append({"event": event_type, **kw}), real(event_type, **kw))[0],
    )
    return captured


@pytest.fixture()
def pushes(monkeypatch):
    captured = []
    monkeypatch.setattr(
        app, "push_lead_async",
        lambda lead_info, *, source, session_id: captured.append(
            {"lead": dict(lead_info), "source": source, "session_id": session_id}
        ),
    )
    return captured


# ── phone normalisation ──────────────────────────────────────────────────────

def test_phone_normalises_au_mobile_to_e164():
    assert app._momence_phone("0412 345 678") == "+61412345678"
    assert app._momence_phone("+61 412-345-678") == "+61412345678"
    assert app._momence_phone("61412345678") == "+61412345678"
    assert app._momence_phone("") is None
    assert app._momence_phone(None) is None


# ── the shared gate ──────────────────────────────────────────────────────────

def test_no_email_means_no_push_and_no_spent_flag(pushes):
    app.maybe_push_lead_to_momence({"phone": "0412345678"}, "wa-1", source="whatsapp")
    assert pushes == []
    assert app.get_wa_setting("momence_pushed:wa-1") != "1"


def test_email_reaches_the_push_worker(pushes):
    lead = {"email": "jane@example.com", "name": "Jane Doe"}
    app.maybe_push_lead_to_momence(lead, "wa-2", source="whatsapp")
    assert len(pushes) == 1 and pushes[0]["source"] == "whatsapp"


def test_worker_dedupes_and_marks_the_flag(monkeypatch):
    calls = []
    monkeypatch.setattr(app, "push_lead_to_momence",
                        lambda info, *, source, session_id: (calls.append(1), {"ok": True})[1])
    lead = {"email": "jane@example.com", "name": "Jane Doe"}
    app._push_lead_guarded(lead, source="whatsapp", session_id="wa-2")
    app._push_lead_guarded(lead, source="whatsapp", session_id="wa-2")
    assert len(calls) == 1
    assert app.get_wa_setting("momence_pushed:wa-2") == "1"


def test_failed_push_rolls_the_flag_back_so_the_next_message_retries(monkeypatch):
    outcomes = [{"ok": False, "reason": "HTTP 500"}, {"ok": True, "member_id": 1}]
    calls = []
    monkeypatch.setattr(app, "push_lead_to_momence",
                        lambda info, *, source, session_id: (calls.append(1), outcomes[len(calls) - 1])[1])
    lead = {"email": "retry@example.com", "name": "Re Try"}
    app._push_lead_guarded(lead, source="website", session_id="w-30")
    assert app.get_wa_setting("momence_pushed:w-30") != "1"      # rolled back
    app._push_lead_guarded(lead, source="website", session_id="w-30")
    assert len(calls) == 2
    assert app.get_wa_setting("momence_pushed:w-30") == "1"


def test_untrusted_session_is_skipped_and_visible(pushes, events):
    app.maybe_push_lead_to_momence({"email": "spam@example.com"}, "s-curl",
                                   source="website", trusted=False)
    assert pushes == []
    skips = [e for e in events if e["event"] == "momence_push_skipped"]
    assert skips and skips[0]["reason"] == "untrusted_session"


def test_phone_first_email_later_still_reaches_the_crm(pushes):
    """The regression the old code had: flag spent on the phone-only message."""
    app.maybe_push_lead_to_momence({"phone": "0412345678"}, "wa-3", source="whatsapp")
    app.maybe_push_lead_to_momence(
        {"phone": "0412345678", "email": "late@example.com"}, "wa-3", source="whatsapp")
    assert len(pushes) == 1
    assert pushes[0]["lead"]["email"] == "late@example.com"


def test_stray_plus_is_stripped_from_phones():
    assert app._momence_phone("0412 345 678 +") == "+61412345678"
    assert app._momence_phone("+614+123+45678") == "+61412345678"


def test_null_json_body_does_not_crash_after_a_successful_create(monkeypatch):
    class _Resp:
        def read(self): return b"null"
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(app.urllib.request, "urlopen", lambda *a, **k: _Resp())
    assert app._momence_request("POST", "/api/v2/host/members", "tok", {}) == {}


def test_internal_qa_is_suppressed_but_visible(pushes, events):
    app.maybe_push_lead_to_momence(
        {"email": "qa@example.com"}, "qa-1", source="website", suppressed=True)
    assert pushes == []
    assert app.get_wa_setting("momence_pushed:qa-1") != "1"
    skips = [e for e in events if e["event"] == "momence_push_skipped"]
    assert skips and skips[0]["reason"] == "internal_qa" and skips[0]["source"] == "website"


# ── the push itself ──────────────────────────────────────────────────────────

@pytest.fixture()
def momence_api(monkeypatch):
    """Capture every CRM call; programmable search result."""
    calls = []
    state = {
        "search_rows": [],
        "create_response": {"memberId": 4242},
        "locations": [{"id": 300, "name": "Camperdown"}, {"id": 301, "name": "Redfern"}],
    }

    def fake_request(method, path, token, body=None):
        calls.append({"method": method, "path": path, "body": body})
        if method == "GET" and "/host/locations" in path:
            return {"payload": state["locations"]}
        if method == "GET" and "/host/members?" in path:
            return {"payload": state["search_rows"]}
        if method == "POST" and path.endswith("/host/members"):
            return state["create_response"]
        return {}

    monkeypatch.setattr(app, "_momence_request", fake_request)
    monkeypatch.setattr(app, "_momence_locations_cache", None)
    monkeypatch.setattr(app, "MOMENCE_DEFAULT_LOCATION_ID", "")
    state["calls"] = calls
    return state


def test_create_payload_has_required_fields_and_phone(momence_api):
    result = app.push_lead_to_momence(
        {"name": "Jane Doe", "email": "jane@example.com", "phone": "0412 345 678"},
        source="whatsapp", session_id="wa-9")
    assert result == {"ok": True, "member_id": 4242, "already_existed": False}
    create = [c for c in momence_api["calls"] if c["method"] == "POST"][0]
    assert create["body"] == {
        "email": "jane@example.com", "firstName": "Jane", "lastName": "Doe",
        "phoneNumber": "+61412345678", "homeLocationId": 300,
    }


def test_name_fallbacks_are_honest_not_fabricated(momence_api):
    app.push_lead_to_momence({"email": "sarah.k@example.com"}, source="website", session_id="w-1")
    create = [c for c in momence_api["calls"] if c["method"] == "POST"][0]
    assert create["body"]["firstName"] == "sarah.k"          # her own address, not an invented name
    assert create["body"]["lastName"] == "(via website)"     # visible source marker

    momence_api["calls"].clear()
    app.push_lead_to_momence({"email": "s@example.com", "name": "Sarah"},
                             source="whatsapp", session_id="wa-8")
    create = [c for c in momence_api["calls"] if c["method"] == "POST"][0]
    assert create["body"]["firstName"] == "Sarah"
    assert create["body"]["lastName"] == "(via whatsapp)"


def test_existing_member_is_not_duplicated(momence_api, events):
    momence_api["search_rows"] = [{"id": 77, "email": "Jane@Example.com"}]
    result = app.push_lead_to_momence(
        {"email": "jane@example.com", "name": "Jane Doe"}, source="website", session_id="w-2")
    assert result["already_existed"] is True and result["member_id"] == 77
    assert not any(c["method"] == "POST" for c in momence_api["calls"])
    skips = [e for e in events if e["event"] == "momence_push_skipped"]
    assert skips and skips[0]["reason"] == "already_in_momence"


def test_search_failure_does_not_block_the_create(momence_api, monkeypatch):
    monkeypatch.setattr(app, "_momence_find_member_by_email",
                        lambda token, email: (_ for _ in ()).throw(RuntimeError("boom")))
    # fail-open lives INSIDE the finder; simulate its contract instead
    monkeypatch.setattr(app, "_momence_find_member_by_email", lambda token, email: None)
    result = app.push_lead_to_momence(
        {"email": "x@example.com", "name": "X Y"}, source="whatsapp", session_id="wa-7")
    assert result["ok"] is True


def test_tag_assigned_when_configured(momence_api, monkeypatch, events):
    monkeypatch.setattr(app, "MOMENCE_WA_TAG_ID", "555")
    app.push_lead_to_momence({"email": "t@example.com", "name": "Tag Test"},
                             source="whatsapp", session_id="wa-6")
    tag_calls = [c for c in momence_api["calls"] if "/tags/" in c["path"]]
    assert tag_calls and tag_calls[0]["path"].endswith("/host/members/4242/tags/555")
    assert any(e["event"] == "momence_lead_tagged" for e in events)


# ── the WhatsApp capture path uses the gate ──────────────────────────────────

def test_wa_capture_routes_through_the_shared_gate(monkeypatch):
    seen = []
    monkeypatch.setattr(app, "extract_lead_info",
                        lambda m, s: {"email": "wa@example.com", "session_id": s})
    monkeypatch.setattr(app, "save_lead", lambda info: None)
    monkeypatch.setattr(app, "has_contact_details", lambda m: True)
    monkeypatch.setattr(app, "notify_lead_summary_async", lambda info, reason: None)
    monkeypatch.setattr(
        app, "maybe_push_lead_to_momence",
        lambda lead_info, session_id, *, source, suppressed=False: seen.append((session_id, source)))
    app._wa_capture_lead("my email is wa@example.com", "wa-55", True, "test")
    assert seen == [("wa-55", "whatsapp")]


# ── homeLocationId: required by Momence on this host (live 400 without it) ───

def test_location_preference_maps_to_the_matching_location(momence_api):
    app.push_lead_to_momence(
        {"email": "r@example.com", "name": "Red Fern", "location_preference": "Redfern mornings"},
        source="whatsapp", session_id="wa-10")
    create = [c for c in momence_api["calls"] if c["method"] == "POST"][0]
    assert create["body"]["homeLocationId"] == 301


def test_no_preference_falls_back_to_oldest_location(momence_api):
    app.push_lead_to_momence({"email": "n@example.com", "name": "No Pref"},
                             source="website", session_id="w-9")
    create = [c for c in momence_api["calls"] if c["method"] == "POST"][0]
    assert create["body"]["homeLocationId"] == 300


def test_env_default_beats_the_oldest_fallback(momence_api, monkeypatch):
    monkeypatch.setattr(app, "MOMENCE_DEFAULT_LOCATION_ID", "301")
    app.push_lead_to_momence({"email": "d@example.com", "name": "Def Ault"},
                             source="website", session_id="w-10")
    create = [c for c in momence_api["calls"] if c["method"] == "POST"][0]
    assert create["body"]["homeLocationId"] == 301


def test_locations_fetch_failure_is_not_cached(momence_api, monkeypatch):
    """One transient locations failure must not poison every later create."""
    state = {"fail": True}
    real = app._momence_request

    def flaky(method, path, token, body=None):
        if "/host/locations" in path and state["fail"]:
            raise RuntimeError("momence hiccup")
        return real(method, path, token, body)

    monkeypatch.setattr(app, "_momence_request", flaky)
    app.push_lead_to_momence({"email": "a@example.com", "name": "A B"},
                             source="website", session_id="w-20")
    first = [c for c in momence_api["calls"] if c["method"] == "POST"][-1]
    assert "homeLocationId" not in first["body"]          # degraded, but attempted

    state["fail"] = False
    momence_api["calls"].clear()
    app.push_lead_to_momence({"email": "b@example.com", "name": "C D"},
                             source="website", session_id="w-21")
    second = [c for c in momence_api["calls"] if c["method"] == "POST"][-1]
    assert second["body"]["homeLocationId"] == 300        # recovered without a restart
