create table if not exists public.outdoor_squad_conversations (
  session_id text primary key,
  messages jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.outdoor_squad_events (
  id bigserial primary key,
  timestamp timestamptz not null default timezone('utc', now()),
  event_type text not null,
  session_id text not null,
  metadata jsonb not null default '{}'::jsonb
);

create index if not exists outdoor_squad_events_session_idx
  on public.outdoor_squad_events (session_id, timestamp desc);

create index if not exists outdoor_squad_events_type_idx
  on public.outdoor_squad_events (event_type, timestamp desc);

-- Atomic, durable once-per-session claim for explicit human-request alerts.
-- The primary key is the cross-instance dedupe guarantee used by PostgREST.
create table if not exists public.outdoor_squad_human_request_claims (
  session_id text primary key,
  claimed_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.outdoor_squad_conversation_logs (
  id bigserial primary key,
  timestamp timestamptz not null default timezone('utc', now()),
  session_id text not null,
  role text not null,
  content text not null
);

create index if not exists outdoor_squad_conversation_logs_session_idx
  on public.outdoor_squad_conversation_logs (session_id, timestamp desc);

create table if not exists public.outdoor_squad_leads (
  id bigserial primary key,
  timestamp timestamptz not null default timezone('utc', now()),
  name text,
  email text,
  phone text,
  route text,
  location_preference text,
  time_preference text,
  concerns jsonb not null default '[]'::jsonb,
  handoff_summary text,
  raw_message text,
  session_id text,
  channel text,
  phone_typed text
);

-- Added 11 Sep 2026 with the website-vs-WhatsApp diff work. Run these against
-- the live project BEFORE deploying: a WhatsApp lead carries the channel it
-- came from and, when the person types a second number, the one they typed.
-- app.py degrades one field rather than the whole row if they are missing,
-- but a missing column still costs Nick that field on every alert.
alter table public.outdoor_squad_leads add column if not exists channel text;
alter table public.outdoor_squad_leads add column if not exists phone_typed text;

create index if not exists outdoor_squad_leads_timestamp_idx
  on public.outdoor_squad_leads (timestamp desc);

create unique index if not exists outdoor_squad_leads_dedupe_idx
  on public.outdoor_squad_leads (
    coalesce(session_id, ''),
    coalesce(email, ''),
    coalesce(phone, ''),
    coalesce(raw_message, '')
  );

alter table public.outdoor_squad_conversations enable row level security;
alter table public.outdoor_squad_events enable row level security;
alter table public.outdoor_squad_human_request_claims enable row level security;
alter table public.outdoor_squad_conversation_logs enable row level security;
alter table public.outdoor_squad_leads enable row level security;

-- Key/value settings (owner-changeable password hash, WhatsApp channel state:
-- kill switch, per-thread mute + nudge markers, rotating Momence refresh token,
-- and since 11 Sep 2026 "wa::wa_profile_name:{session_id}", the sender's own
-- WhatsApp display name, stored unverified for the owner's alerts and thread
-- list only).
-- Also since 11 Sep 2026 (findings #2 and #9): the per-thread markers
-- handoff/nudged/lead_alerted/momence_pushed/undelivered are keyed on the
-- EPISODE, so from the second conversation onwards the key reads
-- "wa::nudged:wa-614...#e2"; "wa::nudge_count:{session_id}" counts the
-- follow-ups ever sent to a thread and is never reset.
-- The table already exists in the live project; recorded here so a fresh
-- provision from this file matches production. "key" must be PRIMARY KEY or
-- the on_conflict upserts in app.py degrade to blind inserts.
create table if not exists outdoor_squad_settings (
  key text primary key,
  value text,
  updated_at timestamptz
);
