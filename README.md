# The Outdoor Squad — AI Chatbot

Custom AI assistant for The Outdoor Squad fitness community (Inner West Sydney).

## Features
- 💬 FAQ handling (classes, locations, pricing, nutrition)
- 🎯 Lead qualification (fitness goals, experience level)
- 📅 Free trial booking guidance
- 💪 Objection handling
- 🥗 Nutrition program upselling
- 📋 Contact detail capture

## Quick Start
```bash
pip install -r requirements.txt
export OPENAI_API_KEY=your_key
uvicorn app:app --reload
```

## Embed Widget
Add to any website:
```html
<script src="https://your-deploy-url/widget.js"></script>
```

## Demo
Visit `http://localhost:8000` for the full demo page.

## API
- `POST /api/chat` — Send a message, get a reply
- `GET /admin` — Protected owner dashboard for leads, metrics, and redacted conversation review
- `GET /api/leads` — Protected captured-leads JSON
- `GET /api/metrics` — Protected success-metrics JSON
- `GET /api/conversation-logs` — Protected redacted transcript review JSON

Built by [AI Sprints](https://aisprints.pages.dev)

## Current Revenue Offer — Canonical after 2026-05-12 trust reset

Use this positioning for Nicholas / Outdoor Squad until first revenue is won or explicitly changed:

- **$349 one-off** for one practical Outdoor Squad enquiry-flow build.
- **No required monthly retainer.** Future changes, hosting/support, or deeper integrations are optional later decisions only.
- **Scope:** one reusable/linkable lead-capture flow usable from website, Instagram bio, Linktree, Facebook, etc.
- **Boundary:** do not promise native Instagram/Facebook/WhatsApp automation in the same first scope.
- **Payment/access guardrail:** no source, install snippet, hosted access, or repo access before payment is confirmed.

Why this changed: Nicholas explicitly objected to the recurring $149/month framing and expected the original value to be a one-off build. The old $299/$99/$149 pilot ladder created pricing confusion; do not reintroduce it on the Thursday call unless Jacobo explicitly chooses a smaller fallback.

Current next action after Friday 2026-05-15 16:27 pulse: **payment confirmed by Nicholas SMS (“Transfer done”)**. Stop payment chasing and cold outreach. Build the first Outdoor Squad working enquiry flow from the paid activation checklist, source PDFs/OCR, and Lyn FAQ brief; fold in Lyn’s completed FAQ when it arrives. Keep scope to the $349 one-off: one practical/linkable enquiry flow, no native social/WhatsApp automation promise, no recurring-fee language unless optional support is requested later.

2026-05-15 20:00 paid-delivery update: success-metric plumbing is now in place (`events.jsonl`, `/api/event`, `/api/metrics`, route/outcome tags, widget open/quick-reply/send tracking). Keep building toward a measurable first handoff; remaining install blockers are final booking URL(s), lead-summary destination, embed location, hosting/API ownership, and Lyn’s completed FAQ answers.

2026-05-16 security/admin update: Square should only embed the public widget script. The owner dashboard now lives on the bot backend at `/admin` and is protected with HTTP Basic auth using `OUTDOOR_SQUAD_ADMIN_USERNAME` + `OUTDOOR_SQUAD_ADMIN_PASSWORD`. Leads, metrics, and conversation-log APIs are protected by the same auth. Set the password in Nicholas/The Outdoor Squad-owned hosting before any production install; do not place the dashboard on an unprotected Square page.

2026-05-16 Lyn FAQ update: Lyn replied with the completed FAQ/details document. Saved it to `source-docs/private-faq/bot-faq-completed-2026-05-16.txt` and updated the app so `source-docs/private-faq/*.txt` is loaded into the AI source chunks alongside OCR/source docs and the curated knowledge base. Chunking now preserves long plain-text FAQ files instead of truncating after the first source block. This removes the previous FAQ blocker; remaining final-install blockers are booking URL(s), lead-summary destination, Square embed location, and Nicholas-owned hosting/API/admin credentials.

2026-05-17 lead-quality update: saved leads now require a phone or email before being written to `leads.json`. Vague replies like "idk" still get a helpful local-tone response, but they cannot create fake owner-dashboard leads. QA-only lead/event/conversation entries from the 2026-05-16 smoke sessions were removed so Nicholas/Lyn review a clean admin surface.

2026-05-18 revenue/proof update: use `MONDAY-0700-REVIEW-TO-REFERRAL-PACKET-2026-05-18.md` as the next client-facing operating packet after production/review preflight passes. The goal is no longer closing payment; it is review approval, final install inputs, one short testimonial, and one warm referral from the first paid customer. Do not send the review note while admin, trial link, lead-summary destination, Nicholas-owned AI billing/provider keys, or a fresh deploy/review smoke pass are unresolved.

## Required production environment
```bash
OUTDOOR_SQUAD_OPENAI_API_KEY=outdoor_squad_owned_key
OUTDOOR_SQUAD_OPENAI_MODEL=gpt-5-mini
OUTDOOR_SQUAD_GEMINI_API_KEY=optional_outdoor_squad_owned_fallback_key
OUTDOOR_SQUAD_GEMINI_MODEL=gemini-2.5-flash
OUTDOOR_SQUAD_DEPLOYMENT_MODE=handoff
OUTDOOR_SQUAD_ADMIN_USERNAME=outdoorsquad
OUTDOOR_SQUAD_ADMIN_PASSWORD=strong_unique_password
OUTDOOR_SQUAD_TRIAL_LINK=https://momence.com/The-Outdoor-Squad-/membership/Squad-Intro-Class/263360
OUTDOOR_SQUAD_HUMAN_EMAIL=innerwest@outdoorsquad.com.au
OUTDOOR_SQUAD_HUMAN_PHONE=0402 439 361
OUTDOOR_SQUAD_LEAD_SUMMARY_EMAIL_TO=innerwest@outdoorsquad.com.au
OUTDOOR_SQUAD_SMTP_HOST=smtp.your-provider.com
OUTDOOR_SQUAD_SMTP_PORT=587
OUTDOOR_SQUAD_SMTP_USER=notifications@outdoorsquad.com.au
OUTDOOR_SQUAD_SMTP_PASSWORD=provider_app_password
OUTDOOR_SQUAD_SMTP_FROM=notifications@outdoorsquad.com.au
OUTDOOR_SQUAD_LEAD_SUMMARY_PHONE_TO=+61402439361
OUTDOOR_SQUAD_LEAD_SUMMARY_WEBHOOK_URL=https://hooks.zapier.com/hooks/catch/...
OUTDOOR_SQUAD_LEAD_SUMMARY_WEBHOOK_SECRET=shared_random_secret
SUPABASE_URL=https://your-project-ref.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your_service_role_key
```

## Lead summary delivery setup

Captured leads always appear in the protected `/admin` dashboard and `/api/leads.csv`. For live handoff, configure at least one push destination before public install:

- Email summaries: set `OUTDOOR_SQUAD_LEAD_SUMMARY_EMAIL_TO` plus SMTP settings. The value can be one inbox or a comma-separated list.
- Phone summaries: set `OUTDOOR_SQUAD_LEAD_SUMMARY_PHONE_TO` plus `OUTDOOR_SQUAD_LEAD_SUMMARY_WEBHOOK_URL`. The webhook receives JSON with `destination_phone`, `summary_text`, and structured lead fields, so it can be wired through Make/Zapier/Twilio/WhatsApp/SMS without changing the bot.

Use Nicholas's existing Outdoor Squad inbox and mobile for the first install: `innerwest@outdoorsquad.com.au` and `+61402439361`. Email summaries still need SMTP credentials. Phone summaries still need a webhook wired through Make/Zapier/Twilio/WhatsApp/SMS. Do not put webhook secrets or SMTP passwords in email/Discord; set them directly in the host environment.

## Meal plan delivery setup

The source docs define the Free 5-Day High-Protein Australian Meal Plan as an email lead magnet, but the actual downloadable file/link and email automation endpoint are not in this repo. Until those are provided, the bot must not claim it sent the meal plan. It should capture the visitor's email and let Nicholas/Lyn/the configured lead-summary flow handle fulfilment.

## Preflight before client demo or handoff
- `/api/health` must report `ai_configured: true`.
- For Jacobo-hosted review, set `OUTDOOR_SQUAD_DEPLOYMENT_MODE=review`. This allows our temporary API/hosting for review only, but still requires protected admin auth before sharing the link.
- For final handoff, set `OUTDOOR_SQUAD_DEPLOYMENT_MODE=handoff`. `/api/health` must report `handoff_ready: true` before transferring ownership or installing publicly.
- The host should use `OUTDOOR_SQUAD_OPENAI_API_KEY` for the preferred production key, with `OUTDOOR_SQUAD_GEMINI_API_KEY` available as a real secondary AI provider if Nicholas wants provider fallback. `OPENAI_API_KEY` and `GEMINI_API_KEY` are local/dev fallbacks only. For final handoff these must be Nicholas/The Outdoor Squad-owned billing, not Jacobo-owned billing.
- `/api/health` should also show `api_key_sources` containing a Nicholas-owned provider key, `admin_configured: true`, and `trial_link_configured: true` before a client-ready install.
- `/api/health` should report `storage_backend: "supabase"` before a client-ready install. Local files are fallback only.
- `/api/health` must report `lead_summary_delivery_configured: true` before final handoff. `handoff_ready` stays false until at least one real lead-summary delivery path is configured.
- Run `python3.11 run_review_build_smoke.py` before sending a progress update. It checks beginner/nervous, 28-Day Kickstarter/SPT, YTP/teen, pricing, injury/limitation, oddball/skeptical, and contact-detail capture, then restores QA data files so the owner dashboard stays clean.
- Any AI-path response that says the backend cannot be reached means the demo is not ready to show, even if the local contact-detail handler still works.
- 2026-05-17 evening update: the backend now tries configured AI providers in order: OpenAI first, then Gemini. This is still the same file-grounded Robo-Nick agent path; it is not the deterministic demo fallback.
- 2026-05-17 20:00 update: AI provider calls now get one retry before moving on/failing, after a transient Gemini exception caused the Kickstarter/SPT review path to show the backend-unavailable message. `python3.11 -m py_compile app.py run_review_build_smoke.py` and the seven-path smoke now pass locally with Gemini, 117 source chunks, and restored QA data files.

## Supabase storage
- Apply `supabase_schema.sql` to the target Supabase project.
- Backfill any local `leads.json`, `events.jsonl`, and `conversation_logs.jsonl` data with `python3 migrate_local_data_to_supabase.py`.
- Session history now persists in `outdoor_squad_conversations`, so admin review survives Render restarts once Supabase is configured.
