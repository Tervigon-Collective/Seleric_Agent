# 04 - Causal Estimation

`causal/estimator.py` + `registries.CausalEstimationService`.

## The service Protocol

```python
class CausalEstimationService(Protocol):
    async def estimate(
        self, query: CausalEstimationQuery, *, observations: Any = None
    ) -> CausalAnalysisArtifact: ...
```

| implementation | behaviour |
| --- | --- |
| `TemplateCausalEstimationService(causal_truth)` | **default**. Emits a metadata-level artifact from a scenario `causal_truth` dict; `passed=True` only when treatment+outcome match the declared truth. Deterministic, offline. |
| `services.DoWhyCausalEstimationService` | when `observations` is a pandas DataFrame -> runs `seleric_swarm.causal.dowhy_service.DoWhyService` (identify -> estimate -> placebo / random-common-cause / data-subset refuters) and returns a fitted artifact (`synthetic=False`). No DataFrame -> delegates to a fallback (template) and notes it. DoWhy failure -> fallback + limitation. **Never fakes a pass.** |

## Which hypotheses get estimated

`graph.causal_node`: hypotheses with status `testing`/`retained`, a
`treatment_metric` and `posterior_score >= 0.5`, capped at
`budgets.max_primary_candidates` (default 2). The one with the highest confidence
tier wins.

## Confidence tiers

```
REJECTED
ASSOCIATION_ONLY
PLAUSIBLE_CAUSAL
CAUSALLY_SUPPORTED_UNDER_ASSUMPTIONS
STRONGLY_SUPPORTED
```

`_confidence()`:

1. **Temporal gate** - if `treatment_started_at > outcome_started_at` -> `REJECTED`.
2. **Graph gate** - `require_graph` and (no graph, or no directed path
   treatment-node -> outcome-node) -> at best `PLAUSIBLE_CAUSAL`.
3. `passed` + graph ok + `>= min_refutations` all passing + ≥1 common cause -> `STRONGLY_SUPPORTED`
4. `passed` + ≥1 refutation all passing -> `CAUSALLY_SUPPORTED_UNDER_ASSUMPTIONS`
5. otherwise `PLAUSIBLE_CAUSAL` / `ASSOCIATION_ONLY`

## The metadata ceiling

If `observations is None` **and** `context["trust_metadata_causal"]` is not set,
the tier is capped at `causal.metadata_only_ceiling` (default `PLAUSIBLE_CAUSAL`).
So a real Coordinator call with no data can never "retain" - the finding is
`inconclusive` with the limitation *"Causal estimate is metadata-only"*. The
swarm bridge sets `trust_metadata_causal=True` because in fixture/replay mode the
declared `causal_truth` is authoritative.

## Retain decision

`policies.meets_retain(confidence)` - default threshold
`CAUSALLY_SUPPORTED_UNDER_ASSUMPTIONS`. On retain the primary hypothesis becomes
`retained`, every other `testing` hypothesis becomes `rejected` ("superseded"),
and a causal `Claim` is emitted. `REJECTED` -> the hypothesis is `rejected`; a
below-threshold tier -> `inconclusive` and (if `emit_inconclusive_finding`) a
finding with no `retained_hypothesis_id` and no claim.
