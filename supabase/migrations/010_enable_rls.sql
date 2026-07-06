-- F17: enable Row-Level Security with NO policies on every table (deny-all for
-- anon/authenticated). All app access uses the service-role key, which BYPASSES
-- RLS, so this changes nothing for the app — but if the anon key ever leaks or a
-- new unauthenticated code path appears, the tables stay locked instead of
-- exposing all lead PII. Defense-in-depth; safe to apply.

do $$
declare t text;
begin
  foreach t in array array[
    'email_events_raw','campaigns','leads','sequences','drafts',
    'sequence_events','scrape_runs','app_users','suppressions','replies',
    'angle_performance','blasts','blast_recipients'
  ]
  loop
    if to_regclass('public.'||t) is not null then
      execute format('alter table public.%I enable row level security;', t);
      execute format('alter table public.%I force row level security;', t);
      execute format('revoke all on public.%I from anon, authenticated;', t);
    end if;
  end loop;
end $$;
