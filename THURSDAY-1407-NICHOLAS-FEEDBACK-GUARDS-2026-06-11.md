# Thursday 14:07 Nicholas feedback guard update — 2026-06-11

Status: Nicholas replied with high-value review feedback, not a new sales objection. Highest-leverage action was to harden the paid Outdoor Squad bot against the exact trust failures he is noticing before asking for anything else.

## Decision

Treat Nicholas's examples as proof-risk, not polish:

- Timetable/class-time answers must be grounded in the current master timetable; never invent class times.
- Medical-specific mentions such as diabetes should route to a cautious human/health-practitioner-aware path, not generic beginner encouragement.
- Privacy/data questions need a clear, plain-English answer.
- Returning/lapsed members need a re-entry path instead of generic new-prospect copy.
- Group-size and qualifications answers remain deterministic guards because those are high-trust questions.

## Changes made

- Updated `app.py` operating facts with the current master timetable and an explicit anti-hallucination timetable guardrail.
- Added deterministic timetable filtering so specific asks like “Power'N'Pilates in Redfern on Thursday” return the exact known slot.
- Added local guard replies for:
  - privacy/personal details;
  - diabetes / blood-sugar medical context;
  - returning/lapsed members;
  - exact timetable/day/location/class queries.
- Reused the timetable guard in both contextual and fallback paths.

## Verification

Ran a local TestClient guard smoke for:

- Thursday Redfern Power'N'Pilates → returned Thursday 6:00am Power'N'Pilates at Redfern and explicitly refused to invent availability.
- Sunday classes → returned no Sunday sessions and named Saturday options.
- Diabetes → routed to Nick/Lyn + healthcare guidance, no medical prescription.
- Privacy → explained contact details are only for Outdoor Squad follow-up and offered direct email.
- Returning member → routed to re-entry check with Nick/Lyn.
- Group size → no invented class count; SPT cap of 4 preserved.
- Qualifications → answered with coach credentials.

Full `run_review_build_smoke.py review` still cannot pass in this local cron environment because review readiness env/admin/AI config are not configured here; that was pre-existing environment state, not introduced by this change.

## Next sales move

Send Nicholas a concise reply: acknowledge that the concrete examples are useful, say the highest-risk ones have been turned into deterministic guards, and ask him to keep sending failures in example format. Do not ask for payment/renewal in the same message; this is paid-proof retention work.
