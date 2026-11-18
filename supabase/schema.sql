-- ClipForge accounts, plans and usage. Run once in Supabase: SQL Editor -> New query -> Run.
-- The server uses the service key, which bypasses row-level security; RLS is on so the
-- public anon key can't read or write these tables.

create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  email text,
  name text,
  reason text,
  plan text not null default 'free' check (plan in ('free', 'creator', 'pro')),
  created_at timestamptz not null default now()
);

create table if not exists public.usage_events (
  id bigint generated always as identity primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  job_id text,
  kind text not null default 'clip',
  minutes integer not null check (minutes >= 0),
  created_at timestamptz not null default now()
);
create index if not exists usage_events_user_month on public.usage_events (user_id, created_at);

create table if not exists public.upgrade_intents (
  id bigint generated always as identity primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  plan text not null check (plan in ('creator', 'pro')),
  created_at timestamptz not null default now()
);

alter table public.profiles enable row level security;
alter table public.usage_events enable row level security;
alter table public.upgrade_intents enable row level security;
