-- Entity activity lookups for live notification cards (per campaign / ad set / ad trail).
CREATE INDEX IF NOT EXISTS findings_entity_live
  ON monitor.findings (brand_id, entity_type, entity_id)
  WHERE detector LIKE 'live.%';
