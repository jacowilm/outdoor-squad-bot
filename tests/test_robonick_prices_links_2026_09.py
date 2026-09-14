"""Regression tests for the 2026-09-14 Nick signoff-meeting miss: the
deterministic pricing ladder ("The main doors are...") was missing SPT 3x +
Group ($175/wk) and the Youth Training Program ($25/wk), and a "YPT" typo for
YTP wasn't recognised as a youth-program query. Also covers the new optional
bottom-link helper for prices/timetable topics.
"""

import app


SESSION = "test-robonick-prices-links"


def reply(message: str, session_id: str = SESSION) -> str:
    direct = app.contextual_short_reply(message, session_id)
    return direct or app.demo_fallback_reply(message, session_id)


def test_exact_transcript_typo_prices_question_has_full_ladder():
    # Exact wording from the live WhatsApp transcript (typo and all) that the
    # parent verified via /api/conversation-transcripts.
    text = reply("What are you prices?")

    assert "$51/wk" in text  # Squad Ascent
    assert "$25/wk" in text  # Squad Student (and/or YTP)
    assert "$397" in text  # 28-Day Kickstarter
    assert "$125/wk" in text  # SPT 2x + Group
    assert "$37" in text  # casual drop-in
    assert app.TRIAL_LINK in text

    # The two tiers missing from the live reply.
    assert "$175/wk" in text  # SPT 3x + Group
    assert "youth training program" in text.lower()


def test_standard_pricing_phrasing_has_spt3x_and_youth_program():
    text = reply("What are your prices?")

    assert "spt 3x" in text.lower()
    assert "$175/wk" in text
    assert "youth training program" in text.lower()
    assert "$25/wk" in text


def test_squad_student_price_unchanged_at_25_per_week():
    # Jacobo explicitly confirmed "leave it at 25" — Squad Student must not
    # silently shift toward the conflicting $55/$25-PW figures seen on the
    # live membership-options page scrape.
    text = reply("How much does it cost to join?")

    assert "Squad Student: $25/wk" in text
    assert "$55" not in text


def test_ypt_typo_routes_to_youth_pricing_not_adult_ladder():
    # "YPT" is a common transposition of the site's "YTP" abbreviation and was
    # reported missing from price answers in the Nick signoff meeting.
    text = reply("how much is YPT a week?")

    assert "$25/wk" in text
    assert "youth training program" in text.lower()
    # Should be the short youth-specific answer, not the full adult ladder.
    assert "$51/wk" not in text
    assert "the main doors are" not in text.lower()


def test_ytp_typo_still_routes_to_youth_pricing():
    text = reply("how much is YTP a week?")

    assert "$25/wk" in text
    assert "youth training program" in text.lower()


def test_pricing_reply_includes_relevant_membership_page_link_once():
    text = reply("What are your prices?")

    url = "https://www.outdoorsquad.com.au/membership-options"
    assert url in text
    assert text.count(url) == 1


def test_pricing_reply_link_is_appended_not_replacing_the_answer():
    text = reply("What are your prices?")
    url = "https://www.outdoorsquad.com.au/membership-options"

    # The full price ladder must appear BEFORE the optional link, i.e. the
    # link is additive, never a substitute for the answer.
    assert text.index("$175/wk") < text.index(url)


def test_pricing_reply_stays_within_whatsapp_body_budget():
    text = reply("What are your prices?")

    assert len(text) <= app.WA_BODY_LIMIT


def test_timetable_overview_includes_relevant_timetable_page_link_once():
    text = app.timetable_reply("what's the timetable look like", SESSION)

    url = "https://www.outdoorsquad.com.au/timetable-and-classes"
    assert url in text
    assert text.count(url) == 1
    # The Momence booking link must still be present too (answer first).
    assert app.TRIAL_LINK in text


def test_timetable_filtered_reply_includes_relevant_timetable_page_link_once():
    text = app.timetable_reply("what's on monday", SESSION)

    url = "https://www.outdoorsquad.com.au/timetable-and-classes"
    assert url in text
    assert text.count(url) == 1


def test_topic_link_line_skips_unknown_topic():
    # "locations" and "trial" are now supported topics — use a genuinely
    # unrecognised one so this still tests the unknown-topic branch.
    assert app.topic_link_line("nutrition", "any text") == ""


def test_topic_link_line_never_duplicates_existing_url():
    url = "https://www.outdoorsquad.com.au/membership-options"
    already_has_it = f"some reply that already links {url} once"

    assert app.topic_link_line("prices", already_has_it) == ""
