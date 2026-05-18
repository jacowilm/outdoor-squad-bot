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
  session_id text
);

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
alter table public.outdoor_squad_conversation_logs enable row level security;
alter table public.outdoor_squad_leads enable row level security;
