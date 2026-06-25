-- Manual one-off composer sends ("blasts") get their own tracking, independent
-- of campaigns and sequences.
--
-- Why a separate table instead of reusing sequences/sequence_events:
--   * sequences.lead_id and sequences.campaign_id are both NOT NULL, and
--     sequence_events.sequence_id is NOT NULL too. A blast can target arbitrary
--     addresses typed into the composer ("Extra emails") that have no lead row
--     and belong to no campaign — those simply cannot be represented there.
--   * We also want a per-blast report (this specific send's open/click/reply
--     rate), which the global sequence_events stream does not isolate.
--
-- Open/click/reply state is correlated back from the Resend webhook by
-- resend_id (each outbound email has a unique id Resend echoes on every event).

create table if not exists public.blasts (
  id          bigserial primary key,
  subject     text not null,
  body        text,
  from_email  text,
  from_name   text,
  sent_by     text,              -- app_user id/email who triggered the send
  recipients  int  not null default 0,
  created_at  timestamptz not null default now()
);
create index if not exists idx_blasts_recent on public.blasts (created_at desc);

create table if not exists public.blast_recipients (
  id          bigserial primary key,
  blast_id    bigint not null references public.blasts(id) on delete cascade,
  email       text not null,
  business    text,
  lead_id     bigint,            -- optional link back to a lead (no FK: blasts outlive leads)
  resend_id   text,              -- Resend email id, used to correlate webhook events
  status      text not null default 'sent',   -- sent | failed
  opened      boolean not null default false,
  clicked     boolean not null default false,
  bounced     boolean not null default false,
  replied     boolean not null default false,
  opened_at   timestamptz,
  clicked_at  timestamptz,
  created_at  timestamptz not null default now()
);
create index if not exists idx_blast_recipients_blast  on public.blast_recipients (blast_id);
create index if not exists idx_blast_recipients_resend on public.blast_recipients (resend_id) where resend_id is not null;
create index if not exists idx_blast_recipients_email  on public.blast_recipients (email);
