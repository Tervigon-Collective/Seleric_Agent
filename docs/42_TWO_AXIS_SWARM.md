# 42 - Two-Axis Dynamic Swarm (prototype)

`src/seleric_swarm/swarm/` implements the domain x intelligence swarm as a
**prototype with pluggable seams**. It runs end-to-end today on deterministic,
offline, clearly-SYNTHETIC providers; you swap providers to go live without
touching agent code.

```
AXIS 1  DOMAIN OWNERSHIP     Performance / Commerce / Funnel / Finance / Inventory / Procurement / Technical
AXIS 2  INTELLIGENCE         Observer / Anomaly / Diagnostic / Prediction / Strategy / Skeptic
```

There is **one** `DomainAgent` class (declarative `DomainConfig` per domain) and
**one** `SpecialistAgent` base. Improve Diagnostic once -> every domain's
diagnosis improves. Add a domain -> add a `DomainConfig` + a provider, not new
orchestration.

## Files

| Path | Role |
| --- | --- |
| `swarm/artifacts.py` | 7 canonical artifacts (Evidence -> Anomaly -> Hypothesis -> Causal -> Prediction/Strategy -> Skeptic). Every artifact carries `data_origin` + `synthetic`. |
| `swarm/blackboard.py` | Mission Blackboard + Evidence Ledger. Agents post artifacts, read *references*. `ArtifactStore` Protocol -> swap for Redis/PG. |
| `swarm/envelope.py` | `seleric.swarm.v1` `SwarmMessage` + `Intent` enum + `HandoffProposal`. |
| `swarm/transport.py` | `AgentTransport` Protocol. `InProcessTransport` now; `A2AHttpTransport` later - drop-in. |
| `swarm/autonomy.py` | Autonomy levels 0-6. Level 6 (execute business action) is disabled. |
| `swarm/providers/base.py` | **The seams.** `DataProvider`, `AnomalyDetector`, `CausalEngine`, `Forecaster`, `Optimizer`, `StatsEngine` Protocols + result records. |
| `swarm/providers/fixtures.py` | `Fixture*` / `Template*` implementations. Deterministic, **zero** LLM/network, everything stamped `FIXTURE`/`SYNTHETIC`. |
| `swarm/domain/base.py` `configs.py` | `DomainAgent` (context -> metric resolve -> observe -> reason -> `evaluate_handoff`) + all 7 configs. |
| `swarm/specialists/*.py` | Observer / Anomaly / Diagnostic / Prediction / Strategy / Skeptic. Each: policy -> service router (provider) -> artifact builder -> validator. |
| `swarm/orchestrator.py` | `run_swarm_mission()` - dynamic team assembly + evidence-driven leadership loop + adaptive pipeline + synthesis. |
| `swarm/synthesis.py` | Prototype synthesizer. Prominent SYNTHETIC banner; claims labelled `trust_label: SYNTHETIC`, never `VERIFIED`. |
| `orchestration/dispatch.py` | `run_any_mission()` - classify, then route L0/L1/L2 retrieval to the `lookup_v1` fast path, diagnostic/predictive/prescriptive to the swarm. |
| `data/fixtures/scenarios/cac_regression.json` | Reference scenario: Sept-1 deploy broke mobile CVR; media healthy. |

## Plugging in real data / models

Implement the Protocol, pass a `ProviderBundle`:

```python
from seleric_swarm.swarm.providers.base import ProviderBundle
from seleric_swarm.swarm.orchestrator import run_swarm_mission

bundle = ProviderBundle(
    data={"performance": MyMetaMCPProvider(), "funnel": MyPostHogProvider(), ...},
    anomaly=MySTLDetector(),
    causal=MyDoWhyEngine(),        # wraps causal/dowhy_service.py
    forecaster=MyModelRegistryForecaster(),
    optimizer=MyRulesOptimizer(),
    stats=MyStatsmodelsEngine(),
)
result = await run_swarm_mission(runtime, query=..., providers=bundle)
```

Agents never change. The `data_origin`/`synthetic` markers on your real artifacts
become `MCP`/`MODEL` + `synthetic=false`, and the synthesizer stops printing the
banner / SYNTHETIC labels.

## Reference mission (the proof it is dynamic, not scripted)

> Why has our CAC increased, what happens if this continues, and what should we do?

```
performance_agent  (lead)
  observer  -> Performance evidence + sentinel scan of metric.purchase_cvr (funnel)
  anomaly   -> cac +29.5%, purchase_cvr -24.2%   (cpm/cpc/ctr within band)
  Performance.evaluate_handoff: my frontier is quiet, purchase_cvr degraded downstream
  -> HANDOFF -> funnel_agent          (LeadershipManager accepts: evidence-backed, no loop)

funnel_agent  (lead, epoch 1)
  observer  -> funnel evidence incl. device=mobile + sentinel scan of LCP / JS errors (technical)
  anomaly   -> mobile purchase_cvr -31%, mobile_lcp +164%, js_error_rate +771%
  Funnel.evaluate_handoff: stage transitions quiet, technical signals degraded
  -> HANDOFF -> technical_agent        (epoch 2)

technical_agent  (lead, terminal - no downstream)
  diagnostic -> 5 explicit hypotheses; primary = "deploy DEP-4471 raised mobile
                latency/JS errors, degrading mobile CVR"; CausalEngine validates
                (effect -0.62, refutations pass) -> hypothesis RETAINED, others REJECTED
  prediction -> CAC ~815 in 7d; ~325 orders lost
  strategy   -> Optimizer ranks mechanism-fit first -> Roll back DEP-4471 / hotfix
  skeptic    -> runs 9 attacks, StatsEngine checks pass -> PASS
  synthesis  -> SYNTHETIC-banner answer
```

If media metrics are *not* quiet (test `test_no_anomaly_scenario_keeps_leadership`),
Performance keeps leadership - the transfer is produced by evidence, not a fixed list.

## What is NOT built yet (fill using the reference pattern)

- Commerce / Finance / Inventory / Procurement domains: configs exist, no scenario
  data or provider slices.
- A2A-over-HTTP transport + Agent Cards (in-process only for v1).
- Real LLM components inside specialists (hypothesis generation is template rules).
- Conflict Resolver, cross-domain direct A2A between two domain agents mid-mission.
