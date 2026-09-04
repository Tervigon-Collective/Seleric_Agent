# 07 — Artifact Governance

`ArtifactManager`: schema-ready ingest, fingerprint dedup, lineage, synthetic taint, supersession, dependency-aware invalidation.

Evidence fingerprint: mission + metric + source + time_range + dimensions + value + baseline + calc version.

Hypothesis identity: same statement reuses canonical ID unless materially revised.
