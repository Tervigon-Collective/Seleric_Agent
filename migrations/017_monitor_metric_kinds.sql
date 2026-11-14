-- A ratio metric is judged on pooled parts (sum of numerator / sum of denominator over the window), never on an
-- average of daily ratios. Parts are catalogue metric ids, stored as data. 'catalogue' = read the id as is; an optional
-- weight_id pools a catalogue rate as a weighted mean (e.g. conversion_rate weighted by sessions).
ALTER TABLE monitor.metrics
    ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'catalogue' CHECK (kind IN ('catalogue', 'ratio')),
    ADD COLUMN IF NOT EXISTS numerator_id TEXT,
    ADD COLUMN IF NOT EXISTS denominator_id TEXT,
    ADD COLUMN IF NOT EXISTS weight_id TEXT;

ALTER TABLE monitor.metrics
    ADD CONSTRAINT metrics_ratio_parts CHECK (
        (kind = 'ratio' AND numerator_id IS NOT NULL AND denominator_id IS NOT NULL)
        OR (kind = 'catalogue' AND numerator_id IS NULL AND denominator_id IS NULL)
    );
