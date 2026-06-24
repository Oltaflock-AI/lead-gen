-- Per-campaign sequence configuration for the interactive campaign builder.
-- Lets each campaign define how many emails to send and the cadence between
-- them, instead of the hardcoded 7-step drip in lib/sequence.py.
--
-- Shape (jsonb):
--   {"steps": [{"role": "first-touch", "gap_days": 0},
--              {"role": "value-drop",  "gap_days": 4},
--              {"role": "breakup",     "gap_days": 9}]}
-- gap_days = days to wait AFTER the previous step (first step is always 0).
-- role = one of the proven copy roles defined in lib.sequence.ROLE_INSTRUCTIONS.
--
-- NULL is intentional and safe: the sequencer treats NULL/missing as the
-- canonical 7-step DEFAULT_SEQUENCE, so every existing campaign and in-flight
-- sequence keeps its current behavior with zero backfill.

alter table public.campaigns
  add column if not exists sequence_config jsonb;
