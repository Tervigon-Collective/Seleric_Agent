# ADR-007 - Investigation premise states and health vocabulary

## Status

Accepted (2026-10-12)

## Context

`InvestigationState` ended a run in exactly three ways: `done`, `budget` and
`premise_false`. But `premise_false` was produced by `_premise` for three
different situations:

- the window we were told to investigate moved the *opposite* way (`opposite`),
- there was no material change at all (`not_material`), and
- a baseline or event read came back `None`, i.e. there was no data to test
  the premise against.

Reporting "there was no data" as "the premise is false" turns a data problem
into a false claim about the business, and it is indistinguishable in the report
from a genuinely refuted premise. Likewise `DataHealth.status` had no way to say
"this figure is preliminary" or "freshness could not be established", so a
provisional number and an unreadable view were both just caveats.

## Decision

`InvestigationState` moves to `schema_version: 2`. Its `status` gains
`not_material` and `insufficient_evidence`. Only `opposite` maps to the existing
`premise_false`; the new statuses stop the run with the same `stage="stop"` so
callers already handling `premise_false` handle them identically.

`DataHealth.status` gains `provisional` and `unknown`, and the freshness source
maps raw view statuses into the vocabulary; an unrecognised raw status becomes
`unknown` rather than being assumed late or silently trusted. `incomplete` and
`unknown` on the investigated metric stop the run as `unhealthy` with distinct
limitations; `provisional` is recorded and the run continues.

Aggregation additivity has one definition: `aggregation_is_additive` in
`investigate.state`, shared by the controller and the pipeline reader. The
pipeline reader no longer sums a non-additive metric across several rows; a
non-additive breakdown key repeated over rows is dropped instead of summed.

## Consequence

`premise_false` now means "the window moved the other way" only. A run with
missing evidence reports `insufficient_evidence`, and callers that need a binary
premise decision can treat `{premise_false, not_material, insufficient_evidence}`
as "not confirmed". Stored `schema_version: 1` state still validates (pydantic
ignores the default bump), and a resume of v1 state is read as a fresh v2 state
because no persisted status outside the literal is accepted.
