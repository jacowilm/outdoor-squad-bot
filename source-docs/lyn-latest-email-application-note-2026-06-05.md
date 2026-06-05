# Lyn latest email application note — 2026-06-05

Source email: Gracelyn Angalao / Lyn, `Updated Bot FAQ + Member Reviews`, received 2026-06-04 18:54 UTC.

Usable material from Lyn's email and linked source files:
- Updated FAQ source: `source-docs/lyn-updated-faq-2026-06-04.txt`.
- Member review / brand voice source: `source-docs/member-reviews-brand-voice-2026-06-04.txt`.
- Family pricing guardrail: no reduced membership prices, percentage discounts, or "$X off" family offers; value-stack with possible bonuses such as extra sessions, movement screens, nutrition consults, parent perks, or other add-ons after a human chat.
- Practical FAQ details: friend/partner welcome, rain/undercover areas, cold-weather layers, bring drink bottle/towel/mat.
- Suitability FAQ details: over-50 suitability, functional strength, mobility, balance, long-term health.
- Member-review voice: welcoming, friendly, technique-focused, supportive, variety, real routine/community, not glossy transformation hype.

Applied to deterministic bot paths:
- `bring a friend / partner` now answers directly and preserves Lyn's value-stack/no-discount guardrail.
- `rain / wet weather` now answers with undercover-area and layers guidance.
- `over 50 / too old` now answers with long-term functional-strength suitability.
- Existing family-discount, review/proof, beginner, and injury paths remain grounded in Lyn/Nick source material.

Verification:
- `python -m py_compile app.py run_review_build_smoke.py` passed.
- Targeted FastAPI TestClient checks passed for bring-friend, rain, over-50, and family-discount paths.
