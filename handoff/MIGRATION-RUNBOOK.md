# Robo-Nick — Handoff / Migration Runbook

**Goal:** move the live bot to 100% Nicholas-owned accounts (GitHub, Render,
Anthropic, Supabase) with his billing, then retire the AI Sprints review service.
Full transfer — he owns the product, data, and costs. No custom domain (the bot is
an embedded widget, so its URL is just a backend endpoint).

**Time:** ~30–45 min on a screen-share. Jacobo drives the technical steps; Nicholas
is present to create accounts and enter his card. Nothing here needs a developer
afterward except occasional knowledge-base edits.

**Cost to Nicholas (~$15–40/mo):** Render Starter ~$7 + Anthropic (Haiku, a few $ at
low volume) + (optional) OpenAI + Supabase (free tier is fine to start).

---

## 0. Before the call — Jacobo preps (10 min, no client accounts needed)

1. Build the clean client repo (product files only, no internal sales notes / history):
   ```bash
   cd <repo>
   bash handoff/build-clean-repo.sh
   ls handoff/client-repo      # sanity check: product files ONLY
   ```
   Confirm there is **no** `*CLOSE*`, `*PAYMENT*`, `*PROPOSAL*`, `*CALL*`, or
   `HANDOFF-PLAN.md` in the output.
2. Generate a strong admin password (keep it for step 4 + the handover): `openssl rand -base64 30`.
3. Have `handoff/.env.example` open as your fill-in checklist.

## 1. Nicholas creates the accounts (he enters his card)

Have him sign up (free unless noted) and **set a monthly spend cap** on the AI ones:
- **GitHub** — github.com (to own the code repo).
- **Render** — render.com (hosting; will be the only paid one).
- **Anthropic** — console.anthropic.com → add billing → **set a monthly limit** → create an API key. *(Primary provider.)*
- **OpenAI** *(optional failover)* — platform.openai.com → billing + usage limit → key.
- **Supabase** — supabase.com (database; free tier fine).

## 2. Push the clean repo to his GitHub

With him logged into GitHub, create an empty repo (e.g. `robo-nick`), then:
```bash
cd handoff/client-repo
git init -b main && git add -A && git commit -m "Robo-Nick — The Outdoor Squad bot"
git remote add origin https://github.com/<nicholas>/robo-nick.git
git push -u origin main
```

## 3. Set up his Supabase project

1. Supabase → New project (pick a region near Sydney, e.g. `ap-southeast-2`).
2. SQL Editor → paste the contents of `supabase_schema.sql` → Run. This creates the
   4 tables (`outdoor_squad_conversations`, `_events`, `_conversation_logs`, `_leads`).
3. **Confirm RLS** is on for those tables and they're reachable only by the
   `service_role` key (the app uses the service role; the anon key must not read leads).
4. Project Settings → API → copy the **Project URL** and the **service_role** key for step 4.

## 4. Create the Render web service (his workspace, his card)

1. Render → **New → Blueprint** → connect his GitHub → pick `robo-nick`. It reads
   `render.yaml` and proposes the service.
2. **Plan: Starter (~$7/mo recommended)** — avoids free-tier spin-down and the
   concurrency fragility flagged in the security audit. (Free works for testing.)
3. Set the **Environment** variables from `handoff/.env.example`:
   - `OUTDOOR_SQUAD_ANTHROPIC_API_KEY` = his Anthropic key *(secret)*
   - `OUTDOOR_SQUAD_ADMIN_PASSWORD` = the password from step 0 *(secret)*
   - `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY` = from step 3 *(secret)*
   - lead delivery: either the SMTP block **or** the webhook URL (needed for
     `handoff_ready: true`)
   - the non-secret ones (`DEPLOYMENT_MODE=handoff`, models, trial link, human
     email/phone) are already in `render.yaml` — just confirm.
4. Deploy. Note the assigned URL (e.g. `https://robo-nick-xxxx.onrender.com`).

## 5. Verify before cutover

```bash
curl -s https://<his-bot-url>/api/health | python3 -m json.tool
```
Must show: `handoff_ready: true`, `owner_key_configured: true`, `admin_configured: true`,
`trial_link_configured: true`, `storage_backend: "supabase"`.

Then:
- Open `/admin` with the new password → loads.
- Send a few test chats (pricing, "is this just CrossFit?", an injury question) and a
  contact detail → confirm a lead lands in `/admin` and the notification fires.
- (Optional) run `python3.11 run_review_build_smoke.py` from the repo with prod env.

## 6. Cut over the website embed

Update the embed on the Outdoor Squad site to his new URL:
```html
<script src="https://<his-bot-url>/widget.js" defer></script>
```
Load the live site, confirm the widget talks to the new backend (network tab → calls
go to his URL), and have him send one real message.

## 7. Decommission + handover

- Suspend/delete the AI Sprints review service (`srv-d7bnmtggjchc73dp74d0` /
  `outdoor-squad-bot.onrender.com`) once the new one is confirmed live.
- Rotate/forget any AI Sprints keys that were in the review service.
- Give Nicholas/Lyn: the **admin URL + password**, the repo link, and `README.md`
  (client version). Walk them through `/admin` once.
- **Set expectations clearly:** he owns the ~$15–40/mo costs; the bot runs itself, but
  *content/code changes are a developer task* — agree whether that's "frozen as-is" or
  occasional paid tweaks from AI Sprints.

---

## What stays with AI Sprints (does NOT transfer)
The original `jacowilm/outdoor-squad-bot` repo (internal sales notes + full history),
this `handoff/` folder, and the AI Sprints Render/Anthropic/Supabase accounts. The
client only ever gets the clean `robo-nick` repo.

## Rollback
If anything breaks during cutover, point the website embed back at the old
`outdoor-squad-bot.onrender.com` URL (keep it running until step 6 is confirmed). No
data is lost — the new Supabase is separate.
