# 00 - Project Charter

## Product name

Seleric Intelligence Swarm

## Mission

Create a business intelligence system that can decompose ambiguous business questions, acquire evidence from governed data sources, detect abnormal changes, investigate candidate causes, forecast likely outcomes, propose interventions and challenge its own conclusions before producing a response.

## Primary domains

- Performance marketing
- Ecommerce / marketplaces
- Website funnel and product experience
- Finance
- Inventory
- Procurement
- Technical / website reliability

## Core design objectives

1. Reduce hallucination by grounding every material claim.
2. Use ML/statistics for quantitative detection and prediction.
3. Use causal inference for causal questions instead of narrative-only reasoning.
4. Keep the analysis frontier moving as the causal picture sharpens.
5. Keep agent interfaces explicit and testable.
6. Separate orchestration from computation.
7. Make every mission replayable and auditable.

## Success criteria

A mission is successful when the system can show:

- what it was asked,
- which investigation steps ran,
- which tools were called,
- what evidence was retrieved,
- how metrics were calculated,
- which hypotheses were tested,
- which models were used,
- what the evidence validator challenged,
- what remains uncertain,
- and why the final answer passed the validation gate.

## Initial operating mode

Read-only intelligence. Write actions require a separate human-approval architecture and are deliberately excluded from v0.
