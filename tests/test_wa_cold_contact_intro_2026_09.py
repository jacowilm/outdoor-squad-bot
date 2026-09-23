"""The 23 Sep 2026 fresh-phone finding: a cold WhatsApp contact whose first
message is an ordinary question got no introduction.

Nick ran the fresh-phone check from a greengrocer's handset that had never
messaged the number (session wa-61413629790). The first message was "Hey do
your have parking", which should_use_local_tone_handler() answers from the
scripted handler. That handler never sees whatsapp_channel_prompt(), and
wa_first_contact_greeting() only defers VAGUE openers to the AI, so the
customer met the business with no idea they were talking to a bot, while the
reply was still logged as kind="intro".
"""

import app


def _fresh(sid):
    app.conversations.pop(sid, None)
    return sid


def test_cold_contact_asking_a_real_question_is_introduced():
    sid = _fresh("wa-61400000801")
    assert app.wa_needs_intro(sid) is True
    reply = app.wa_prepend_intro(
        app.demo_fallback_reply("Hey do your have parking", session_id=sid), sid
    )
    assert "Robo-Nick" in reply.splitlines()[0]
    assert "parking" in reply.lower()


def test_the_scripted_identity_answer_is_not_introduced_twice():
    sid = _fresh("wa-61400000802")
    raw = app.demo_fallback_reply("are you a bot", session_id=sid)
    assert app.wa_prepend_intro(raw, sid) == raw
    assert app.wa_prepend_intro(raw, sid).count("Robo-Nick") == 1


def test_a_later_reply_in_the_same_episode_is_not_introduced():
    sid = _fresh("wa-61400000803")
    app.episode_history(sid).append({"role": "user", "content": "hi"})
    app.episode_history(sid).append({"role": "assistant", "content": "yo"})
    assert app.wa_prepend_intro("Prices start at $51/wk.", sid) == "Prices start at $51/wk."


def test_website_sessions_are_untouched():
    sid = _fresh("web-cold-visitor")
    assert app.wa_needs_intro(sid) is False
    assert app.wa_prepend_intro("Hello there.", sid) == "Hello there."


def test_an_empty_reply_is_left_alone():
    assert app.wa_prepend_intro("", _fresh("wa-61400000804")) == ""


def test_the_intro_line_carries_no_em_dash_and_no_markdown():
    assert "—" in app.WA_DETERMINISTIC_INTRO is False or "—" not in app.WA_DETERMINISTIC_INTRO
    assert "**" not in app.WA_DETERMINISTIC_INTRO
    assert app.render_for_whatsapp(app.WA_DETERMINISTIC_INTRO) == app.WA_DETERMINISTIC_INTRO
