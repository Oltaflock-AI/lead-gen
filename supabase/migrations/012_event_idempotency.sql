-- 012 — H1: event/counter idempotency + status enum CHECKs (plan.md Phase 1.6)
-- Additive only, except the two clearly-marked dedupe DELETEs that are required
-- for the unique indexes to build (they remove exact-duplicate event rows,
-- keeping the earliest). Review counts before applying:
--   select resend_id, event_type, count(*) from email_events_raw
--     where resend_id is not null group by 1,2 having count(*) > 1;
--   select resend_id, event_type, count(*) from sequence_events
--     where resend_id is not null group by 1,2 having count(*) > 1;

-- ── email_events_raw: dedupe key on (resend_id, event_type) ─────────────────
-- Cleanup required for the unique index: drop later duplicates, keep earliest.
delete from email_events_raw a
using email_events_raw b
where a.resend_id is not null
  and a.resend_id = b.resend_id
  and a.event_type = b.event_type
  and a.id > b.id;

create unique index if not exists email_events_raw_dedupe_uq
  on email_events_raw (resend_id, event_type);

-- ── sequence_events: one row per (resend_id, event_type) ────────────────────
-- NULL resend_id rows (tick-logged 'sent' fallbacks, inbound 'replied') never
-- collide: Postgres unique indexes treat NULLs as distinct, so this plain
-- index gives exactly the "where resend_id is not null" partial semantics.
delete from sequence_events a
using sequence_events b
where a.resend_id is not null
  and a.resend_id = b.resend_id
  and a.event_type = b.event_type
  and a.id > b.id;

create unique index if not exists sequence_events_dedupe_uq
  on sequence_events (resend_id, event_type);

-- ── enum CHECKs (NOT VALID first; validated best-effort below) ──────────────
do $$ begin
  alter table sequences add constraint sequences_status_chk
    check (status in ('active','paused','done','manual')) not valid;
exception when duplicate_object then null; end $$;

do $$ begin
  alter table leads add constraint leads_enrichment_status_chk
    check (enrichment_status in ('pending','enriched','failed')) not valid;
exception when duplicate_object then null; end $$;

do $$ begin
  alter table sequence_events add constraint sequence_events_type_chk
    check (event_type in ('sent','delivered','delayed','opened','clicked',
                          'replied','bounced','complained','opened_bot','clicked_bot')) not valid;
exception when duplicate_object then null; end $$;

-- Best-effort validation: raises a NOTICE instead of aborting the migration if
-- pre-existing rows violate a constraint. Re-run VALIDATE manually after fixing.
do $$ begin
  alter table sequences validate constraint sequences_status_chk;
exception when check_violation then
  raise notice 'sequences_status_chk left NOT VALID: existing rows violate it';
end $$;

do $$ begin
  alter table leads validate constraint leads_enrichment_status_chk;
exception when check_violation then
  raise notice 'leads_enrichment_status_chk left NOT VALID: existing rows violate it';
end $$;

do $$ begin
  alter table sequence_events validate constraint sequence_events_type_chk;
exception when check_violation then
  raise notice 'sequence_events_type_chk left NOT VALID: existing rows violate it';
end $$;
