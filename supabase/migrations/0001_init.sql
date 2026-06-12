-- GrowthOS — initial schema
-- Run via: supabase db push  (or paste into the Supabase SQL editor)

-- ============================================================ profiles ====
create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  email text not null,
  full_name text,
  avatar_url text,
  plan text not null default 'starter' check (plan in ('starter','growth','pro','enterprise')),
  onboarding_completed boolean not null default false,
  activation_milestones jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now()
);

alter table public.profiles enable row level security;

create policy "profiles_select_own" on public.profiles
  for select using (auth.uid() = id);
create policy "profiles_update_own" on public.profiles
  for update using (auth.uid() = id);
create policy "profiles_insert_own" on public.profiles
  for insert with check (auth.uid() = id);

-- Auto-create a profile on signup
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer set search_path = public
as $$
begin
  insert into public.profiles (id, email, full_name, avatar_url)
  values (
    new.id,
    new.email,
    new.raw_user_meta_data->>'full_name',
    new.raw_user_meta_data->>'avatar_url'
  )
  on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- =========================================================== businesses ====
create table if not exists public.businesses (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  name text not null,
  website_url text not null,
  industry text not null,
  location text not null,
  revenue_goal numeric not null default 0,
  target_customer text not null default '',
  created_at timestamptz not null default now()
);

alter table public.businesses enable row level security;
create policy "businesses_all_own" on public.businesses
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
create index if not exists businesses_user_idx on public.businesses(user_id);

-- ======================================================== workflow_runs ====
-- One row per agent execution (audit, competitors, leads, outreach, opportunities).
-- `steps` tracks live progress; `result` stores the typed report JSON.
create table if not exists public.workflow_runs (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  business_id uuid references public.businesses(id) on delete cascade,
  kind text not null check (kind in ('audit','competitors','leads','outreach','opportunities')),
  status text not null default 'queued' check (status in ('queued','running','completed','failed')),
  steps jsonb not null default '[]'::jsonb,
  result jsonb,
  error text,
  model_usage jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now(),
  completed_at timestamptz
);

alter table public.workflow_runs enable row level security;
create policy "workflow_runs_all_own" on public.workflow_runs
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
create index if not exists workflow_runs_user_kind_idx on public.workflow_runs(user_id, kind, created_at desc);

-- ================================================================ icps ====
create table if not exists public.icps (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  business_id uuid references public.businesses(id) on delete cascade,
  name text not null,
  industry text,
  company_size text,
  region text,
  pain_points jsonb not null default '[]'::jsonb,
  buying_triggers jsonb not null default '[]'::jsonb,
  decision_makers jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now()
);

alter table public.icps enable row level security;
create policy "icps_all_own" on public.icps
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

-- =============================================================== leads ====
create table if not exists public.leads (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  business_id uuid references public.businesses(id) on delete cascade,
  icp_id uuid references public.icps(id) on delete set null,
  company text not null,
  website text,
  industry text,
  company_size text,
  location text,
  contact_name text,
  contact_title text,
  contact_email text,
  linkedin_url text,
  score int not null default 0 check (score between 0 and 100),
  score_reasons jsonb not null default '[]'::jsonb,
  deal_probability numeric not null default 0,
  estimated_deal_value numeric not null default 0,
  status text not null default 'new'
    check (status in ('new','contacted','replied','qualified','meeting','won','lost')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.leads enable row level security;
create policy "leads_all_own" on public.leads
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
create index if not exists leads_user_score_idx on public.leads(user_id, score desc);
create index if not exists leads_user_status_idx on public.leads(user_id, status);

-- =========================================================== campaigns ====
create table if not exists public.campaigns (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  business_id uuid references public.businesses(id) on delete cascade,
  name text not null,
  status text not null default 'draft' check (status in ('draft','active','paused','completed')),
  sequence jsonb not null default '[]'::jsonb,
  stats jsonb not null default '{"sent":0,"opened":0,"replied":0,"meetings":0,"open_rate":0,"reply_rate":0,"meeting_rate":0}'::jsonb,
  created_at timestamptz not null default now()
);

alter table public.campaigns enable row level security;
create policy "campaigns_all_own" on public.campaigns
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

create table if not exists public.campaign_leads (
  campaign_id uuid not null references public.campaigns(id) on delete cascade,
  lead_id uuid not null references public.leads(id) on delete cascade,
  user_id uuid not null references public.profiles(id) on delete cascade,
  primary key (campaign_id, lead_id)
);

alter table public.campaign_leads enable row level security;
create policy "campaign_leads_all_own" on public.campaign_leads
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

-- ======================================================= opportunities ====
create table if not exists public.opportunities (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  business_id uuid references public.businesses(id) on delete cascade,
  category text not null,
  title text not null,
  description text,
  impact_score int not null default 0,
  effort text not null default 'medium' check (effort in ('low','medium','high')),
  estimated_annual_value numeric not null default 0,
  status text not null default 'open' check (status in ('open','in_progress','done','dismissed')),
  created_at timestamptz not null default now()
);

alter table public.opportunities enable row level security;
create policy "opportunities_all_own" on public.opportunities
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

-- =============================================================== events ====
-- Product analytics / outreach tracking events (sent, opened, replied, …)
create table if not exists public.events (
  id bigint generated always as identity primary key,
  user_id uuid not null references public.profiles(id) on delete cascade,
  kind text not null,
  entity_type text,
  entity_id uuid,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

alter table public.events enable row level security;
create policy "events_all_own" on public.events
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
create index if not exists events_user_kind_idx on public.events(user_id, kind, created_at desc);
