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
            "Quick options: - Free 1-day trial — easiest way to try a session. - 28-day Kickstarter — 4-week run with assessment + nutrition."
        )
        if "\n- Free 1-day trial" in quick_options or "Quick options:\n" in quick_options:
            failures.append("quick options should stay inline")

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

    if failures:
        print("\nFAIL")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("\nPASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
