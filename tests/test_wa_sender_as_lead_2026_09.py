"""Diff item #3 (with the #18/#19 residue): on WhatsApp the sender's number IS
the lead's phone, the bot asks for a first name only, and Twilio's ProfileName
is kept as an unverified display name.

Numbers refer to outbox/WEB-VS-WHATSAPP-DIFF-2026-09-11.md.
"""
import ast
import base64
import hashlib
import hmac
import importlib
import io
import json
import os
import sys
import tokenize

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
SENDER = "whatsapp:+61452006342"
SENDER_E164 = "+61452006342"


@pytest.fixture()
def wa(monkeypatch, tmp_path):
    """Real gate, real scripted replies, real alert dedupe, no network."""
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
    monkeypatch.setattr(app, "_wa_async_dispatch", lambda target, args: target(*args))
    monkeypatch.setattr(app, "is_rate_limited", lambda *a, **k: False)

    ai = {"reply": "Happy to help with that one.", "calls": []}

    def _generate(message, session_id):
        ai["calls"].append((message, session_id))
        reply = ai["reply"]
        return (reply(message, session_id) if callable(reply) else reply), "test"

    monkeypatch.setattr(app, "generate_ai_reply", _generate)

    # Owner alerts run the REAL notify_lead_summary (so the once-per-fingerprint
    # dedupe is exercised) with every transport stubbed out.
    alerts = []
    monkeypatch.setattr(app, "lead_summary_delivery_configured", lambda: True)
    monkeypatch.setattr(app, "send_lead_summary_email", lambda li: True)
    monkeypatch.setattr(app, "send_lead_summary_phone",
                        lambda li: bool(alerts.append(dict(li))) or True)
    monkeypatch.setattr(app, "notify_lead_summary_async",
                        lambda li, **kw: app.notify_lead_summary(li, **kw))

    pushes = []
    monkeypatch.setattr(app, "maybe_push_lead_to_momence",
                        lambda li, sid, **kw: pushes.append((dict(li), sid)))

    sent = []
    monkeypatch.setattr(app, "send_whatsapp_via_twilio",
                        lambda to, body: (sent.append((to, body)), (True, "SMfake"))[1])
    app.conversations.clear()
    app._twilio_wa_seen_sids.clear()
    app._wa_setting_cache.clear()
    client = TestClient(app.app)
    client.sent = sent
    client.alerts = alerts
    client.pushes = pushes
    client.ai = ai
    return client


def _sign(params):
    base = app.TWILIO_WA_FALLBACK_HOSTS[0]
    payload = base + WEBHOOK_PATH + "".join(k + str(params[k]) for k in sorted(params))
    return base64.b64encode(hmac.new(TEST_TOKEN.encode(), payload.encode(), hashlib.sha1).digest()).decode()


_n = [0]


def post(client, body, sender=SENDER, profile_name=None, wa_id=None):
    _n[0] += 1
    params = {"From": sender, "To": "whatsapp:+61499000000", "Body": body,
              "MessageSid": f"SMlead{_n[0]:05d}", "NumMedia": "0"}
    if profile_name is not None:
        params["ProfileName"] = profile_name
    if wa_id is not None:
        params["WaId"] = wa_id
    return client.post(WEBHOOK_PATH, data=params, headers={"X-Twilio-Signature": _sign(params)})


def sid_for(sender=SENDER):
    return "wa-" + "".join(ch for ch in sender if ch.isdigit())


def leads(client=None):
    return json.loads(app.LEADS_FILE.read_text())


def events(name=None):
    rows = [json.loads(line) for line in app.EVENTS_FILE.read_text().splitlines() if line.strip()]
    return [r for r in rows if name is None or r.get("event_type") == name]


def last_out(client):
    return client.sent[-1][1] if client.sent else None


# ---- (1) the rewrite table never leaves "mobile" in a scripted reply --------

# Non-copy literals: a gate phrase list, a routing trigger, the fast-path guard
# inside wa_rewrite_contact_asks, and the WhatsApp system-prompt clause (which
# is an instruction to the model, never a message to a customer).
_MOBILE_LITERAL_ALLOWLIST = {
    "The person is messaging from their own mobile, so NEVER ask for their mobile or phone number; ",
    "mobile",
    "name and mobile",
    "mobile number",
    "if i leave my mobile",
}


def _scripted_mobile_literals():
    """Every single-line double-quoted literal in app.py that says "mobile".

    Harvested from the source rather than hand-listed: a hand-typed list is
    exactly how six of these lines survived the first pass at this fix.
    Triple-quoted blocks (the agent prompt, docstrings, the HTML templates)
    and raw regex strings are skipped, plus the pinned allowlist above.
    """
    found = []
    with open(app.__file__.replace(".pyc", ".py"), "rb") as handle:
        for token in tokenize.tokenize(handle.readline):
            if token.type != tokenize.STRING:
                continue
            prefix = token.string[:len(token.string) - len(token.string.lstrip("rRbBuUfF"))]
            body = token.string[len(prefix):]
            if "r" in prefix.lower() or not body.startswith('"') or body.startswith('"""'):
                continue
            try:
                value = ast.literal_eval(token.string)
            except (ValueError, SyntaxError):
                continue
            if isinstance(value, str) and "mobile" in value.lower() and value not in _MOBILE_LITERAL_ALLOWLIST:
                found.append((token.start[0], value))
    return found


def test_every_scripted_mobile_ask_becomes_a_first_name_ask():
    harvested = _scripted_mobile_literals()
    assert len(harvested) > 20, "harvester stopped seeing the scripted copy"
    for line_no, literal in harvested:
        rendered = app.render_for_whatsapp(literal)
        assert "mobile" not in rendered.lower(), f"app.py:{line_no} still asks for a mobile: {rendered}"
        assert set(app._WA_EMAIL_RE.findall(literal)) == set(app._WA_EMAIL_RE.findall(rendered)), \
            f"app.py:{line_no} lost an email address"
        for url in ["http://", "https://"]:
            assert literal.count(url) == rendered.count(url), f"app.py:{line_no} lost a link"
        assert app.render_for_whatsapp(rendered) == rendered, f"app.py:{line_no} is not idempotent"


def test_uncertain_terminal_on_whatsapp_asks_for_a_name_only(wa):
    # The uncertain terminal, verbatim from app.py, as an AI-path draft.
    wa.ai["reply"] = (
        "Honest answer: that one's outside what Robo-Nick can reliably do.\n\n"
        "Drop your first name + mobile and he'll sort it properly, or email "
        "innerwest@outdoorsquad.com.au."
    )
    response = post(wa, "do you run sessions on the moon?")
    body = last_out(wa)
    assert body and "mobile" not in body.lower()
    assert "innerwest@outdoorsquad.com.au" in body
    assert "first name" in body.lower()
    stored = [m for m in app.load_conversation(sid_for()) if m["role"] == "assistant"]
    assert stored[-1]["content"] == body
    assert response.status_code == 200


def test_scripted_terminal_stores_the_rendered_text(wa):
    # The uncertain terminal is a scripted reply, answered inside the request.
    post(wa, "where do you meet at camperdown?")
    stored = [m for m in app.load_conversation(sid_for()) if m["role"] == "assistant"]
    assert stored, "no scripted reply was stored"
    assert "mobile" not in stored[-1]["content"].lower()
    assert "+ mobile" not in stored[-1]["content"]


# ---- (2) a bare first name is a lead, with the sender as the phone ---------

def test_bare_first_name_after_the_ask_becomes_a_lead_with_the_sender_number(wa):
    wa.ai["reply"] = "Happy to help. What's your first name so I can flag it?"
    post(wa, "do you run sessions on the moon?")
    assert not leads()

    post(wa, "Sarah")
    rows = leads()
    assert len(rows) == 1
    row = rows[0]
    assert row["phone"] == SENDER_E164
    assert row["name"] == "Sarah"
    assert row["channel"] == "whatsapp"
    assert "wa_profile_name" not in row
    assert len(wa.alerts) == 1
    assert wa.alerts[0]["channel"] == "whatsapp" and wa.alerts[0]["phone"] == SENDER_E164
    assert len(events("lead_captured")) == 1

    # Once the name is known the bot stops asking, and the answer survives.
    wa.ai["reply"] = "Sessions run at 6am and 9.30am. What's your first name?"
    post(wa, "tell me about your programs")
    body = last_out(wa)
    assert "first name" not in body.lower()
    assert "6am" in body


def test_the_prompt_is_told_the_name_and_never_the_profile_name(wa):
    post(wa, "hello there", profile_name="Definitely Not Sarah")
    sid = sid_for()
    app.conversations[sid] = [
        {"role": "assistant", "content": "What's your first name?"},
        {"role": "user", "content": "Sarah"},
    ]
    prompt = app.whatsapp_channel_prompt(sid)
    assert "first name is Sarah" in prompt
    assert "Definitely Not Sarah" not in prompt


# ---- (3) a bare "hi", a suburb or a time word is never a lead -------------

@pytest.mark.parametrize("ask_first,message,sender", [
    (False, "hi", "whatsapp:+61452000001"),
    (False, "how much?", "whatsapp:+61452000002"),
    (False, "yes", "whatsapp:+61452000003"),
    (True, "it's my first time", "whatsapp:+61452000004"),
    (True, "Bondi", "whatsapp:+61452000005"),
    (True, "Newtown", "whatsapp:+61452000006"),
    (True, "Mornings", "whatsapp:+61452000007"),
    (False, "I'm Newtown based", "whatsapp:+61452000008"),
])
def test_non_names_never_create_a_lead(wa, ask_first, message, sender):
    if ask_first:
        app.conversations[sid_for(sender)] = [
            {"role": "assistant", "content": "No worries. What's your first name?"},
        ]
    post(wa, message, sender=sender)
    assert leads() == []
    assert wa.alerts == []


# ---- (4) typed details still work, and a second number is kept ------------

def test_typed_email_keeps_the_sender_as_the_phone_and_dedupes_the_alert(wa):
    post(wa, "my email is sarah@example.com")
    rows = leads()
    assert len(rows) == 1
    assert rows[0]["phone"] == SENDER_E164
    assert rows[0]["email"] == "sarah@example.com"
    assert len(wa.pushes) == 1
    assert len(wa.alerts) == 1
    sid = sid_for()
    assert app.get_wa_setting(f"lead_alerted:{sid}") == f"61452006342|sarah@example.com"

    # Repeating the same details must not fire a second email plus SMS.
    again = app.notify_lead_summary(dict(rows[0]), reason="wa_ai_contact_capture")
    assert again is False
    assert len(wa.alerts) == 1


def test_a_typed_second_number_is_kept_beside_the_sender(wa):
    post(wa, "ring my partner on 0412 345 678 instead")
    rows = leads()
    assert len(rows) == 1
    assert rows[0]["phone"] == SENDER_E164
    assert "412345678" in "".join(ch for ch in rows[0]["phone_typed"] if ch.isdigit())
    assert wa.alerts[0]["phone_typed"] == rows[0]["phone_typed"]


def test_typing_the_same_number_you_are_writing_from_is_not_a_second_number(wa):
    post(wa, "my number is 0452 006 342")
    rows = leads()
    assert len(rows) == 1
    assert rows[0]["phone"] == SENDER_E164
    assert "phone_typed" not in rows[0]


# ---- (5) a name after a human request does not double-alert ---------------

def test_name_after_a_human_request_updates_the_lead_without_a_second_alert(wa):
    post(wa, "can nick call me please?")
    assert len(wa.alerts) == 1
    wa.ai["reply"] = "Will do. What's your first name?"
    post(wa, "what else do you offer?")
    post(wa, "Sarah")
    rows = leads()
    assert len(rows) == 1 and rows[0]["name"] == "Sarah"
    assert len(wa.alerts) == 1


# ---- (6) the WhatsApp display name -----------------------------------------

def test_profile_name_is_stored_once_and_shown_as_unverified(wa, monkeypatch):
    writes = []
    real_set = app.set_wa_setting
    monkeypatch.setattr(app, "set_wa_setting",
                        lambda key, value: (writes.append(key), real_set(key, value))[1])
    post(wa, "my email is sarah@example.com", profile_name="Saz 🏋", wa_id="61452006342")
    post(wa, "and what about parking?", profile_name="Saz 🏋", wa_id="61452006342")
    assert [k for k in writes if k.startswith("wa_profile_name:")] == [f"wa_profile_name:{sid_for()}"]

    payload = app.wa_dashboard_payload()
    thread = next(t for t in payload["conversations"] if t["session_id"] == sid_for())
    assert thread["phone"] == SENDER_E164
    assert thread["profile_name"] == "Saz 🏋"

    lead = dict(leads()[0])
    app.annotate_lead_channel(lead, sid_for())
    text = app.format_lead_summary(lead)
    assert "WhatsApp name: Saz" in text and "not verified" in text
    html = app.format_lead_summary_html(lead)
    assert "not verified" in html and f'tel:{SENDER_E164}' in html
    assert "/admin#whatsapp" in html

    # The display name is never the lead's name and never event metadata.
    assert leads()[0].get("name") is None
    captured = events("lead_captured")
    assert captured and all("wa_profile_name" not in row for row in captured)


def test_profile_name_is_ignored_when_wa_id_disagrees(wa):
    post(wa, "hello there", profile_name="Someone Else", wa_id="61400999999")
    assert app.get_wa_setting(f"wa_profile_name:{sid_for()}") == ""


# ---- (7) the lead row whitelist and the fail-soft retry --------------------

def _supabase_capture(monkeypatch, reject_column=None):
    """Capture what PostgREST would receive. Events go to Supabase too once it
    is enabled, so they are captured here rather than read from EVENTS_FILE."""
    bodies, logged = [], []

    def fake_request(method, table, *, params=None, json_body=None, prefer=None):
        if method == "GET":
            return []
        body = dict(json_body or {})
        if table == app.SUPABASE_TABLES["events"]:
            logged.append(body)
            return []
        if table != app.SUPABASE_TABLES["leads"]:
            return []
        if reject_column and reject_column in body:
            raise Exception(
                f"Could not find the '{reject_column}' column of 'outdoor_squad_leads' in the schema cache"
            )
        bodies.append(body)
        return []

    monkeypatch.setattr(app, "supabase_enabled", lambda: True)
    monkeypatch.setattr(app, "supabase_request", fake_request)
    return bodies, logged


def test_save_lead_sends_only_real_columns(wa, monkeypatch):
    bodies, _logged = _supabase_capture(monkeypatch)
    app.save_lead({
        "session_id": sid_for(), "channel": "whatsapp", "phone": SENDER_E164,
        "wa_profile_name": "Saz", "alert_type": "human_request", "raw_message": "hi",
    })
    assert len(bodies) == 1
    assert set(bodies[0]) <= set(app.LEAD_ROW_FIELDS)
    assert bodies[0]["channel"] == "whatsapp"


def test_a_missing_column_costs_one_field_not_the_whole_lead(wa, monkeypatch):
    bodies, logged = _supabase_capture(monkeypatch, reject_column="channel")
    app.save_lead({"session_id": sid_for(), "channel": "whatsapp", "phone": SENDER_E164})
    assert len(bodies) == 1
    assert "channel" not in bodies[0]
    assert bodies[0]["phone"] == SENDER_E164
    degraded = [row for row in logged if row["event_type"] == "lead_storage_degraded"]
    assert degraded and degraded[0]["metadata"]["column"] == "channel"
    assert [row for row in logged if row["event_type"] == "lead_storage_error"] == []
    assert json.loads(app.LEADS_FILE.read_text()) == []


# ---- (8) the leads tab and the CSV carry the channel ----------------------

def test_leads_api_and_csv_carry_channel_and_typed_number(wa, monkeypatch):
    app.LEADS_FILE.write_text(json.dumps([
        {"timestamp": "2026-09-11T09:00:00", "name": "Sarah", "phone": SENDER_E164,
         "phone_typed": "0412 345 678", "session_id": sid_for(), "channel": "whatsapp"},
        {"timestamp": "2026-09-11T09:05:00", "name": "Tom", "email": "tom@example.com",
         "session_id": "widget-abc"},
    ]))
    monkeypatch.setattr(app, "ADMIN_PASSWORD", "test-admin-password")
    with app._login_failures_lock:
        app._login_failures.clear()
    auth = (app.ADMIN_USERNAME, "test-admin-password")

    rows = wa.get("/api/leads", auth=auth).json()
    assert [r["channel"] for r in rows] == ["whatsapp", "website"]

    csv_text = wa.get("/api/leads.csv", auth=auth).text
    header = csv_text.splitlines()[0]
    assert "channel" in header and "phone_typed" in header
    assert "whatsapp" in csv_text and "website" in csv_text
    assert "0412 345 678" in csv_text


# ---- (9) the guard strips the ask, not the answer -------------------------

def test_guard_keeps_a_non_asking_mention_of_email_address(wa):
    sid = sid_for()
    app.conversations[sid] = [{"role": "user", "content": "hi"}]
    draft = "Your email address is never shared with anyone. Sessions run at 6am."
    assert app.enforce_contact_and_handoff_progression(draft, sid) == draft


def test_guard_drops_only_the_sentence_that_asks_for_a_number(wa):
    sid = sid_for()
    app.conversations[sid] = [{"role": "user", "content": "hi"}]
    out = app.enforce_contact_and_handoff_progression(
        "Redfern runs at 6am sharp. What's your mobile number?", sid)
    assert "6am" in out and "mobile number" not in out.lower()


def test_guard_keeps_the_name_ask_until_a_name_is_known(wa):
    sid = sid_for()
    app.conversations[sid] = [{"role": "user", "content": "hi"}]
    draft = "Redfern runs at 6am sharp. What's your first name?"
    assert "first name" in app.enforce_contact_and_handoff_progression(draft, sid).lower()

    app.conversations[sid] = [
        {"role": "assistant", "content": "What's your first name?"},
        {"role": "user", "content": "Sarah"},
    ]
    out = app.enforce_contact_and_handoff_progression(draft, sid)
    assert "first name" not in out.lower() and "6am" in out


def test_guard_never_returns_an_empty_reply(wa):
    sid = sid_for()
    app.conversations[sid] = [{"role": "user", "content": "hi"}]
    out = app.enforce_contact_and_handoff_progression("What's your mobile number?", sid)
    assert out.strip()


# ---- (10) the REST branch renders the substituted reply too ---------------

def test_non_repeating_followup_on_the_rest_branch_asks_for_no_mobile(wa):
    long_reply = (
        "Camperdown and Redfern both run early mornings and evenings, and the "
        "free trial is the cleanest way to see which one suits your week. "
        "Drop your first name + mobile and Humanoid-Nick or Lyn will sort it."
    )
    wa.ai["reply"] = long_reply
    post(wa, "tell me about the sessions")
    post(wa, "tell me more about the sessions please")
    for _to, body in wa.sent:
        assert "mobile" not in body.lower()
        assert "**" not in body


# ---- (11) the website path is untouched ----------------------------------

def test_website_name_only_message_creates_no_lead():
    app.conversations.clear()
    assert app.extract_lead_info("I'm Sarah", "widget-abc") is None


def test_website_contact_capture_reply_still_asks_for_a_name():
    app.conversations.clear()
    app.conversations["widget-abc"] = [{"role": "user", "content": "sarah@example.com"}]
    reply = app.contact_capture_reply("sarah@example.com", "widget-abc")
    assert "pop your first name in too" in reply


def test_website_wholesale_replacement_still_fires_after_typed_details():
    app.conversations.clear()
    app.conversations["widget-abc"] = [{"role": "user", "content": "sarah@example.com"}]
    out = app.enforce_contact_and_handoff_progression(
        "Happy to help. What's your mobile number?", "widget-abc")
    assert "won" in out and "ask for those again" in out


def test_website_reply_keeps_a_first_name_ask_after_typed_details():
    app.conversations.clear()
    app.conversations["widget-abc"] = [{"role": "user", "content": "sarah@example.com"}]
    out = app.enforce_contact_and_handoff_progression(
        "Sessions run at 6am. What's your first name?", "widget-abc")
    assert "first name" in out.lower()


def test_website_lead_rows_are_stamped_website():
    app.conversations.clear()
    info = app.extract_lead_info("my email is tom@example.com", "widget-abc")
    assert info and info["channel"] == "website"
    assert "phone_typed" not in info


def test_wa_sender_e164_is_empty_for_a_widget_session():
    assert app.wa_sender_e164("widget-abc") == ""
    assert app.wa_sender_e164("wa-61452006342") == SENDER_E164


# ---- the strip must never cost a link, an email address or a paragraph -----

_SCRIPTED_PROBES = [
    "is there a free trial?",
    "how much is it?",
    "what times do you run?",
    "where do you meet?",
    "do you do anything for teenagers?",
    "i have a bad knee",
    "i'm pregnant",
    "what do you do with my data?",
    "can i get a discount?",
    "are you a bot?",
    "i used to be a member, can i come back?",
    "can you do a corporate group booking?",
    "do you help with nutrition?",
    "i'm nervous about group training",
    "no thanks, bye",
    "i never got a reply from anyone",
    "hmm",
    "what makes you different from a gym?",
]


@pytest.mark.parametrize("known_name", [False, True])
@pytest.mark.parametrize("probe", _SCRIPTED_PROBES)
def test_scripted_branches_survive_the_whatsapp_contact_strip(wa, probe, known_name):
    sid = "wa-61452009999"
    app.conversations[sid] = (
        [{"role": "assistant", "content": "What's your first name?"}, {"role": "user", "content": "Sarah"}]
        if known_name else [{"role": "user", "content": "hi"}]
    )
    raw = app.demo_fallback_reply(probe, session_id=sid)
    out = app.render_for_whatsapp(app.wa_strip_contact_asks(raw, sid))
    assert out.strip(), f"{probe!r} was stripped to nothing"
    assert "mobile" not in out.lower(), f"{probe!r} still asks for a mobile: {out}"
    assert set(app._WA_EMAIL_RE.findall(raw)) <= set(app._WA_EMAIL_RE.findall(out)), \
        f"{probe!r} lost an email address"
    assert raw.count("https://") == out.count("https://"), f"{probe!r} lost a link"


def test_the_trial_link_and_its_paragraphs_survive(wa):
    sid = "wa-61452009998"
    app.conversations[sid] = [
        {"role": "assistant", "content": "What's your first name?"},
        {"role": "user", "content": "Sarah"},
    ]
    raw = app.demo_fallback_reply("is there a free trial?", session_id=sid)
    out = app.wa_strip_contact_asks(raw, sid)
    assert "momence.com" in out
    # WhatsApp copy is written in blocks; the strip must not flatten it.
    assert "\n\n" in out


def test_without_a_known_name_the_ask_becomes_a_first_name_ask(wa):
    sid = "wa-61452009997"
    app.conversations[sid] = [{"role": "user", "content": "hi"}]
    raw = app.demo_fallback_reply("is there a free trial?", session_id=sid)
    out = app.render_for_whatsapp(app.wa_strip_contact_asks(raw, sid))
    assert "first name" in out.lower()
    assert "mobile" not in out.lower()


def test_an_email_fallback_is_kept_when_the_name_ask_is_removed(wa):
    sid = "wa-61452009996"
    app.conversations[sid] = [
        {"role": "assistant", "content": "What's your first name?"},
        {"role": "user", "content": "Sarah"},
    ]
    draft = ("Not one for me to call. Drop your name + mobile or email "
             "innerwest@outdoorsquad.com.au and they'll sort it with you directly.")
    out = app.render_for_whatsapp(app.wa_strip_contact_asks(draft, sid))
    assert "innerwest@outdoorsquad.com.au" in out
    assert "first name" not in out.lower() and "mobile" not in out.lower()
