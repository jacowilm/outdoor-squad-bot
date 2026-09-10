"""Regression guards from the stage-1 sign-off runsheet dry run (2026-09-10).

The runsheet was replayed in-process against the real deterministic paths with
every outbound transport captured. Two answer defects and two alert defects
came out of it:

- "where exactly is the Camperdown session?" asked right after the parking
  answer was read as the visitor PICKING Camperdown ("Camperdown it is.") and
  got a goals question instead of the address.
- A visitor who handed over name + email a second time hit the repeat-detector
  (the acknowledgement matched the previous turn) and was told "that one's
  outside what Robo-Nick can reliably do", then asked for a first name and
  mobile they had just typed.
- The same details repeated in one conversation fired Nick a second identical
  owner alert (email + SMS); the Momence push was deduped, the alert was not.
- classify_route matched bare "son" inside "real perSON", filing a human
  request as a YTP / parent enquiry in the alert.
"""

import app


def _conv(session, turns):
    app.conversations[session] = [{"role": role, "content": content} for role, content in turns]


def wa_reply(message, session):
    # Mirrors the WhatsApp inline path: deterministic answer, then the repeat detector.
    reply = app.demo_fallback_reply(message, session_id=session)
    return app.prevent_repetitive_reply(reply, message, session)


def test_where_is_camperdown_after_parking_answer_gets_the_address():
    session = "test-signoff-where-camperdown"
    _conv(session, [("user", "Do you have parking?"), ("assistant", app.location_detail_reply(""))])
    text = wa_reply("Great, and where exactly is the Camperdown session?", session)
    assert "mallett st" in text.lower(), text
    assert "camperdown it is" not in text.lower(), text


def test_where_is_redfern_right_after_the_timetable_gets_the_address():
    session = "test-signoff-where-redfern"
    _conv(session, [
        ("user", "What's the timetable this week?"),
        ("assistant", "Quick version of the current week... Camperdown or Redfern, which is closer for you to walk into?"),
    ])
    text = wa_reply("Where is the Redfern one?", session)
    assert "redfern park" in text.lower(), text


def test_bare_venue_mention_or_comparison_is_still_not_an_address_question():
    assert not app.asks_venue_address("what makes you different in camperdown")
    assert not app.asks_venue_address("where is better, camperdown or redfern")
    assert not app.asks_venue_address("camperdown")
    assert app.asks_venue_address("where exactly is the camperdown session")
    assert app.asks_venue_address("what's the address for redfern")


def test_repeated_contact_details_are_confirmed_not_handed_off():
    session = "test-signoff-contact-repeat"
    first = "can I talk to a real person? I'm Test Human, 0452 006 342"
    _conv(session, [("user", first), ("assistant", app.contact_capture_reply(first, session))])
    message = "Sure. Signoff Test, signoff.test@example.com, keen on Camperdown"
    app.conversations[session].append({"role": "user", "content": message})
    text = wa_reply(message, session)
    assert "outside what robo-nick" not in text.lower(), text
    assert "drop your first name" not in text.lower(), text
    assert "signoff.test@example.com" in text, text

    again = "Did that go through? Signoff Test, signoff.test@example.com"
    app.conversations[session] += [{"role": "assistant", "content": text}, {"role": "user", "content": again}]
    text2 = wa_reply(again, session)
    assert "outside what robo-nick" not in text2.lower(), text2
    assert "signoff.test@example.com" in text2, text2


def test_classify_route_real_person_is_human_handoff_not_ytp():
    assert app.classify_route("can i talk to a real person? i'm test human, 0452 006 342") == "human handoff"


def test_classify_route_still_catches_youth_words():
    assert app.classify_route("my son is 12, is there something for him") == "YTP / parent enquiry"
    assert app.classify_route("do you do kids classes") == "YTP / parent enquiry"
    assert app.classify_route("i'm a teen, 15, can i join") == "YTP / parent enquiry"
    assert app.classify_route("just a casual drop-in while visiting") == "casual drop-in"


def test_lead_summary_concerns_do_not_collide_on_person():
    session = "test-signoff-concerns"
    _conv(session, [("user", "can I talk to a real person? 0452 006 342")])
    summary = app.build_lead_summary(session, "")
    assert "child/youth" not in summary["concerns"], summary
    assert summary["route"] == "human handoff", summary


def _alert_sandbox(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "SUPABASE_URL", "")
    monkeypatch.setattr(app, "SUPABASE_KEY", None)
    monkeypatch.setattr(app, "WA_STATE_FILE", tmp_path / "wa_state.json")
    app._wa_setting_cache.clear()
    monkeypatch.setattr(app, "lead_summary_delivery_configured", lambda: True)
    sent = []
    monkeypatch.setattr(app, "send_lead_summary_email", lambda li: (sent.append(("email", li.get("phone"), li.get("email"))), True)[1])
    monkeypatch.setattr(app, "send_lead_summary_phone", lambda li: (sent.append(("phone", li.get("phone"), li.get("email"))), True)[1])
    return sent


def test_lead_alert_fires_once_per_details_and_again_for_new_details(monkeypatch, tmp_path):
    sent = _alert_sandbox(monkeypatch, tmp_path)
    lead = {"session_id": "wa-61400000002", "phone": "0452 006 342"}
    assert app.notify_lead_summary(lead, reason="wa_ai_contact_capture") is True
    assert app.notify_lead_summary(dict(lead), reason="wa_ai_contact_capture") is False
    assert len(sent) == 2, sent  # email + phone, once

    with_email = {**lead, "email": "signoff.test@example.com"}
    assert app.notify_lead_summary(with_email, reason="wa_ai_contact_capture") is True
    assert app.notify_lead_summary(dict(with_email), reason="wa_ai_contact_capture") is False
    assert len(sent) == 4, sent


def test_explicit_human_request_alert_is_never_deduped_by_an_earlier_lead_alert(monkeypatch, tmp_path):
    sent = _alert_sandbox(monkeypatch, tmp_path)
    lead = {"session_id": "wa-61400000003", "phone": "0452 006 342"}
    assert app.notify_lead_summary(lead, reason="wa_ai_contact_capture") is True
    assert app.notify_lead_summary(dict(lead), reason="explicit_human_request") is True
    assert len(sent) == 4, sent


def test_dedupe_is_per_conversation(monkeypatch, tmp_path):
    sent = _alert_sandbox(monkeypatch, tmp_path)
    assert app.notify_lead_summary({"session_id": "wa-1", "phone": "0452 006 342"}, reason="wa_ai_contact_capture") is True
    assert app.notify_lead_summary({"session_id": "wa-2", "phone": "0452 006 342"}, reason="wa_ai_contact_capture") is True
    assert len(sent) == 4, sent


def test_human_request_alert_carries_the_name(monkeypatch, tmp_path):
    session = "wa-61400000009"
    message = "can I talk to a real person? I'm Test Human, 0452 006 342"
    _conv(session, [("user", message)])
    monkeypatch.setattr(app, "SUPABASE_URL", "")
    monkeypatch.setattr(app, "SUPABASE_KEY", None)
    monkeypatch.setattr(app, "HUMAN_REQUEST_CLAIMS_FILE", tmp_path / "claims.jsonl")
    monkeypatch.setattr(app, "HUMAN_REQUEST_CLAIMS_LOCK_FILE", tmp_path / "claims.lock")
    captured = []
    monkeypatch.setattr(app, "notify_lead_summary_async", lambda li, *, reason: captured.append(li))
    assert app.notify_human_request_if_needed(message, session, trusted_widget=True, internal_qa=False) is True
    assert captured and captured[0].get("name") == "Test Human", captured
    assert captured[0].get("route") == "human handoff"
