-- 014: full outbound shutdown (2026-07-25, operator request).
-- Unschedules every leadgen pg_cron job created by migration 013.
-- Run in the Supabase SQL editor (pg_cron is not reachable via PostgREST).
-- Reverse by re-running the cron.schedule block of 013.

do $$
declare j record;
begin
  for j in select jobid, jobname from cron.job where jobname like 'leadgen-%' loop
    perform cron.unschedule(j.jobid);
    raise notice 'unscheduled %', j.jobname;
  end loop;
end $$;
