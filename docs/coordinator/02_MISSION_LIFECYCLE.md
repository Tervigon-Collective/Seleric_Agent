# 02 — Mission Lifecycle

Statuses: `received` → `normalizing` → `decomposing` → `planning` → `assembled` → `running` → (`remediating` | `validating`) → `completed` | `prototype_completed` | `partial` | `blocked` | `failed`.

`partial` requires satisfied objectives, unresolved objectives, and blocking reasons.

Synthetic-only fixtures complete as production-compatible `completed` with a PROTOTYPE banner; the completion gate may also emit `prototype_completed` internally.
