"""Regression guards for Robo-Coach's WhatsApp stranger pass (2026-09-10).

RC messaged the live number as a stranger and caught, among the webhook
timeout: the price list hedging exact prices with "Roughly" and missing the
Squad Student rung, timetable/pricing answers carrying no booking link, and
"Do you have parking?" being answered with the venue chooser instead of the
actual parking facts. These pin the exact failing phrasings from the
transcript (session wa-61402439361, 9 Sep 06:51-06:57 UTC).
"""

import app


SESSION = "test-rc-stranger-pass"


def reply(message: str, session: str = SESSION) -> str:
    direct = app.contextual_short_reply(message, session)
    return direct or app.demo_fallback_reply(message, session)


def test_price_list_has_no_roughly_hedge():
    # RC: casual about fit is fine, but prices are exact and should read exact.
    text = reply("Could you send me the price list?")
    assert not text.lower().startswith("roughly"), text
    assert "roughly" not in text.lower(), text


def test_price_list_includes_squad_student():
    text = reply("Could you send me the price list?")
    lowered = text.lower()
    assert "squad student" in lowered, text
    assert "$25/wk" in text, text
    assert "verified students" in lowered, text


def test_price_list_includes_booking_link():
    text = reply("Could you send me the price list?")
    assert app.TRIAL_LINK in text, text


def test_price_list_keeps_the_full_ladder():
    # Adding Student must not dislodge the rest of the ladder.
    text = reply("Could you send me the price list?")
    for figure in ["$51/wk", "$397", "$125/wk", "$37"]:
        assert figure in text, f"{figure} missing from: {text}"


def test_timetable_generic_answer_includes_booking_link():
    # RC's bare "Timetable?" got the generic week summary with no link.
    text = reply("Timetable?")
    assert app.TRIAL_LINK in text, text


def test_timetable_filtered_answer_still_includes_booking_link():
    text = reply("What's on Monday morning?")
    assert app.TRIAL_LINK in text, text


def test_parking_question_gets_parking_facts_not_venue_chooser():
    # RC: "Do you have parking?" was answered with the two-venues block.
    text = reply("Do you have parking?")
    lowered = text.lower()
    assert "parking" in lowered, text
    # The real logistics facts, not the chooser:
    assert "australia st" in lowered or "chalmers st" in lowered, text
    assert "two main training spots" not in lowered, text


def test_parking_question_with_venue_gets_that_venue():
    text = reply("Is there parking at Camperdown?")
    lowered = text.lower()
    assert "australia st" in lowered, text
    assert "two main training spots" not in lowered, text


def test_youth_pricing_answer_not_broken_by_ladder_edit():
    # The youth branch sits above the ladder in the same handler; make sure the
    # edit didn't reroute it.
    session = "test-rc-youth-pricing"
    app.load_conversation(session).clear()
    text = reply("How much for my 12 year old?", session)
    assert "$25/wk" in text, text
    assert "youth" in text.lower() or "kids" in text.lower() or "kid" in text.lower(), text


def test_wa_status_line_off_and_unreachable_and_live_read_differently():
    off = app.wa_channel_status_line({"wa_channel_enabled": False, "wa_entry_points_live": False})
    unreachable = app.wa_channel_status_line({"wa_channel_enabled": True, "wa_entry_points_live": False})
    live = app.wa_channel_status_line({"wa_channel_enabled": True, "wa_entry_points_live": True})
    assert len({off, unreachable, live}) == 3
    assert "kill switch" in off.lower()
    assert "not yet publicly reachable" in unreachable.lower()
    assert "publicly reachable" in live.lower() and "not yet" not in live.lower()


def test_report_text_carries_wa_status_line():
    stats = {
        "window_days": 7,
        "widget_impressions": 0,
        "raw_page_loads": 0,
        "widget_opened_sessions": 0,
        "conversations_started": 0,
        "engagement_rate": 0.0,
        "contact_leads": 0,
        "conversation_to_lead_rate": 0.0,
        "trial_link_clicks": 0,
        "booking_link_shown_sessions": 0,
        "handoffs": 0,
        "handoff_suggestions": 0,
        "human_requests": 0,
        "handoff_alerts_sent": 0,
        "handoff_rate": 0.0,
        "lead_lines": [],
        "teaser_variants": {},
        "teaser_variants_total": {},
        "widget_versions": {},
        "shipped_lines": [],
        "wa_conversations": 0,
        "wa_messages": 0,
        "wa_leads": 0,
        "wa_manual_replies": 0,
        "wa_channel_enabled": True,
        "wa_entry_points_live": False,
    }
    text = app.format_report_text(stats)
    assert "Channel status:" in text
    assert "not yet publicly reachable" in text


def test_report_greeting_test_shows_running_totals_with_week_underneath():
    stats_variants = {
        "control": {"visitors": 2, "opened": 1, "conversations": 1},
        "nick": {"visitors": 3, "opened": 2, "conversations": 1},
    }
    stats_totals = {
        "control": {"visitors": 20, "opened": 8, "conversations": 5},
        "nick": {"visitors": 22, "opened": 11, "conversations": 6},
    }
    stats = {
        "window_days": 7,
        "widget_impressions": 5,
        "raw_page_loads": 5,
        "widget_opened_sessions": 3,
        "conversations_started": 2,
        "engagement_rate": 0.4,
        "contact_leads": 0,
        "conversation_to_lead_rate": 0.0,
        "trial_link_clicks": 0,
        "booking_link_shown_sessions": 0,
        "handoffs": 0,
        "handoff_suggestions": 0,
        "human_requests": 0,
        "handoff_alerts_sent": 0,
        "handoff_rate": 0.0,
        "lead_lines": [],
        "teaser_variants": stats_variants,
        "teaser_variants_total": stats_totals,
        "widget_versions": {},
        "shipped_lines": [],
        "wa_conversations": 0,
        "wa_messages": 0,
        "wa_leads": 0,
        "wa_manual_replies": 0,
        "wa_channel_enabled": True,
        "wa_entry_points_live": False,
    }
    text = app.format_report_text(stats)
    # Headline = running totals, not the weekly slice.
    assert "20 visitors" in text and "22 visitors" in text
    assert "since 6 Aug" in text
    # Weekly figures underneath.
    assert "This week:" in text
    assert "1/2" in text and "2/3" in text
