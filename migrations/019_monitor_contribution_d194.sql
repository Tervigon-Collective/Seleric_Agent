-- D-194 (Agent_Core de6c4c4, 2026-11-05) redefined catalogue ssot_gross_profit as gross_sales - placed_cogs - ad_spend
-- but kept the id. The monitor targets were seeded on the old meaning, now ssot_contribution_before_ads
-- (net_sales - net_cogs), so gross profit, gross margin and net ROAS raised false criticals from 2026-11-04.
-- Move them onto contribution. Every data step copies existing rows, so a database without them is unchanged.

-- Catalogue description per part when the target was seeded; the runner fills NULLs and flags a later change.
ALTER TABLE monitor.metrics ADD COLUMN IF NOT EXISTS definitions JSONB;

INSERT INTO monitor.metrics (metric_id, label, unit, direction_bad, eval_window, intraday, settles_after_days, enabled, kind)
SELECT 'ssot_contribution_before_ads', 'Contribution before ads (net sales - net COGS)', unit, direction_bad, eval_window,
       intraday, settles_after_days, TRUE, kind
FROM monitor.metrics WHERE metric_id = 'ssot_gross_profit'
ON CONFLICT (metric_id) DO NOTHING;

-- The seeded band is a contribution band (seeded 2026-11-03 from serve.ssot_pnl_daily, pre-D-194).
INSERT INTO monitor.target_versions (brand_id, metric_id, floor_value, target_value, ceiling_value, seed,
                                     seed_window_start, seed_window_end, note, created_by)
SELECT brand_id, 'ssot_contribution_before_ads', floor_value, target_value, ceiling_value, seed,
       seed_window_start, seed_window_end, 'moved from ssot_gross_profit (D-194: the id changed meaning)', 'migration 019'
FROM monitor.targets_current
WHERE metric_id = 'ssot_gross_profit'
  AND NOT EXISTS (SELECT 1 FROM monitor.target_versions WHERE metric_id = 'ssot_contribution_before_ads');

-- Gross profit under its new meaning has no honest target yet.
UPDATE monitor.metrics SET enabled = FALSE WHERE metric_id = 'ssot_gross_profit';

UPDATE monitor.metrics
SET numerator_id = 'ssot_contribution_before_ads', label = 'Contribution margin (contribution / net sales)'
WHERE metric_id = 'gross_margin' AND numerator_id = 'ssot_gross_profit';

-- Same formula as catalogue ssot_net_roas (contribution_before_ads / ad_spend).
UPDATE monitor.metrics
SET numerator_id = 'ssot_contribution_before_ads', label = 'Net ROAS (contribution / ad spend)'
WHERE metric_id = 'net_roas_pooled' AND numerator_id = 'ssot_gross_profit';

INSERT INTO monitor.finding_events (finding_id, from_state, to_state, note)
SELECT finding_id, state, 'resolved', 'metric re-pointed to ssot_contribution_before_ads (D-194)'
FROM monitor.findings WHERE metric_id = 'ssot_gross_profit' AND state <> 'resolved';

UPDATE monitor.findings SET state = 'resolved', resolved_at = NOW(), updated_at = NOW()
WHERE metric_id = 'ssot_gross_profit' AND state <> 'resolved';
