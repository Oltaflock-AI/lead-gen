-- Buffer table for Resend webhook events received by the Supabase Edge
-- Function. Local Flask polls this table every minute, dispatches each row
-- through sequencer.record_event(), then sets processed_at so the same row
-- never applies twice. Service role only -- RLS stays disabled (no anon).

create table if not exists public.email_events_raw (
  id            bigserial primary key,
  resend_id     text,
  event_type    text not null,
  payload       jsonb not null,
  received_at   timestamptz not null default now(),
  processed_at  timestamptz,
  source        text default 'resend-webhook'
);

create index if not exists idx_email_events_raw_unprocessed
  on public.email_events_raw (received_at)
  where processed_at is null;

create index if not exists idx_email_events_raw_resend
  on public.email_events_raw (resend_id);

-- Defensive: keep RLS off but make intent explicit. Service role bypasses
-- RLS by default; anon role is never granted.
alter table public.email_events_raw disable row level security;
revoke all on public.email_events_raw from anon, authenticated;
