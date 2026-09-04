# 07 - Coordinator Integration

## Stable boundary

```python
from seleric_swarm.agents.skeptic import (
    SkepticAgent, SkepticValidationRequest, Claim, SkepticDeps,
)

agent = SkepticAgent(
    evidence_repo=...,      # EvidenceRepository  (in-memory or Postgres-backed)
    artifact_repo=...,      # ArtifactRepository
    deps=SkepticDeps(       # every collaborator is optional / has an in-memory default
        metric_registry=..., model_registry=..., causal_graphs=...,
        incident_registry=..., rules=..., stats=..., causal_service=...,
        reasoning=...,       # ReasoningModel | NullReasoningModel (default)
    ),
    policies=SkepticPolicies.load(),   # config/skeptic_policies.yaml
)

verdict: SkepticVerdict = await agent.validate_claim(
    SkepticValidationRequest(
        mission_id="M-100",
        claim=Claim(mission_id="M-100", claim_type="causal",
                    statement="...", origin_agent="diagnostic_agent",
                    support_refs=["EV-11","EV-22"], causal_refs=["CAUS-3"]),
        evidence_refs=["EV-11","EV-22"],
        risk_context={"impact": "high"},
    )
)
```

## From a live swarm Blackboard

```python
from seleric_swarm.agents.skeptic import skeptic_from_blackboard

agent = skeptic_from_blackboard(blackboard)         # adapts evidence + artifacts
verdict = await agent.validate_claim(request)
```

`registries.repositories_from_blackboard()` maps
`seleric_swarm.swarm.blackboard.Blackboard` artifacts to the Skeptic's repos;
`*Artifact.from_blackboard()` adapts each payload shape.

## Activation policy

`config/skeptic_policies.yaml -> skeptic.activation` tells the Coordinator when a
Skeptic pass is *required*:

| claim_type | rule |
| --- | --- |
| numeric / comparison / qualitative | not required |
| anomaly | required if risk > 0.60 |
| correlation | required if risk > 0.50 |
| causal / forecast / action | always required |
| recommendation | required if risk > 0.65 |

`SkepticPolicies.skeptic_required(claim_type, risk_score)` encodes this.

## Handling the verdict

- **PASS** - Coordinator may promote the claim past the completion gate.
- **REVISE** - Coordinator dispatches `verdict.required_followups` (each has
  `requested_capability`, `preferred_domain`, `blocking`, stable `task_id`),
  then re-submits the revised claim (bounded by
  `budgets.max_followup_rounds`).
- **REJECT** - Coordinator drops/re-opens the claim and dispatches the
  remediation tasks. A `strategy` REJECT with an inventory follow-up means "ask
  the Inventory agent", not "the Skeptic leads inventory".

## The Skeptic is never mission lead

It has no `handoff_targets`; issues route back through the Coordinator via
`FollowUpTask.preferred_domain`.
