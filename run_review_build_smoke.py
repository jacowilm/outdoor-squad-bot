#!/usr/bin/env python3
"""Outdoor Squad paid review-build smoke test.

Runs the seven client-review paths without leaving QA rows in owner-facing
lead, event, or conversation files. Exits non-zero until the AI backend is
configured and every AI-backed path avoids the backend-unavailable message.
"""
from __future__ import annotations

import contextlib
import json
import os
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

import app


BASE_DIR = Path(__file__).resolve().parent
DATA_FILES = [
    BASE_DIR / "leads.json",
    BASE_DIR / "events.jsonl",
    BASE_DIR / "conversation_logs.jsonl",
]

CASES = [
    ("beginner", "I'm pretty unfit and nervous. Is this okay for beginners?", True),
    ("kickstarter", "What's the 28 day Kickstarter and who is it for?", True),
    ("ytp", "Do you have anything for my 13 year old son?", True),
    ("pricing", "How much does it cost after the free trial?", True),
    ("injury", "I have a dodgy knee, can I still join?", True),
    ("oddball", "Does this involve nudity or army yelling?", False),
    (
        "contact",
        "I'm Sam, mobile 0412 345 678, keen to try an evening session in Camperdown.",
        False,
    ),
]

BACKEND_UNAVAILABLE = "trouble reaching the AI backend"
REQUIRED_BRAND_TERMS = [
    "Crom",
    "Conan",
    "Robo-Nick",
    "Yo-gah",
    "Puh-lah-tees",
    "having a crack",
    "carrying groceries at 75",
]
REQUIRED_SOURCE_TITLES = [
    "Brand voice guide",
    "Master knowledge base",
    "Offer architecture",
    "Bot avatar routing",
    "bot-faq-completed",
]


@contextlib.contextmanager
def preserve_data_files():
    snapshots = {}
    for path in DATA_FILES:
        snapshots[path] = path.read_text() if path.exists() else None
    try:
        yield
    finally:
        for path, content in snapshots.items():
            if content is None:
                with contextlib.suppress(FileNotFoundError):
                    path.unlink()
            else:
                path.write_text(content)


def main() -> int:
    client = TestClient(app.app)
    failures: list[str] = []
    mode = os.environ.get("OUTDOOR_SQUAD_DEPLOYMENT_MODE", "review").strip().lower()
    if mode not in {"review", "handoff"}:
        failures.append(f"Unknown OUTDOOR_SQUAD_DEPLOYMENT_MODE: {mode}")

    with preserve_data_files():
        health = client.get("/api/health").json()
        print("health:", json.dumps(health, sort_keys=True))

        if not health.get("ai_configured"):
            failures.append("AI backend is not configured")
        if not health.get("admin_configured"):
            failures.append("Admin auth is not configured")
        if not health.get("trial_link_configured"):
            failures.append("Final trial/booking link is not configured")
        if mode == "handoff":
            if not health.get("handoff_ready"):
                failures.append("Handoff mode is not ready for Nicholas-owned ownership")
        else:
            if not health.get("review_hosted_by_ai_sprints"):
                failures.append("Review mode is not marked as AI Sprints-hosted")
            if not health.get("review_ready"):
                failures.append("Review mode is not ready to send to Nicholas/Lyn")
        if health.get("source_chunks", 0) < 1:
            failures.append("source chunks are not loaded")
        loaded_titles = "\n".join(source.get("title", "") for source in app.SOURCE_DOCS)
        for title in REQUIRED_SOURCE_TITLES:
            if title not in loaded_titles:
                failures.append(f"required source document not loaded: {title}")

        prompt_text = "\n\n".join(
            message["content"]
            for message in app.build_agent_messages(
                "I'm into strength training and want to know if the Squad has personality.",
                f"review-smoke-brand-{uuid.uuid4().hex[:8]}",
            )
            if message.get("role") == "system"
        )
        for term in REQUIRED_BRAND_TERMS:
            if term not in prompt_text:
                failures.append(f"brand voice prompt missing required reference: {term}")
        for term in ["Camperdown", "Redfern", "Mallett St", "Waterloo", "Surry Hills"]:
            if term not in prompt_text:
                failures.append(f"operating facts prompt missing location detail: {term}")
        quick_options = app.clean_agent_reply(
            "Quick options: Free 1-day trial: easiest way to try a session; 28-day Kickstarter: 4-week run with assessment + nutrition; SPT: small-group coaching with programming."
        )
        # New rule (Nicholas 2026-06-03): option lists MUST render as a bullet list
        # with a bolded header and bolded labels, not inline semicolon-separated prose.
        if "**Quick options:**" not in quick_options:
            failures.append("quick options header should be bolded on its own line")
        if "- **Free 1-day trial**" not in quick_options:
            failures.append("quick options should expand each option to a bold-labelled bullet")
        placeholder_clean = app.clean_agent_reply(
            "You can email [email], call [phone], or use [link]. Also {HUMAN_EMAIL} / {HUMAN_PHONE} / {TRIAL_LINK}."
        )
        for placeholder in ["[email]", "[phone]", "[link]", "{HUMAN_EMAIL}", "{HUMAN_PHONE}", "{TRIAL_LINK}"]:
            if placeholder.lower() in placeholder_clean.lower():
                failures.append(f"placeholder guard did not resolve {placeholder}")
        for term in [app.HUMAN_EMAIL, app.HUMAN_PHONE, app.TRIAL_LINK]:
            if term not in placeholder_clean:
                failures.append(f"placeholder guard missing real value {term}")

        admin_auth = (
            os.environ.get("OUTDOOR_SQUAD_ADMIN_USERNAME", "outdoorsquad"),
            os.environ.get("OUTDOOR_SQUAD_ADMIN_PASSWORD", ""),
        )
        admin_response = client.get("/admin", auth=admin_auth)
        if admin_response.status_code != 200:
            failures.append(f"admin dashboard HTTP {admin_response.status_code}")
        else:
            admin_html = admin_response.text
            for term in ["Transcripts", "Copy selected", "Download selected", "Search transcripts"]:
                if term not in admin_html:
                    failures.append(f"admin dashboard missing {term}")
        grouped_response = client.get("/api/conversation-transcripts?limit=20", auth=admin_auth)
        if grouped_response.status_code != 200:
            failures.append(f"grouped transcripts HTTP {grouped_response.status_code}")
        elif not isinstance(grouped_response.json(), list):
            failures.append("grouped transcripts should return a list")

        if failures:
            print("\nFAIL")
            for failure in failures:
                print(f"- {failure}")
            return 1

        for name, message, requires_ai in CASES:
            session_id = f"review-smoke-{name}-{uuid.uuid4().hex[:8]}"
            response = client.post(
                "/api/chat",
                json={"session_id": session_id, "message": message},
            )
            data = response.json()
            reply = (data.get("reply") or "").strip()
            preview = " ".join(reply.split())[:180]
            print(f"{name}: status={response.status_code} fallback={data.get('fallback')} reply={preview}")

            if response.status_code != 200:
                failures.append(f"{name}: HTTP {response.status_code}")
            if not reply:
                failures.append(f"{name}: empty reply")
            if reply.lower().startswith(("nice", "good call", "love that", "perfect", "sweet")):
                failures.append(f"{name}: repetitive validation opener")
            if requires_ai and BACKEND_UNAVAILABLE in reply:
                failures.append(f"{name}: AI backend unavailable")
            if "**" in reply:
                failures.append(f"{name}: markdown artifact leaked")
            if any(phrase in reply.lower() for phrase in ["clothes at home", "nudity here!", "naked"]):
                failures.append(f"{name}: unsafe boundary wording")
            if "specific program training" in reply.lower():
                failures.append(f"{name}: incorrect SPT expansion")
            if "book you" in reply.lower() or "want to book" in reply.lower() or "would you like to book" in reply.lower():
                failures.append(f"{name}: unsupported booking claim")
            if "\n\n-\n\n" in reply or reply.strip() == "-":
                failures.append(f"{name}: lone bullet formatting artifact")
            if len(reply) > 900:
                failures.append(f"{name}: reply is too long for the widget")
            if name == "ytp":
                ytp_terms = ["WWCC", "Saturday", "Camperdown", "$25"]
                for term in ytp_terms:
                    if term.lower() not in reply.lower():
                        failures.append(f"{name}: missing YTP detail {term}")
                if "watch" not in reply.lower() and "parent" not in reply.lower():
                    failures.append(f"{name}: missing parent-watch reassurance")
            if name == "kickstarter":
                for term in ["$397", "28 days", "SPT", "8 SPT sessions"]:
                    if term.lower() not in reply.lower():
                        failures.append(f"{name}: missing Kickstarter detail {term}")
                if "3 spt sessions per week" in reply.lower() or "3 sessions per week" in reply.lower():
                    failures.append(f"{name}: incorrectly says Kickstarter defaults to 3 SPT sessions/week")
            if name == "injury":
                if "every injury is individual" not in reply.lower():
                    failures.append(f"{name}: missing Nicholas injury caveat")
                if not any(term in reply.lower() for term in ["nick", "lyn", "coach", "trainer", "human"]):
                    failures.append(f"{name}: injury path should route to a human/coach/trainer")
                if not any(term in reply.lower() for term in ["trial", "spt", "coach chat"]):
                    failures.append(f"{name}: injury path should name a safe next step")
                if "health practitioner" not in reply.lower():
                    failures.append(f"{name}: missing serious-case health-practitioner guardrail")

        extra_conversion_cases = [
            ("nervous-beginner", "I'm pretty unfit and nervous. Is the first class okay for beginners?", ["free trial", "coach", "beginner"]),
            ("bring-friend", "Can I bring my partner or a friend to try it with me?", ["free trial", "friend", "value-stack"]),
            ("rain", "What if it rains during the session?", ["undercover", "layers", "free trial"]),
            ("over-50", "I'm over 50, am I too old for this?", ["50s", "functional strength", "free trial"]),
            ("browsing", "Just browsing for now, thanks.", ["free trial"]),
            ("winter", "Isn't it awkward training outdoors in winter?", ["free trial"]),
            ("quit-gyms", "I've quit gyms before. Why would this be different?", ["free trial"]),
            ("plus-fitness", "$51 a week is a lot. Plus Fitness is $18.", ["free trial", "Plus Fitness", "$51"]),
            ("pt-redirect", "Do you do personal training?", ["SPT", "28-Day Kickstarter"]),
            ("coach-program", "I want a coach who knows my goals and writes me a program.", ["SPT", "28-Day Kickstarter"]),
            ("partner-budget", "My partner and I are keen but we're on a budget. Is pricing flexible?", ["membership levels", "$51/wk", "SPT"]),
            ("generic-group", "Are group classes just generic, or does the coach actually pay attention?", ["coached", "cues", "modifications"]),
            ("flow-flex", "What's Yoga Squad like?", ["Flow'N'Flex", "mobility", "balance"]),
            ("researching", "I'm still looking at options and thinking about it.", ["free trial", "Crom weeps", "research"]),
        ]
        for name, message, required_terms in extra_conversion_cases:
            session_id = f"review-smoke-extra-{name}-{uuid.uuid4().hex[:8]}"
            response = client.post("/api/chat", json={"session_id": session_id, "message": message})
            data = response.json()
            reply = (data.get("reply") or "").strip()
            preview = " ".join(reply.split())[:180]
            print(f"{name}: status={response.status_code} fallback={data.get('fallback')} reply={preview}")
            if response.status_code != 200:
                failures.append(f"{name}: HTTP {response.status_code}")
            for term in required_terms:
                if term.lower() not in reply.lower():
                    failures.append(f"{name}: missing required term {term}")
            if "book you" in reply.lower() or "would you like to book" in reply.lower():
                failures.append(f"{name}: unsupported booking claim")
            if name in {"partner-budget", "generic-group", "flow-flex", "researching"}:
                forbidden = ["flexible pricing", "negotiable", "yoga squad", "generic class"]
                for term in forbidden:
                    if term in reply.lower():
                        failures.append(f"{name}: leaked forbidden wording {term}")

        nicholas_retest_cases = [
            (
                "plain-budget-no-phantom-partner",
                "Money's tight this quarter — what are the options?",
                ["$51/wk", "free trial"],
                ["partner", "both", "either of you", "haggle", "discount"],
            ),
            (
                "family-all-people",
                "My wife and I both want to join, and our 14-year-old is keen too.",
                ["two Squad Ascent", "Youth Training Program", "Saturday", "9:15"],
                ["discount"],
            ),
            (
                "spt-size-direct",
                "How many people are in those SPT sessions again?",
                ["4", "max"],
                ["numbers vary", "group classes stay small"],
            ),
            (
                "value-125-no-discount-guard",
                "What do I get for $125 a week?",
                ["SPT", "$125/wk", "four-person max"],
                ["haggle", "discount", "random discounts"],
            ),
        ]
        for name, message, required_terms, forbidden_terms in nicholas_retest_cases:
            session_id = f"review-smoke-nicholas-retest-{name}-{uuid.uuid4().hex[:8]}"
            response = client.post("/api/chat", json={"session_id": session_id, "message": message})
            data = response.json()
            reply = (data.get("reply") or "").strip()
            preview = " ".join(reply.split())[:180]
            print(f"{name}: status={response.status_code} fallback={data.get('fallback')} reply={preview}")
            if response.status_code != 200:
                failures.append(f"{name}: HTTP {response.status_code}")
            for term in required_terms:
                if term.lower() not in reply.lower():
                    failures.append(f"{name}: missing required term {term}")
            for term in forbidden_terms:
                if term.lower() in reply.lower():
                    failures.append(f"{name}: leaked forbidden wording {term}")

        location_session = f"review-smoke-location-{uuid.uuid4().hex[:8]}"
        response = client.post(
            "/api/chat",
            json={"session_id": location_session, "message": "what locations is there"},
        )
        data = response.json()
        reply = (data.get("reply") or "").strip()
        preview = " ".join(reply.split())[:180]
        print(f"location: status={response.status_code} fallback={data.get('fallback')} reply={preview}")
        for term in ["Camperdown", "Redfern"]:
            if term not in reply:
                failures.append(f"location: missing {term}")
        if "exact" in reply.lower() and "unavailable" in reply.lower():
            failures.append("location: claimed exact location unavailable")

        non_location_cases = [
            ("private-sessions", "Do you do private sessions or can I get a private coach?"),
            ("different-camperdown", "What makes you different from other gyms in Camperdown?"),
        ]
        for name, message in non_location_cases:
            session_id = f"review-smoke-non-location-{name}-{uuid.uuid4().hex[:8]}"
            response = client.post("/api/chat", json={"session_id": session_id, "message": message})
            reply = (response.json().get("reply") or "").strip()
            preview = " ".join(reply.split())[:180]
            print(f"{name}: status={response.status_code} reply={preview}")
            if response.status_code != 200:
                failures.append(f"{name}: HTTP {response.status_code}")
            if "mallett st" in reply.lower() or "redfern st" in reply.lower() or "the barracks" in reply.lower():
                failures.append(f"{name}: non-location question received stock venue address")
            if name == "private-sessions" and not any(term in reply.lower() for term in ["spt", "1:1", "$150"]):
                failures.append("private-sessions: did not answer private coaching path")
            if name == "different-camperdown" and not any(term in reply.lower() for term in ["coaching", "consistency", "free trial"]):
                failures.append("different-camperdown: did not answer differentiation path")

        goal_session = f"review-smoke-goal-choice-{uuid.uuid4().hex[:8]}"
        app.conversations[goal_session] = [
            {
                "role": "assistant",
                "content": "Are you mainly looking to build strength, lose weight, or get back into a routine?",
            }
        ]
        second = client.post(
            "/api/chat",
            json={"session_id": goal_session, "message": "build strength"},
        ).json().get("reply", "")
        preview = " ".join(second.split())[:180]
        print(f"goal-choice: reply={preview}")
        if "Strength path" not in second:
            failures.append("goal-choice: did not advance into strength path")
        repeated_options = ["Free 1-day trial", "28-day Kickstarter", "Weight loss"]
        if sum(1 for term in repeated_options if term.lower() in second.lower()) >= 2:
            failures.append("goal-choice: repeated the previous broad options")

        repeat_location_session = f"review-smoke-location-repeat-{uuid.uuid4().hex[:8]}"
        app.conversations[repeat_location_session] = [
            {
                "role": "assistant",
                "content": (
                    "Redfern sessions are at Redfern Park, Redfern St, Redfern NSW 2016.\n\n"
                    "The usual meeting point is near the Park Cafe at the Sports Oval end, or undercover behind the cafe if the weather is being dramatic.\n\n"
                    "It serves Redfern, Waterloo, Surry Hills and nearby spots. There’s parking on Chalmers St and underground at Woolworths, and Redfern Station is about 700m away."
                ),
            }
        ]
        repeated = client.post(
            "/api/chat",
            json={"session_id": repeat_location_session, "message": "redfern"},
        ).json().get("reply", "")
        preview = " ".join(repeated.split())[:180]
        print(f"location-repeat: reply={preview}")
        if repeated.lower().count("redfern park") > 0 or "redfern st" in repeated.lower() or "700m" in repeated.lower():
            failures.append("location-repeat: repeated full Redfern logistics block")
        if "Redfern it is" not in repeated or "strength, fitness, weight loss" not in repeated:
            failures.append("location-repeat: did not commit to Redfern and advance")

        location_choice_session = f"review-smoke-location-choice-{uuid.uuid4().hex[:8]}"
        app.conversations[location_choice_session] = [
            {"role": "assistant", "content": "Camperdown or Redfern? 🙂"},
        ]
        chosen_location = client.post(
            "/api/chat",
            json={"session_id": location_choice_session, "message": "Redfern"},
        ).json().get("reply", "")
        preview = " ".join(chosen_location.split())[:180]
        print(f"location-choice: reply={preview}")
        if "Redfern it is" not in chosen_location:
            failures.append("location-choice: did not accept selected location")
        if "Camperdown or Redfern" in chosen_location or "Which one is closer" in chosen_location:
            failures.append("location-choice: re-asked the location menu")

        generic_repeat_session = f"review-smoke-generic-repeat-{uuid.uuid4().hex[:8]}"
        repeated_block = (
            "There are two main training spots: Camperdown and Redfern.\n\n"
            "Camperdown: The Barracks at Camperdown Tennis & Oval, Mallett St, Camperdown NSW 2050. Good for Camperdown, Newtown, Stanmore and nearby Inner West suburbs.\n\n"
            "Redfern: Redfern Park, Redfern St, Redfern NSW 2016. Good for Redfern, Waterloo, Surry Hills and nearby spots.\n\n"
            "Which one is closer for you?"
        )
        app.conversations[generic_repeat_session] = [{"role": "assistant", "content": repeated_block}]
        guarded = app.prevent_repetitive_reply(repeated_block, "locations again", generic_repeat_session)
        if guarded == repeated_block or "Mallett St" in guarded or "Redfern St" in guarded:
            failures.append("generic-repeat: repeat guard did not replace duplicated block")

        contact_session = f"review-smoke-contact-progress-{uuid.uuid4().hex[:8]}"
        first_contact = client.post(
            "/api/chat",
            json={
                "session_id": contact_session,
                "message": "I'm Sam, mobile 0412 345 678, keen to try evenings in Redfern.",
            },
        ).json().get("reply", "")
        preview = " ".join(first_contact.split())[:180]
        print(f"contact-progress-first: reply={preview}")
        if "main thing you want help with" in first_contact.lower():
            failures.append("contact-progress-first: kept qualifying after contact capture")
        if first_contact.count("?") > 1:
            failures.append("contact-progress-first: asked too many questions after contact capture")

        app.conversations[contact_session].append(
            {
                "role": "assistant",
                "content": "The team can follow up by SMS or call and point you to the right session.",
            }
        )
        repeated_handoff = app.prevent_repetitive_reply(
            "The team can follow up by SMS or call. Can you send your phone number again?",
            "sounds good",
            contact_session,
        )
        preview = " ".join(repeated_handoff.split())[:180]
        print(f"contact-progress-repeat: reply={preview}")
        if "phone number again" in repeated_handoff.lower() or "sms or call" in repeated_handoff.lower():
            failures.append("contact-progress-repeat: repeated contact/handoff request")
        if repeated_handoff.count("?") > 0:
            failures.append("contact-progress-repeat: kept asking questions after handoff was already clear")

    if failures:
        print("\nFAIL")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("\nPASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
