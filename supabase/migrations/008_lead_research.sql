-- Real-time "fresh research" cache for warm/hot leads (Phase 3 of the
-- self-improving loop).
--
-- Once a lead engages (opens/clicks), a dedicated research tick scrapes their
-- website homepage and pulls their latest Google reviews, then stores a compact
-- digest here. The sequencer reads this digest at draft time and weaves ONE
-- specific, current detail into the next email, so engaged leads get a message
-- that is visibly about THEM right now, not a template.
--
-- Cached (not fetched in the send path) so the send tick stays fast and within
-- the function time budget. researched_at drives a TTL refresh.

alter table public.leads add column if not exists research      jsonb;
alter table public.leads add column if not exists researched_at timestamptz;

-- Lets the research tick cheaply find engaged leads whose digest is stale/missing.
create index if not exists idx_leads_researched_at on public.leads (researched_at);
