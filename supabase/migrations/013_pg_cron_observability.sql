-- 013 — Phase 2: pg_cron scheduling + observability (plan.md 2.1/2.2/2.3)
-- Additive only.
--
-- BEFORE applying, store the two secrets in Vault ONCE (values never live in
-- migrations):
--   select vault.create_secret('<CRON_SECRET value>',                'leadgen_cron_secret');
--   select vault.create_secret('https://lead-gen-fawn-seven.vercel.app', 'leadgen_base_url');

create extension if not exists pg_cron;
create extension if not exists pg_net;

-- ── observability tables ────────────────────────────────────────────────────
create table if not exists cron_heartbeats (
  job         text primary key,
  last_ok     timestamptz,
  last_status text,
  last_body   text,
  updated_at  timestamptz not null default now()
);

create table if not exists system_events (
  id      bigint generated always as identity primary key,
  ts      timestamptz not null default now(),
  level   text not null,          -- info | warn | error | alert | alert-sent
  source  text not null,
  message text not null,
  meta    jsonb
);
create index if not exists system_events_source_ts on system_events (source, ts desc);

-- ── B7: atomic daily send counter (claim-then-count kills the cap race) ─────
create table if not exists send_counters (
  day   date primary key,
  count int not null default 0
);

create or replace function claim_send_slot(p_cap int)
returns boolean language plpgsql as $fn$
declare c int;
begin
  insert into send_counters as sc (day, count) values (current_date, 1)
  on conflict (day) do update set count = sc.count + 1
  returning sc.count into c;
  if c > p_cap then
    update send_counters set count = count - 1 where day = current_date;
    return false;
  end if;
  return true;
end $fn$;

-- ── 2.3: bounded auto-retry of error-paused sequences ───────────────────────
alter table sequences add column if not exists retry_count int not null default 0;

-- Service-role-only tables: RLS on, no anon grants (matches migration 010).
alter table cron_heartbeats enable row level security;
alter table system_events   enable row level security;
alter table send_counters   enable row level security;
revoke all on cron_heartbeats, system_events, send_counters from anon, authenticated;

-- ── pg_cron schedules ───────────────────────────────────────────────────────
-- Secrets are read from Vault AT CALL TIME; nothing sensitive is stored here.
create or replace function leadgen_cron_call(path text)
returns bigint language sql as $fn$
  select net.http_get(
    url := (select decrypted_secret from vault.decrypted_secrets where name = 'leadgen_base_url') || path,
    headers := jsonb_build_object(
      'Authorization',
      'Bearer ' || (select decrypted_secret from vault.decrypted_secrets where name = 'leadgen_cron_secret')),
    timeout_milliseconds := 30000
  );
$fn$;

-- Idempotent re-apply: clear any prior leadgen-* schedules first.
do $$
declare j record;
begin
  for j in select jobid from cron.job where jobname like 'leadgen-%' loop
    perform cron.unschedule(j.jobid);
  end loop;
end $$;

select cron.schedule('leadgen-sequencer-tick', '*/5 * * * *',  $$select leadgen_cron_call('/api/cron/sequencer_tick')$$);
select cron.schedule('leadgen-enrich-tick',    '*/10 * * * *', $$select leadgen_cron_call('/api/cron/enrich_tick')$$);
select cron.schedule('leadgen-replies-tick',   '*/15 * * * *', $$select leadgen_cron_call('/api/cron/replies_tick')$$);
select cron.schedule('leadgen-research-tick',  '*/15 * * * *', $$select leadgen_cron_call('/api/cron/research_tick')$$);
select cron.schedule('leadgen-learning-tick',  '0 6 * * *',    $$select leadgen_cron_call('/api/cron/learning_tick')$$);
select cron.schedule('leadgen-watchdog',       '0 */6 * * *',  $$select leadgen_cron_call('/api/cron/watchdog')$$);
-- daily_scrape + daily_digest stay on Vercel cron (vercel.json) — they work there.
