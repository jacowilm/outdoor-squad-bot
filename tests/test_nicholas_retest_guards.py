import app


def reply(message: str) -> str:
    direct = app.contextual_short_reply(message, "test-nicholas-retest")
    return direct or app.demo_fallback_reply(message, "test-nicholas-retest")


def test_plain_budget_does_not_invent_partner_or_discount_objection():
    text = reply("Money's tight this quarter — what are the options?")

    assert "$51/wk" in text
    assert "free trial" in text.lower()
    lowered = text.lower()
    assert "partner" not in lowered
    assert "both" not in lowered
    assert "either of you" not in lowered
    assert "haggle" not in lowered
    assert "discount" not in lowered


def test_multi_person_family_answers_adults_and_teen():
    text = reply("My wife and I both want to join, and our 14-year-old is keen too.")

    lowered = text.lower()
    assert "two squad ascent" in lowered
    assert "youth training program" in lowered
    assert "saturday" in lowered
    assert "9:15" in lowered


def test_spt_size_answers_four_max_first_without_group_hedge():
    text = reply("How many people are in those SPT sessions again?")

    lowered = text.lower()
    assert "4" in lowered
    assert "max" in lowered
    assert "numbers vary" not in lowered
    assert "group classes stay small" not in lowered
    first_sentence = text.split(".")[0].lower()
    assert "4" in first_sentence or "four" in first_sentence


def test_value_125_answer_does_not_trigger_discount_guard():
    text = reply("What do I get for $125 a week?")

    assert "SPT" in text
    assert "$125/wk" in text
    assert "four-person max" in text.lower()
    lowered = text.lower()
    assert "haggle" not in lowered
    assert "discount" not in lowered
    assert "random discounts" not in lowered


def test_redfern_thursday_uses_exact_timetable_not_generic_fallback():
    text = reply("What is on at Redfern Thursday?")

    lowered = text.lower()
    assert "thursday 6:00am" in lowered
    assert "power'n'pilates" in lowered
    assert "redfern" in lowered
    assert "6:30pm" not in lowered
    assert "i won’t invent" in lowered or "won't invent" in lowered


def test_privacy_question_gets_plain_privacy_answer():
    text = reply("Who can see what I've typed?")

    lowered = text.lower()
    assert "only share what you’re comfortable" in lowered or "only share what you're comfortable" in lowered
    assert "nick" in lowered or "lyn" in lowered
    assert "innerwest@outdoorsquad.com.au" in lowered
