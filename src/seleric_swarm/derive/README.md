# `seleric_swarm.derive` (M2.3)

All arithmetic in the pipeline is one typed, recorded operation. Import from
this package only; `_ops` and `_engine` are private.

```python
from seleric_swarm.derive import derive

result = derive("share", [part, whole], read_model=read_model)
```

`derive(op, inputs, params=None, *, read_model, ref=None) -> Derivation`
takes resolved `ResultSet` / `Derivation` inputs and returns a frozen
`Derivation` whose `inputs` cite every input ref and whose `rows` carry the
input `evidence_id`s. An op that is not defined for the catalogue returns a
`Derivation` with `error` set (a `StageError`, `code` below) rather than
raising, so one bad step never ends a mission. Only an unknown op name raises.

## Ops

| op | inputs | params | refuses (`error.code`) when |
|---|---|---|---|
| `sum` | 1 `ResultSet` | `where: {dim: [values]}` optional | metric is non-additive (`not_additive`) |
| `share` | part, whole | — | units differ (`unit_mismatch`), whole is zero/absent (`zero_whole`) |
| `delta` | before, after | `per_day: bool` | units differ (`unit_mismatch`), unequal windows without `per_day` (`window_length_mismatch`) |
| `pct_change` | before, after | `per_day: bool` | as `delta`, plus zero baseline (`zero_base`) |
| `per_day` | 1 | — | no window (`no_window`) |
| `ratio` | numerator, denominator | `metric_id` optional | metric declares a composition (`composition_defined`), zero denominator (`zero_denominator`) |
| `recompute` | component `ResultSet`s | `metric_id` (required) | no composition (`no_composition`), missing component (`missing_components`), unreadable composition (`unsupported_composition`) |
| `reconcile` | parts…, total | `tolerance` | no total value (`no_total`), units differ (`unit_mismatch`) |
| `rank` | 1 | `by`, `order`, `limit` | bad order (`bad_order`) or limit (`bad_limit`) |
| `join` | ≥2 `ResultSet`s | `on: [dims]` | keys not carried by all (`keys_not_carried`), no keys (`no_keys`) |

Non-additive aggregations come from `SemanticReadModel.aggregation_for`
(`avg`/`average`/`mean`/`median`/`min`/`max`/`count_distinct`/`distinct`/
`percentile`/`rate`). `recompute` reads `composition` from the read model's
metric records: either `{numerator, denominator}` (a ratio) or a list of
`{metric, coefficient}` terms. `reconcile` records `residual` and
`explained_share` in its `params`.

The op set is fixed by the plan; the legacy helpers it mirrors
(`analytics/comparison.py::period_deltas`, `analytics/breakdown.py::shares`,
`agent/executor.py::_change`) stay in place until `DERIVE_MODE` enforces, then
`_MERGE_DERIVED` / `_PREFERRED_JOIN_KEYS` are deleted.

## Boundaries

`derive` is layer L2: it imports `seleric_swarm.semantic` and the frozen
`seleric_swarm.contracts.pipeline` contracts, never `re`, an I/O SDK, or
`seleric_swarm.adapters`. Persisting a `Derivation` as an artifact
(`artifact_type="derivation"`) is the caller's job — the `toolsets/derive.py`
agent tool and the executor wire it to the artifact store.
