# Robo-Nick — The Outdoor Squad enquiry bot

Robo-Nick is the chat assistant embedded on the Outdoor Squad website. It answers
common enquiries in Nick's voice, routes people toward the right first step (free
trial, SPT, Youth Training Program), and captures leads for the team.

## How it works

- A small **FastAPI** backend (this repo) runs on **Render**.
- A lightweight **`widget.js`** is embedded on your website; it calls the backend.
- Conversations and leads are stored in your **Supabase** project.
- Answers come from your **AI provider** (Anthropic Claude Haiku, with an optional
  OpenAI failover) grounded in the files under `source-docs/` and `knowledge_base.md`.

## Configuration

All settings are environment variables — see **`.env.example`** for the full list
with comments. In Render they live under the service's **Environment** tab. The
secrets (API keys, admin password, Supabase service key, SMTP password) are entered
in Render only, never committed.

Health check: `GET /api/health` returns readiness booleans. Before going live it
should show `handoff_ready: true`, `owner_key_configured: true`, `admin_configured: true`,
and `trial_link_configured: true`.

## Running it / deploying

Render auto-deploys whenever you push to the `main` branch of this repo. To run
locally for testing:

```bash
pip install -r requirements.txt
uvicorn app:app --reload          # then open http://localhost:8000
```

## The owner dashboard

Visit **`/admin`** on your bot's URL and log in with `OUTDOOR_SQUAD_ADMIN_USERNAME`
/ `OUTDOOR_SQUAD_ADMIN_PASSWORD`. From there Nick/Lyn can:

- review captured **leads** (and download them as CSV),
- read **conversation transcripts** (contact details are redacted),
- see simple **metrics**.

Leads also get pushed to you live if you configure the lead-summary email or webhook
(see `.env.example`).

## Updating what the bot knows

The bot's knowledge lives in two places:

- **`knowledge_base.md`** — the curated facts (prices, classes, timetable, coaches,
  locations, policies).
- **`source-docs/`** — the longer source material (FAQ, brand voice, member reviews,
  injury protocol).

Current naming/timetable note: the bot persona is **Robo-Nick**; per Nicholas's 2026-06-12 decision the bot refers to the human Nick as **Humanoid-Nick** (replacing "Real Nick") in its replies,
and the current class source treats **Flow'N'Flex** as the umbrella class for the
old Yoga Squad plus yoga, Pilates and mobility-style sessions. Do not reintroduce
Power'N'Pilates as a separate current class unless Nick/Lyn explicitly bring it
back.

To change an answer: edit the relevant file, commit, and push to `main` — Render
redeploys automatically. *Note: this is a developer task (editing files + git). If
no one on the team is comfortable with that, keep a developer on call for occasional
updates.*

## Embedding on your website

Paste this once into your site (Square/Wix/etc.), replacing the URL with your bot's
Render URL:

```html
<script src="https://YOUR-BOT-URL.onrender.com/widget.js" defer></script>
```

The widget loads itself; nothing else is needed.

## Security

Hardened for public use: the `/admin` dashboard is password-protected and XSS-safe,
the public endpoints are rate-limited with input caps, and lead/transcript data files
are gitignored (production uses Supabase). Keep the admin password long and random,
and set a **monthly spend cap** on your Anthropic/OpenAI accounts as a cost backstop.

## Ownership

The Outdoor Squad owns this product, its hosting, data, and accounts, and the ongoing
hosting/API/database costs (typically ~$10–25/month at normal enquiry volume, up to
~$50–60/month if chat traffic gets heavy).
