-- Angle effectiveness aggregated from sequence_events (sent vs subsequent
-- opened/clicked on the same sequence+step), used by an epsilon-greedy angle
-- selector to favor higher-performing subject angles.
--
-- Rows are keyed on the angle slug (e.g. "pain_point", "social_proof", "roi").
-- The sent/opened/clicked counters are raw tallies derived by the learning tick
-- (api/cron/learning_tick.py → lib.learning.recompute_angle_performance).
--
-- open_rate and click_rate are Laplace-smoothed so that angles with low sample
-- counts do not dominate the selector.  Formula applied by the learning module:
--   smoothed_open_rate  = (opened  + 1) / (sent + 2)
--   smoothed_click_rate = (clicked + 1) / (sent + 2)
--
-- updated_at is refreshed on every recompute so staleness is detectable.

create table if not exists public.angle_performance (
  angle       text primary key,
  sent        int  not null default 0,
  opened      int  not null default 0,
  clicked     int  not null default 0,
  open_rate   numeric not null default 0,   -- Laplace-smoothed, 0..1
  click_rate  numeric not null default 0,   -- Laplace-smoothed, 0..1
  updated_at  timestamptz not null default now()
);
