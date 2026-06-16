-- Autopilot pipeline schema. Vercel cron + Supabase state of truth.
-- Migrates lead-gen off SQLite to Supabase so it runs on serverless.

-- Campaigns: control panel rows. Add/disable from Supabase UI.
create table if not exists public.campaigns (
  id                  bigserial primary key,
  name                text not null unique,
  niche               text not null,
  region              text not null,
  offer_brief         text,
  active              boolean not null default true,
  daily_scrape_target int not null default 50,
  notes               text,
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now()
);

-- Leads: master record per business.
create table if not exists public.leads (
  id                bigserial primary key,
  campaign_id       bigint references public.campaigns(id) on delete set null,
  business          text not null,
  website           text,
  email             text,
  email_status      text,
  phone             text,
  address           text,
  city              text,
  country           text,
  source            text,
  signals           jsonb,
  intent_score      int,
  decision_maker    jsonb,
  enrichment_status text not null default 'pending',
  enriched_at       timestamptz,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),
  unique (campaign_id, business)
);
create index if not exists idx_leads_enrich_pending
  on public.leads (enrichment_status, created_at)
  where enrichment_status = 'pending';
create index if not exists idx_leads_campaign on public.leads (campaign_id);
create index if not exists idx_leads_email on public.leads (email) where email is not null;

-- Sequences: one per lead. State of truth for the autopilot loop.
create table if not exists public.sequences (
  id            bigserial primary key,
  lead_id       bigint not null references public.leads(id) on delete cascade,
  campaign_id   bigint not null references public.campaigns(id),
  status        text not null default 'pending',
  current_step  int  not null default 0,
  opens         int  not null default 0,
  clicks        int  not null default 0,
  replied       boolean not null default false,
  bounced       boolean not null default false,
  next_send_at  timestamptz,
  paused_reason text,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  unique (lead_id)
);
create index if not exists idx_seq_due
  on public.sequences (next_send_at)
  where status = 'active' and next_send_at is not null;
create index if not exists idx_seq_status on public.sequences (status);

-- Drafts: pre-rendered subject + body per step.
create table if not exists public.drafts (
  id          bigserial primary key,
  sequence_id bigint not null references public.sequences(id) on delete cascade,
  step        int not null,
  subject     text not null,
  body        text not null,
  angle       text,
  model       text,
  created_at  timestamptz not null default now(),
  unique (sequence_id, step)
);

-- Sequence events: outbound sends + delivery callbacks + replies.
create table if not exists public.sequence_events (
  id          bigserial primary key,
  sequence_id bigint not null references public.sequences(id) on delete cascade,
  step        int,
  event_type  text not null,
  resend_id   text,
  meta        jsonb,
  ts          timestamptz not null default now()
);
create index if not exists idx_seq_events_seq on public.sequence_events (sequence_id, ts desc);
create index if not exists idx_seq_events_resend on public.sequence_events (resend_id) where resend_id is not null;
create index if not exists idx_seq_events_type_ts on public.sequence_events (event_type, ts desc);

-- Scrape run history: one row per cron invocation.
create table if not exists public.scrape_runs (
  id            bigserial primary key,
  campaign_id   bigint references public.campaigns(id),
  triggered_by  text not null default 'cron',
  target_count  int,
  scraped_count int not null default 0,
  enriched_count int not null default 0,
  status        text not null default 'running',
  error         text,
  started_at    timestamptz not null default now(),
  finished_at   timestamptz
);
create index if not exists idx_scrape_runs_recent on public.scrape_runs (started_at desc);

-- Daily roll-up view for the email digest.
create or replace view public.daily_metrics as
select
  date_trunc('day', ts) as day,
  count(*) filter (where event_type = 'sent')      as sent,
  count(*) filter (where event_type = 'delivered') as delivered,
  count(*) filter (where event_type = 'opened')    as opened,
  count(*) filter (where event_type = 'clicked')   as clicked,
  count(*) filter (where event_type = 'bounced')   as bounced,
  count(*) filter (where event_type = 'replied')   as replied
from public.sequence_events
group by 1;

-- Atomic counter increment (called from webhook on opened/clicked).
create or replace function public.increment_sequence_counter(seq_id bigint, field text)
returns void language plpgsql as $$
begin
  if field = 'opens' then
    update public.sequences set opens = opens + 1, updated_at = now() where id = seq_id;
  elsif field = 'clicks' then
    update public.sequences set clicks = clicks + 1, updated_at = now() where id = seq_id;
  end if;
end $$;

-- Service-role only across the board.
alter table public.campaigns        disable row level security;
alter table public.leads            disable row level security;
alter table public.sequences        disable row level security;
alter table public.drafts           disable row level security;
alter table public.sequence_events  disable row level security;
alter table public.scrape_runs      disable row level security;
revoke all on public.campaigns        from anon, authenticated;
revoke all on public.leads            from anon, authenticated;
revoke all on public.sequences        from anon, authenticated;
revoke all on public.drafts           from anon, authenticated;
revoke all on public.sequence_events  from anon, authenticated;
revoke all on public.scrape_runs      from anon, authenticated;
