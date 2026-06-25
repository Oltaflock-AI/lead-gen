-- Hard opt-out suppression list. Any address in this table must never receive
-- another email, regardless of campaign or sequence. The sequencer checks this
-- table before each send so suppressed leads are silently skipped even if they
-- are still enrolled in an active sequence.
--
-- Addresses are always stored lowercased so lookups are a simple equality check.
-- source distinguishes how the entry was created (e.g. 'gmail-reply', 'manual').
-- reason captures the intent (e.g. 'unsubscribe', 'manual').

create table if not exists public.suppressions (
  email       text primary key,               -- always lowercased
  reason      text,                           -- 'unsubscribe' | 'manual' | …
  source      text,                           -- 'gmail-reply' | 'manual' | …
  created_at  timestamptz not null default now()
);
