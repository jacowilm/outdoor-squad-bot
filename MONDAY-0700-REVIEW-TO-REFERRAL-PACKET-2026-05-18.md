# Outdoor Squad - Review-to-Referral Packet (2026-05-18 07:00)

Purpose: convert the paid Outdoor Squad build from a one-off $349 delivery into proof, a testimonial, and the next warm lead. Do not send this until the production/review preflight passes.

## Funnel read

- Outdoor Squad is paid at $349. This is no longer a payment-chase funnel.
- Lyn's FAQ/detail document is integrated. Last known full review smoke passed on 2026-05-17 with Gemini fallback, but the 2026-05-18 shell run did not finish cleanly, so deploy/review smoke must be rerun before any client note.
- Cold outreach has volume but weak buyer signal: 506 total prospects, 367 recorded sent, 21 delivery failures/bounces in current state, and 137 raw unsent records. Recent sends have not produced a positive cold buyer reply.
- The strongest niche signal is owner-led fitness/community businesses because the first payer came from that world and the product can be shown as a linkable enquiry flow, not vague automation.
- The biggest trust gap is not copy. It is showing Nicholas/Lyn a review link before the review host has AI configured, admin protected, and QA data clean. Nicholas-owned billing/provider keys are required for final handoff, not the temporary review link.

## Send gate

Send a client progress/review note only after all are true:

- `/api/health` shows `ai_configured: true`.
- `/api/health` shows `admin_configured: true`.
- `/api/health` shows `deployment_mode: "review"` and `review_hosted_by_ai_sprints: true`.
- `python3.11 run_review_build_smoke.py` passes in the review/deploy environment.
- The admin surface is protected and QA data is clean.
- The client note is explicit that final booking URL, lead destination, Square embed location, and Nicholas-owned hosting/API/admin setup are still needed before public install/handoff.

## Client note after gate passes

Subject: Outdoor Squad review link + final install details

Hey Nicholas and Lyn,

I have the first Outdoor Squad review build ready enough for you to test.

It now uses the FAQ/details you sent through, answers as the Outdoor Squad enquiry flow rather than a generic bot, captures useful lead details only when someone shares phone/email, and keeps the owner dashboard separate from the public Square page.

Before I install it publicly, can you send me these final details?

1. The exact booking/free-trial URL you want the main button to use.
2. The email address where lead summaries should go.
3. The Square page/location where you want the widget embedded.
4. Confirmation that the hosting/API/admin setup should sit under Outdoor Squad-owned billing, unless you want me to quote optional hosted support separately later.

Once you have reviewed it, the most useful feedback is: "would I be comfortable linking this from the website/Instagram bio today?" If yes, I will install the public version and give you the admin/login handoff.

## Proof/referral ask after acceptance

Use only after Nicholas/Lyn say the review build is useful or approve install.

> Glad it is useful. Tiny ask: once it is live, could you send me one sentence on what problem this solves for Outdoor Squad? Something like "it gives people a clearer first step and sends us better enquiry details" is enough.
>
> Also, if you know one other owner-led fitness/coaching/community business that loses enquiries in DMs or website forms, an intro would be more useful than broad cold outreach.

## Next warm ICP

Prioritize businesses that match the Outdoor Squad proof pattern:

- owner-led fitness, coaching, youth sport, martial arts, Pilates, yoga, outdoor groups, dance, swim schools;
- website/social enquiries where people ask repeated beginner/price/suitability questions;
- clear trial/session CTA;
- owner or small team cares about lead quality but does not want a platform rebuild;
- can buy a one-off linkable enquiry flow without a retainer.

Do not lead with emergency trades unless a live reply arrives. The current proof is fitness/community enquiry routing, so use that proof while it is fresh.
