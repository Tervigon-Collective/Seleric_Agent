# 04 - Causal Validation

`validators/causal_validator.py` + `registries.CausalValidationService`.

## Boundary

```python
class CausalValidationService(Protocol):
    async def validate(self, artifact: CausalAnalysisArtifact,
                       *, context: dict) -> CausalValidationResult: ...
```

Implementations:

- `BasicCausalValidationService(graphs)` - **default**. Metadata-driven audit; it
  does *not* estimate effects, it audits an already-produced
  `CausalAnalysisArtifact`.
- `UnavailableCausalValidationService` - stand-in when DoWhy is down. Returns
  `available=False`, `confidence=ASSOCIATION_ONLY`; **never fakes a pass**.
- Production: wrap `seleric_swarm.causal.dowhy_service.DoWhyService` to run
  placebo-treatment, random-common-cause and data-subset refuters.

## Checks (spec sec. 26)

1. **Temporal ordering** - `treatment_started_at <= outcome_started_at`. If the
   outcome precedes the treatment -> `confidence=REJECTED`, blocking `temporal`
   challenge -> **REJECT**.
2. **Causal graph** - graph registered (`CausalGraphRegistry`), has a directed
   path `treatment_node -> outcome_node`. Missing graph or no path -> warning.
3. **Confounders** - at least one common cause adjusted for; else methodological
   issue.
4. **Estimator** - estimator metadata present.
5. **Refutation** - `>= causal.min_refutations` (default 2) run and all pass for
   the top confidence tier; partial -> warning + `causal_refutation` follow-up.
6. **Alternative explanation** - the alternative generator seeds candidates from
   `common_causes`; an unresolved one (priority>=6) forces **REVISE**.

## Confidence levels (never "proved causal")

```
REJECTED
ASSOCIATION_ONLY
PLAUSIBLE_CAUSAL
CAUSALLY_SUPPORTED_UNDER_ASSUMPTIONS
STRONGLY_SUPPORTED
```

Mapped to a `causal_confidence` score signal (0.0 - 0.9) that feeds the causal
trust profile. Every non-REJECTED causal verdict adds the limitation
*"Unmeasured confounding cannot be completely excluded."*

## Missing causal artifact

A `causal` claim with no `CausalAnalysisArtifact` -> `status=INSUFFICIENT`,
blocking `EvidenceGap` (`capability_required=causal_diagnosis`), a blocking
`causal_diagnosis` follow-up -> **REVISE** (a blocking gap is REVISE, not
REJECT).
