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
    assert "$51/wk each" in lowered
    assert "youth training program" in lowered
    assert "saturday" in lowered
    assert "9:15" in lowered
    # the household answer must be visitor copy, not a leaked internal note
    assert "answer all three people" not in lowered


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
    assert "flow'n'flex" in lowered
    assert "power'n'pilates" not in lowered
    assert "redfern" in lowered
    assert "6:30pm" not in lowered
    assert "i won’t invent" in lowered or "won't invent" in lowered


def test_privacy_question_gets_plain_privacy_answer():
    text = reply("Who can see what I've typed?")

    lowered = text.lower()
    # honest blurb: says chats are logged, who sees them, and that contact
    # details are masked in stored logs (launch prerequisite, Nicholas 2026-06-11)
    assert "logged" in lowered
    assert "nick" in lowered or "lyn" in lowered
    assert "masked" in lowered
    assert "innerwest@outdoorsquad.com.au" in lowered


def test_bot_is_robo_nick_and_human_is_humanoid_nick():
    # Nicholas's 2026-06-12 naming decision renames the HUMAN: the bot persona
    # stays Robo-Nick; the human coach (formerly "Real Nick") is Humanoid-Nick.
    text = reply("Are you a real person or a bot?")

    lowered = text.lower()
    assert "robo-nick" in lowered          # the bot self-identifies as Robo-Nick
    assert "humanoid-nick" in lowered      # the human is referenced as Humanoid-Nick
    assert "real nick" not in lowered      # the old human name is fully retired


def test_injury_handoff_acknowledges_specific_named_person_without_instruction_leak():
    text = reply("I'm Garth. I've got tendinitis in my elbows and wrists, plus a shoulder issue. Moderately fit — can I train?")

    lowered = text.lower()
    assert "garth" in lowered
    assert "elbows" in lowered or "wrists" in lowered or "shoulder" in lowered
    assert "every injury is individual" in lowered
    assert "the bot should" not in lowered
    assert "chat widget" not in lowered
    assert "drop your name" not in lowered
