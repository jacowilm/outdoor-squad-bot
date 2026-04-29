# The Outdoor Squad — Call Prep

## Goal for the Call
Move from interest to a paid pilot.

This is not a generic discovery chat. The aim is to:
1. confirm their exact workflow and pains
2. show a working solution tailored to them
3. agree the fastest pilot scope
4. leave with a clear next step, ideally a paid trial

---

## What They Already Told Us
The lead wants a chatbot that can:
- field leads and general enquiries
- direct people to a free trial signup or a booked call
- ask qualifying questions
- handle objections
- support nutrition upsells
- automate some follow-up

They also mentioned Zoovr and offered to share a chat example.

Source:
- `memory/2026-04-09.md`
- `independence/scripts/reply_classifications.json`

---

## Call Agenda (15-20 min)

### 1. Frame the call (1-2 min)
Use:
> Thanks for jumping on. I wanted to make this practical, so instead of just talking through ideas, I’ve prepared a simple prototype flow based on what you mentioned. I’d love to quickly confirm your current enquiry flow, show you how this could work for Outdoor Squad, and then see if it makes sense to pilot.

### 2. Understand the current enquiry flow (4-5 min)
Ask:
- Where do most new enquiries come from right now, website, Instagram, phone, ads, somewhere else?
- What are the most common questions people ask before joining?
- What usually happens after someone shows interest today?
- Where are leads currently getting lost or going cold?
- Do you want the bot mainly to qualify, to book, or to do both?

### 3. Clarify the open implementation details (4-5 min)
Need answers to:
- What exact qualifying questions should the bot ask?
- What is the preferred CTA, free intro class, free trial, consultation, or something else?
- Should the bot book directly into a calendar, or just capture details and notify the team?
- What objections come up most often?
- How should nutrition be introduced, immediately, later, or only for existing members?

### 4. Show the demo (3-5 min)
Demo path:
- visitor asks a simple FAQ
- bot answers in Outdoor Squad tone
- bot identifies interest and asks qualifying questions
- bot moves user toward the free intro class CTA
- bot captures lead details
- bot optionally surfaces nutrition support / upsell logic

### 5. Close toward pilot (3-4 min)
Use:
> The main thing I’d suggest is we keep this really lean. We don’t need to build a giant system first. We can launch a focused version that handles the most common enquiries, qualifies leads, and pushes people into the right next step, then improve it from real conversations.

Then offer a simple pilot.

---

## Demo Flow to Show

### Demo 1 — New lead
User:
> Hey, I’m interested but I’m not very fit yet. What do you offer?

Bot should:
- reassure beginner-friendliness
- explain outdoor group training
- mention Inner West locations
- offer free intro class
- ask 1-2 qualifying questions

### Demo 2 — Qualification
User:
> I want to lose weight and get back into training. I work full time so evenings are best.

Bot should:
- acknowledge goals
- ask relevant qualifiers like training history, location, schedule preference
- recommend next step
- move toward booking / trial CTA

### Demo 3 — Objection handling
User:
> I’m not sure if group training is for me.

Bot should:
- reduce risk
- position free intro as low-pressure
- explain coach support and all-level suitability
- ask if they want the link or to speak to a coach

### Demo 4 — Nutrition upsell
User:
> Do you also help with food?

Bot should:
- explain practical nutrition support
- connect it to results
- offer next step without sounding pushy

---

## Discovery Questions to Ask
Keep these conversational, not interrogation-style.

### Business / funnel
- How many new enquiries do you get in a normal week?
- Which channels bring the best-fit leads?
- Do leads usually convert better when someone replies quickly?
- Are missed or delayed replies a real issue now?

### Qualification
- What makes someone a good lead versus a bad-fit lead?
- Which 3-5 questions do you wish every new lead answered upfront?
- Do you want different flows for beginners, existing members, PT prospects, and nutrition prospects?

### Booking / conversion
- What is the ideal next step, free intro class, phone call, form, or direct message?
- Do you want the assistant to book directly or just tee up the handoff?
- What would make this feel like a win after 30 days?

### Operations
- Who follows up today?
- Where do conversations currently live?
- Do you want this only on the website first, or also on Instagram/WhatsApp later?

---

## Recommended Offer for Tomorrow
Go in with a simple paid pilot.

### Option A — Best option
**Paid pilot, 2 weeks**
- chatbot tailored to Outdoor Squad
- website embed
- FAQ + lead qualification
- free intro / booking CTA
- lead capture
- one round of refinement from real chats

Suggested structure:
- setup fee + small monthly support
- or one flat pilot fee

### Option B — If price resistance shows up
**Fast starter build**
- single use case only: qualify and convert website enquiries
- no complex integrations yet
- prove value first, expand later

### Positioning
Do not sell “AI”. Sell:
- faster responses to enquiries
- fewer missed leads
- more free intro bookings
- less admin load
- better follow-up consistency

---

## Suggested Pricing Framing
Don’t overcomplicate this.

Say something like:
> I’d suggest we start with a lean pilot, get this live around your core enquiry flow, and use real conversations to improve it. That way you’re not paying for a bloated build before we know what actually converts best.

If pressed, give a simple structure:
- setup/build fee
- monthly support/optimization fee

If needed, offer a founder-friendly early pilot rate in exchange for speed and feedback.

---

## What a Good Outcome Looks Like
Best case:
- they like the prototype
- they confirm the desired flow
- they agree on pilot scope
- they commit to next step and budget

Acceptable case:
- they give the exact qualification questions + CTA + objections
- they send the Zoovr example
- we agree on a revised prototype and a follow-up date

Bad outcome to avoid:
- vague “sounds cool, send me something” with no concrete next step

---

## Closing Script
Use one of these.

### Direct close
> Based on what you’ve said, I think the smartest move is a lean pilot focused on handling enquiries, qualifying leads, and moving people into the free intro flow. If you want, I can tighten this around your exact process and get version one in place fast.

### Soft close
> If this feels aligned, the next step would be for me to lock the flow around your real enquiry process and launch a focused pilot rather than overbuilding it.

### If they hesitate
> Totally fine. In that case, the most useful thing would be for you to send me the current questions, CTA, and any example chats you like, and I’ll sharpen the prototype around that.

---


## 20:00 Revenue Pulse Update — Close Discipline

Nicholas has confirmed **Thursday 2pm**. The next highest-leverage move is not more generic demo polishing; it is keeping the call anchored to a paid pilot. Use `PAID-PILOT-CLOSE-PACKET-2026-04-28.md` as the close sheet.

Pilot anchor to use if pricing comes up:
- **$299 setup for a 14-day pilot**
- then **$149/month only if useful enough to keep**
- fallback: **$199 narrow website FAQ + qualification + free intro handoff** if price resistance appears

Success condition for the call: paid pilot agreed, or exact scope + decision date agreed. Do not accept vague “send me something” without a follow-up date.

## Checklist Before the Call
- [ ] Bot runs without breaking
- [ ] 3-4 demo prompts tested
- [ ] Free trial / booking CTA decided for the demo
- [ ] One sentence offer ready
- [ ] Price framing ready
- [ ] Closing question ready
- [ ] One-page paid pilot proposal ready if he asks for details
- [ ] Decision-date fallback ready if he does not commit live
- [ ] Notes doc open during the call

---

## My Recommendation
Go in aiming to close a paid pilot.

Don’t drift into generic “custom AI solutions” chat. Keep it grounded in:
- enquiries
- qualification
- conversion
- follow-up
- time saved
- missed leads prevented

That’s the sale.

## 2026-04-29 08:00 follow-up discipline
A post-call follow-up pack now exists at `POST-CALL-FOLLOW-UP-2026-04-29.md`. If Nicholas does not agree/pay live, send the matching follow-up immediately so the thread asks for one of three outcomes: start the paid pilot, send launch materials, or commit to a Friday decision point.

## 2026-04-29 09:00 live call run sheet
Use `CALL-RUN-SHEET-2026-04-30.md` during the call. It keeps the sequence simple: short framing, five demo prompts, five discovery questions, one paid-pilot close, and exact paths for think-about-it / price-resistance / feature-creep objections.

## 2026-04-29 18:30 payment collection guardrail
A payment collection pack now exists at `PAYMENT-COLLECTION-READY-2026-04-29.md`. The funnel is weak if Nicholas says yes but there is no approved way to collect the $299 setup fee. If he is ready and no payment method is confirmed, ask Jacobo for Stripe/payment link, bank transfer, PayPal, or invoice details first. Do not improvise payment details, and do not share source/demo/install access until payment is confirmed.
