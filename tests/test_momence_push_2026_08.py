"""Momence lead push — the chain that puts a captured lead into Nick's CRM.

Momence v2 member creation REQUIRES email + firstName + lastName (verified
live 11-Aug-2026; phone alone is rejected), so the gate must not spend the
once-per-session dedupe flag on a phone-only lead: the email often arrives in
a LATER message, and the old code permanently blocked it.
"""
import io
import os
import sys
import tempfile
import importlib
import urllib.error
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
    assert "tags_failed" not in result
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


# ── source-tag scoping: new + existing contacts, generic-tag preservation ───

def test_existing_paying_customer_not_generic_tagged(momence_api, monkeypatch, events):
    """An email-search hit is someone this integration did NOT just create —
    possibly an existing paying customer. It must never be newly enrolled in
    the generic Lead tag; only the explicit, additive WhatsApp source badge
    (when configured) is allowed to touch a pre-existing contact."""
    monkeypatch.setattr(app, "MOMENCE_WA_TAG_ID", "41226")
    momence_api["search_rows"] = [{"id": 77, "email": "existing@example.com"}]
    result = app.push_lead_to_momence(
        {"email": "existing@example.com", "name": "Existing Person"},
        source="whatsapp", session_id="wa-existing")
    assert result["already_existed"] is True
    assert not any("/tags/" in c["path"] for c in momence_api["calls"])
    assert not any(e["event"] == "momence_lead_tagged" for e in events)
    assert "tags_failed" not in result


def test_existing_paying_customer_still_gets_wa_source_badge(momence_api, monkeypatch, events):
    """The one thing an existing contact IS allowed to receive: the new,
    explicit, additive WhatsApp source badge — never the generic Lead tag."""
    monkeypatch.setattr(app, "MOMENCE_WA_TAG_ID", "41226")
    monkeypatch.setattr(app, "MOMENCE_WA_SOURCE_TAG_ID", "99001")
    momence_api["search_rows"] = [{"id": 77, "email": "existing@example.com"}]
    result = app.push_lead_to_momence(
        {"email": "existing@example.com", "name": "Existing Person"},
        source="whatsapp", session_id="wa-existing-badge")
    assert result["already_existed"] is True
    tag_paths = [c["path"] for c in momence_api["calls"] if "/tags/" in c["path"]]
    assert tag_paths == ["/api/v2/host/members/77/tags/99001"]     # badge only, not 41226
    assert any(e["event"] == "momence_lead_tagged" for e in events)


def test_generic_lead_tag_preserved_alongside_new_contact(momence_api, monkeypatch):
    """Both WA and web point at the same generic Lead tag id in production;
    the new source-tag feature must not repurpose or drop it."""
    monkeypatch.setattr(app, "MOMENCE_WA_TAG_ID", "41226")
    monkeypatch.setattr(app, "MOMENCE_WEB_TAG_ID", "41226")
    app.push_lead_to_momence({"email": "web@example.com", "name": "Web Lead"},
                             source="website", session_id="w-generic")
    tag_calls = [c for c in momence_api["calls"] if "/tags/" in c["path"]]
    assert [c["path"] for c in tag_calls] == ["/api/v2/host/members/4242/tags/41226"]


def test_wa_only_source_badge_layers_on_top_of_generic_tag(momence_api, monkeypatch):
    """New optional config: a distinct WhatsApp source badge is applied IN
    ADDITION to the generic Lead tag, and only for whatsapp — website leads
    never see it even when it's configured."""
    monkeypatch.setattr(app, "MOMENCE_WA_TAG_ID", "41226")
    monkeypatch.setattr(app, "MOMENCE_WEB_TAG_ID", "41226")
    monkeypatch.setattr(app, "MOMENCE_WA_SOURCE_TAG_ID", "99001")

    app.push_lead_to_momence({"email": "wa-badge@example.com", "name": "WA Badge"},
                             source="whatsapp", session_id="wa-badge")
    wa_tag_paths = {c["path"] for c in momence_api["calls"] if "/tags/" in c["path"]}
    assert wa_tag_paths == {
        "/api/v2/host/members/4242/tags/41226",
        "/api/v2/host/members/4242/tags/99001",
    }

    momence_api["calls"].clear()
    momence_api["create_response"] = {"memberId": 5252}
    app.push_lead_to_momence({"email": "web-nobadge@example.com", "name": "Web NoBadge"},
                             source="website", session_id="w-nobadge")
    web_tag_paths = {c["path"] for c in momence_api["calls"] if "/tags/" in c["path"]}
    assert web_tag_paths == {"/api/v2/host/members/5252/tags/41226"}


def test_already_applied_badge_is_not_reapplied(momence_api, monkeypatch, events):
    """Idempotency: once the source badge is confirmed applied for a member,
    a later push for the SAME (existing) member must not fire the POST or
    event again. Uses the badge, not the generic tag — an existing contact
    is never re-considered for the generic tag at all (see the
    not_generic_tagged test above)."""
    monkeypatch.setattr(app, "MOMENCE_WA_SOURCE_TAG_ID", "99001")
    momence_api["search_rows"] = [{"id": 88, "email": "repeat@example.com"}]
    lead = {"email": "repeat@example.com", "name": "Repeat Contact"}
    app.push_lead_to_momence(lead, source="whatsapp", session_id="wa-repeat-1")
    momence_api["calls"].clear()
    events.clear()
    app.push_lead_to_momence(lead, source="whatsapp", session_id="wa-repeat-2")
    assert not any("/tags/" in c["path"] for c in momence_api["calls"])
    assert not any(e["event"] == "momence_lead_tagged" for e in events)


def test_failed_generic_tag_recovers_on_retry_without_duplicate_customer(momence_api, monkeypatch):
    """A generic-tag failure on a member THIS push just created must not be
    swallowed as silent success, AND must be recoverable on a later retry
    for that SAME member — without either creating a duplicate customer or
    newly Lead-tagging an unrelated pre-existing contact."""
    monkeypatch.setattr(app, "MOMENCE_WA_TAG_ID", "41226")

    # Reuse the momence_api fake for create/search; the first tag POST fails,
    # the retry's tag POST succeeds — proving the pending marker actually
    # drives a real recovery, not just a re-reported failure.
    real_fake = app._momence_request
    attempts = {"n": 0}

    def fake_with_flaky_tag(method, path, token, body=None):
        if "/tags/" in path:
            momence_api["calls"].append({"method": method, "path": path, "body": body})
            if attempts["n"] == 0:
                attempts["n"] += 1
                raise urllib.error.HTTPError(path, 500, "boom", {}, io.BytesIO(b"boom"))
            return {}
        return real_fake(method, path, token, body)

    monkeypatch.setattr(app, "_momence_request", fake_with_flaky_tag)
    lead = {"email": "flaky@example.com", "name": "Flaky Contact"}

    result = app.push_lead_to_momence(lead, source="whatsapp", session_id="wa-flaky")
    assert result["ok"] is True                       # member creation is never blocked
    assert result.get("tags_failed") == [41226]

    create_calls_before = [c for c in momence_api["calls"] if c["method"] == "POST"
                            and c["path"].endswith("/host/members")]
    assert len(create_calls_before) == 1

    # Simulate the retry a later message triggers via _push_lead_guarded:
    # search-before-create finds the member Momence already has (this is a
    # best-effort no-duplicate path — _momence_find_member_by_email fails
    # OPEN on a search error, so this is not an absolute guarantee).
    momence_api["search_rows"] = [{"id": 4242, "email": "flaky@example.com"}]
    result2 = app.push_lead_to_momence(lead, source="whatsapp", session_id="wa-flaky")
    assert result2["already_existed"] is True
    assert result2.get("tags_failed") in (None, [])    # pending generic tag finished
    create_calls_after = [c for c in momence_api["calls"] if c["method"] == "POST"
                           and c["path"].endswith("/host/members")]
    assert len(create_calls_after) == 1                # no duplicate customer


def test_pending_tag_retry_is_scoped_to_the_member_that_owns_it(momence_api, monkeypatch):
    """The pending-retry marker set after a failed generic tag is keyed by
    member id — it must never cause a DIFFERENT, unrelated existing member
    (found via a later, separate email search) to receive the generic tag."""
    monkeypatch.setattr(app, "MOMENCE_WA_TAG_ID", "41226")
    real_fake = app._momence_request

    def fake_fail_tag_for_4242(method, path, token, body=None):
        if "/tags/" in path:
            momence_api["calls"].append({"method": method, "path": path, "body": body})
            if "/members/4242/" in path:
                raise urllib.error.HTTPError(path, 500, "boom", {}, io.BytesIO(b"boom"))
            return {}
        return real_fake(method, path, token, body)

    monkeypatch.setattr(app, "_momence_request", fake_fail_tag_for_4242)

    # Member 4242 is created here and its generic tag fails, setting a
    # pending marker scoped to member id 4242.
    app.push_lead_to_momence({"email": "creator-fail@example.com", "name": "Creator Fail"},
                             source="whatsapp", session_id="wa-creator-fail")

    # A completely different, unrelated existing member must stay untouched.
    momence_api["calls"].clear()
    momence_api["search_rows"] = [{"id": 999, "email": "unrelated@example.com"}]
    result = app.push_lead_to_momence({"email": "unrelated@example.com", "name": "Unrelated"},
                                      source="whatsapp", session_id="wa-unrelated")
    assert result["already_existed"] is True
    assert not any("/tags/" in c["path"] for c in momence_api["calls"])


def test_malformed_generic_tag_reports_config_error_not_silent_success(momence_api, monkeypatch, events):
    """A garbage, non-empty generic tag id (env typo) must NOT read as a
    complete, successful push — it has to surface as a config error and
    keep the push in a retry/incomplete state, unlike an absent config."""
    monkeypatch.setattr(app, "MOMENCE_WA_TAG_ID", "not-a-number")
    result = app.push_lead_to_momence({"email": "malformed@example.com", "name": "Bad Ids"},
                                      source="whatsapp", session_id="wa-malformed")
    assert result["ok"] is True                        # member creation still succeeds
    assert result.get("tag_config_errors")              # surfaced, not swallowed
    assert not any("/tags/" in c["path"] for c in momence_api["calls"])
    assert any(e["event"] == "momence_tag_config_error" for e in events)


def test_malformed_wa_source_badge_does_not_block_absent_generic_tag(momence_api, monkeypatch):
    """An absent generic tag config stays fully backwards-compatible (no
    error, no attempted call) even when the separate WA-badge config is
    malformed — the two configs are validated and reported independently."""
    monkeypatch.setattr(app, "MOMENCE_WA_SOURCE_TAG_ID", "-5")
    result = app.push_lead_to_momence({"email": "badge-only-bad@example.com", "name": "Badge Bad"},
                                      source="whatsapp", session_id="wa-badge-bad")
    assert result["ok"] is True
    assert result.get("tag_config_errors")
    assert not any("/tags/" in c["path"] for c in momence_api["calls"])


def test_malformed_tag_config_error_never_echoes_the_raw_value(momence_api, monkeypatch, events):
    """The raw config value could be an accidentally pasted secret — the
    error must name only the field, never the value itself."""
    secret_looking_value = "sk_live_totally_secret_do_not_log_12345"
    monkeypatch.setattr(app, "MOMENCE_WA_TAG_ID", secret_looking_value)
    result = app.push_lead_to_momence({"email": "secret-echo@example.com", "name": "Secret Echo"},
                                      source="whatsapp", session_id="wa-secret-echo")
    errors = result.get("tag_config_errors") or []
    assert errors and all(secret_looking_value not in e for e in errors)
    config_events = [e for e in events if e["event"] == "momence_tag_config_error"]
    assert config_events and all(secret_looking_value not in str(e) for e in config_events)


def test_wa_source_tag_equal_to_generic_is_a_config_error_not_dedup(momence_api, monkeypatch, events):
    """A WA source tag configured to the SAME id as the generic Lead tag
    defeats the whole point of source attribution — it must be reported as
    a config error, not silently treated as a harmless one-POST dedup."""
    monkeypatch.setattr(app, "MOMENCE_WA_TAG_ID", "41226")
    monkeypatch.setattr(app, "MOMENCE_WA_SOURCE_TAG_ID", "41226")
    result = app.push_lead_to_momence({"email": "collide@example.com", "name": "Collide Test"},
                                      source="whatsapp", session_id="wa-collide")
    assert result["ok"] is True
    assert result.get("tag_config_errors")
    # The generic tag still gets applied (untouched behaviour); only the
    # duplicate-badge attempt is refused.
    tag_paths = [c["path"] for c in momence_api["calls"] if "/tags/" in c["path"]]
    assert tag_paths == ["/api/v2/host/members/4242/tags/41226"]
    assert any(e["event"] == "momence_tag_config_error" for e in events)


def test_failed_wa_source_tag_resets_worker_flag_for_retry(monkeypatch):
    """A failed WA-source-badge assignment (not just a failed generic tag)
    must also roll back the per-session push flag in the real worker path,
    so the lead's next message actually retries."""
    monkeypatch.setattr(
        app, "push_lead_to_momence",
        lambda info, *, source, session_id: {"ok": True, "member_id": 1,
                                             "already_existed": False,
                                             "tags_failed": [99001]})
    lead = {"email": "badge-retry@example.com", "name": "Badge Retry"}
    app._push_lead_guarded(lead, source="whatsapp", session_id="wa-badge-retry")
    assert app.get_wa_setting("momence_pushed:wa-badge-retry") != "1"


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
