-- N1 hardening: "paused by default" becomes a schema guarantee instead of an
-- app-code convention. Before this, campaigns.active defaulted to TRUE
-- (002_autopilot_schema.sql), so a row inserted outside the app (Supabase UI,
-- SQL console, a future API) went live on the next sequencer tick.
alter table public.campaigns alter column active set default false;
