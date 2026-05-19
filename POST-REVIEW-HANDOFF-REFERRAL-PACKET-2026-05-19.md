# Outdoor Squad - Post-Review Handoff + Referral Packet (2026-05-19)

Purpose: be ready the moment Nicholas or Lyn reviews the bot. No extra nudge is needed while they are still quiet; this is the response kit for approval, feedback, or install readiness.

## Current status

- Paid build: confirmed at $349 one-off.
- Live review app: https://outdoor-squad-bot.onrender.com
- Health check: /api/health reported review_ready true on 2026-05-19 after the brand-voice fix.
- Brand voice fix: pushed in commits 5d12f9c and 9753eaa, then live QA found two more polish issues that are being guarded in the app formatter.
- Nicholas/Lyn do not need to know this was fixed after the first review link; if they have not reviewed yet, it simply improves the first impression.

## Do not send yet

Do not send a nudge solely to mention fixes. Send only if:
- Nicholas/Lyn reply;
- Jacobo explicitly wants a light check-in later;
- or the review link has been quiet long enough that a single low-pressure reminder is useful.

## Final live QA checklist before any client-facing follow-up

- Open the review link on desktop and mobile.
- Confirm the widget loads without layout overlap.
- Send one nervous beginner message.
- Send one brand/personality message.
- Send one SPT/Kickstarter message.
- Send one YTP parent message.
- Confirm no reply says the bot can book, email, SMS, or confirm anything it cannot actually do.
- Confirm no reply expands SPT as anything except Semi-Private Personal Training.
- Confirm no lone bullet/dash formatting artifacts.
- Confirm /api/health still shows review_ready true.

## If they approve or say it looks good

Reply with this:

Subject: Outdoor Squad install details

Hey Nicholas and Lyn,

Glad this is feeling useful.

To install it cleanly, I just need the final handoff details:

1. The exact free-trial / booking URL you want the main path to use.
2. The email address where lead summaries should go.
3. Where on the Square site you want the widget embedded.
4. The admin username/password you want for the owner dashboard, or confirmation you want me to set a temporary one and hand it over.
5. Whether final hosting/API billing should sit under Outdoor Squad-owned accounts now, or whether you want me to quote optional hosted support separately.

Once I have those, I can prep the public install version and keep the dashboard separate from the Square page.

## If they give feedback

Reply structure:

- Acknowledge the exact point.
- Say whether it is a copy/knowledge tweak, routing tweak, or handoff/install detail.
- Avoid defending the bot.
- Ask for only the missing input needed to fix it.

Useful line:

That makes sense. I can tune that directly. Is the main issue the wording/tone, the factual answer, or where it sends the person next?

## If they ask what changed / what it does

Use this short summary:

It answers common first-contact questions in the Outdoor Squad voice, routes people toward the right next step, and captures useful lead details when someone shares phone/email. It is meant to reduce repetitive enquiry handling without pretending to replace real Nick for sensitive or high-touch conversations.

## Testimonial ask after install approval

Use only after they approve the review build or public install:

Tiny ask: once this is live, could you send me one sentence on what it helps Outdoor Squad with?

Something plain is perfect, for example: "It gives new people a clearer first step and sends us better enquiry details."

## Warm intro ask after testimonial

Use only after they are happy:

Also, if you know one other owner-led fitness, youth sport, coaching, Pilates, yoga, martial arts, or community business that gets repetitive enquiries, a warm intro would be more useful than broad cold outreach.

## Install/handoff boundary

Final public handoff should not leave Jacobo owning ongoing costs by default.

Required before public install:
- Outdoor Squad-owned API/provider key or explicit hosted-support agreement.
- Admin password set and transferred safely.
- Booking URL confirmed.
- Lead summary destination confirmed.
- Square embed location confirmed.
- Supabase/storage owner plan confirmed if they want retained dashboard history.

## Internal next action

If no client reply arrives:
1. Keep the bot live and stable.
2. Use the Outdoor Squad proof to build the reusable fitness-studio template.
3. Continue Sydney local-business call sprint separately.
4. Do not burn trust with unnecessary follow-ups.

