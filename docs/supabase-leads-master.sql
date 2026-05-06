-- leads_master — long-term lead profile store
-- Run once in Supabase SQL editor. Re-running is safe (CREATE IF NOT EXISTS).
-- Keyed on lowercased email so scrapes, sends, and webhooks all funnel
-- into one row per prospect across CSVs and months.

create table if not exists public.leads_master (
  id              bigserial primary key,
  email           text not null unique,

  -- Identity / firmographics (filled from scrape)
  business_name   text,
  business_type   text,
  niche           text,
  city            text,
  address         text,
  country         text,
  phone           text,
  website         text,
  rating          numeric,
  review_count    int,
  place_id        text,
  opening_hours_summary text,

  -- AI scoring (overwrites on each scrape so latest wins)
  ai_niche_fit    numeric,
  ai_lead_score   numeric,
  ai_score_reason text,
  email_verified  text,

  -- Provenance
  source_csv      text,

  -- Outreach lifecycle (latest-event-wins)
  outreach_status text,        -- sent | delivered | opened | clicked | replied | bounced | failed
  last_subject    text,
  last_sent_at    timestamptz,
  last_opened_at  timestamptz,
  last_clicked_at timestamptz,
  replied_at      timestamptz,

  -- Derived intent (optional — fill via batch job later)
  intent_score    int,         -- 0–100
  intent_signal   text,        -- cold | curious | engaged | warm | hot

  -- Free-form
  notes           text,
  tags            jsonb default '[]'::jsonb,

  first_seen_at   timestamptz default now(),
  last_seen_at    timestamptz default now(),
  updated_at      timestamptz default now()
);

create index if not exists idx_leads_master_niche       on public.leads_master (niche);
create index if not exists idx_leads_master_country     on public.leads_master (country);
create index if not exists idx_leads_master_status      on public.leads_master (outreach_status);
create index if not exists idx_leads_master_intent      on public.leads_master (intent_score desc);
create index if not exists idx_leads_master_last_sent   on public.leads_master (last_sent_at desc);
create index if not exists idx_leads_master_business    on public.leads_master (business_name);

create or replace function public._set_leads_master_touched()
returns trigger language plpgsql as $$
begin
  new.updated_at := now();
  if new.last_seen_at is null then
    new.last_seen_at := now();
  end if;
  return new;
end;
$$;

drop trigger if exists trg_leads_master_touched on public.leads_master;
create trigger trg_leads_master_touched
  before update on public.leads_master
  for each row execute function public._set_leads_master_touched();
