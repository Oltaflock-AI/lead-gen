-- Phase 4 — reply intelligence.
--
-- When a lead replies (caught by the Gmail poller), we don't just pause the
-- sequence and wait for a human to notice. We capture the inbound message, ask
-- Claude to classify intent (interested / objection / not_now / stop / other)
-- and draft a tailored response, and surface it in a dashboard inbox for
-- one-click send. This table is that inbox.
--
-- gmail_msg_id is unique so the poller is idempotent: a reply already captured
-- is never re-drafted (which would also waste an LLM call) on the next tick.

create table if not exists public.replies (
  id            bigserial primary key,
  gmail_msg_id  text unique,                       -- Gmail message id; dedup + idempotency
  sequence_id   bigint,                            -- originating sequence (may be null)
  lead_id       bigint,
  from_email    text not null,
  business      text,
  in_subject    text,                              -- inbound reply subject
  in_body       text,                              -- inbound reply text
  intent        text,                              -- interested | objection | not_now | stop | other
  draft_subject text,                              -- AI-suggested reply subject
  draft_body    text,                              -- AI-suggested reply body (no signature; appended at send)
  model         text,
  status        text not null default 'pending',   -- pending | sent | dismissed
  handled_by    text,
  handled_at    timestamptz,
  created_at    timestamptz not null default now()
);
create index if not exists idx_replies_status on public.replies (status, created_at desc);
