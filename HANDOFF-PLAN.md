# Outdoor Squad Bot — Handoff Plan

Goal: deliver a polished website-embeddable chatbot that Nicholas/Lyn can manage after the one-off $349 build.

## Delivery constraints
- This is not a recurring managed service.
- Jacobo-hosted review is allowed temporarily so Nicholas/Lyn can approve the first version before we ask for handoff setup.
- Avoid Jacobo-owned infrastructure as the long-term dependency unless Nicholas explicitly buys support later.
- Jacobo is not responsible for ongoing API, hosting, domain, platform, or usage costs.
- Any paid API/hosting must be under Nicholas/The Outdoor Squad-owned accounts and billing before final handoff.
- Final handoff should include simple instructions, source files, and clear ownership of where leads/settings live.

## Preferred handoff options

### Option A — Nicholas-owned small hosting + Square embed (recommended)
- Deploy the FastAPI app to a Nicholas-controlled account/service with Nicholas-owned billing.
- Use Nicholas-owned API keys because this product is an AI agent.
- Embed `widget.js` on the Square-hosted public website.
- Keep the owner/admin dashboard on the backend host at `/admin`, protected by username/password; do not expose leads or logs through a hidden Square page.
- Give Nicholas/Lyn simple admin instructions for reviewing leads, metrics, redacted conversation logs, and updating the knowledge base.
- Best balance: keeps current custom bot, preserves ownership, easy to explain.

### Option B — Static widget + third-party form/handoff only
- Use a lighter embedded front-end with deterministic FAQ/routing logic and send leads to email/form.
- Less server maintenance, but weaker conversational quality and no AI unless paired with an owned backend.

### Option C — Transfer repo + install guide only
- Give Nicholas the source and instructions to run/deploy.
- Lowest ongoing responsibility for Jacobo, but highest friction for Nicholas/Lyn.

## Before handoff
- Polish visible demo and Robo-Nick tone.
- Keep knowledge base editable in one obvious file.
- Add environment variable examples without secrets.
- Add admin/lead export or email-forwarding path.
- Provide Square embed snippet.
- Set `OUTDOOR_SQUAD_ADMIN_PASSWORD` to a strong unique password in Nicholas-owned hosting.
- Document how Lyn can update FAQs/source answers.
- Include an explicit cost/ownership note: hosting/API costs are Nicholas/The Outdoor Squad responsibility, not Jacobo/AI Sprints.

## Production preflight
- For review, set `OUTDOOR_SQUAD_DEPLOYMENT_MODE=review`; `GET /api/health` must show `ai_configured: true`, `admin_configured: true`, and `review_hosted_by_ai_sprints: true`.
- For final handoff, set `OUTDOOR_SQUAD_DEPLOYMENT_MODE=handoff`; `GET /api/health` must show `handoff_ready: true`, a Nicholas-owned provider in `api_key_sources`, `admin_configured: true`, and `trial_link_configured: true`.
- OpenAI remains the preferred provider; Gemini can be configured as a real secondary provider for failover/review. If `api_key_sources` only contains `OPENAI_API_KEY` or `GEMINI_API_KEY`, treat it as a development/test setup unless Jacobo has explicitly sold ongoing hosting/support.
- Run `python3.11 run_review_build_smoke.py` from this folder after setting production-like env vars. Do not send Nicholas/Lyn a ready-for-review note until it passes.
- Keep the Square public install to the widget script only. Leads, metrics, and redacted conversation logs stay on the protected backend admin surface.

## Review-to-referral sequence
- After production preflight and a fresh deploy/review smoke pass, use `MONDAY-0700-REVIEW-TO-REFERRAL-PACKET-2026-05-18.md` for the client progress note.
- Ask only for the final install details: booking/free-trial URL, lead-summary email, Square embed location, and ownership of hosting/API/admin setup.
- After Nicholas/Lyn approve the review build or public install, ask for one short testimonial sentence and one warm intro to another owner-led fitness/coaching/community business.
- Do not restart broad cold outreach before this proof/referral step is attempted unless a new urgent buyer reply arrives.

## AI runtime assumption
- This product is an AI agent. AI connectivity is a baseline requirement, not optional.
- During development and review, Jacobo/AI Sprints may use its own API key for testing.
- At handoff, Nicholas/The Outdoor Squad must connect their own API key/account/billing.
- Reliability should be handled with provider/model fallback where practical, not by replacing the agent with canned FAQ answers.
