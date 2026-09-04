# 10 - Testing

```
uv run pytest tests/skeptic -q          # 22 tests, deterministic, no network
uv run pytest -q                        # full repo suite (118)
uv run ruff check src/seleric_swarm/agents/skeptic tests/skeptic
uv run mypy src/seleric_swarm/agents/skeptic
```

## Files

| file | covers |
| --- | --- |
| `tests/skeptic/conftest.py` | builders: `evidence`, `causal_artifact`, `forecast_artifact`, `strategy_artifact`, `anomaly_artifact`; `make_agent` fixture with seeded in-memory registries |
| `tests/skeptic/test_skeptic_agent.py` | the 14 numbered spec scenarios + sec 56 / 57 / 58 end-to-end + determinism |
| `tests/skeptic/test_units.py` | claim parser (legacy shape), classifier mismatch flag, risk-class floors, validator routing, A2A adapter |

## Scenario map (spec sec. 55)

| # | scenario | expected |
| --- | --- | --- |
| 1 | numeric + valid evidence | PASS |
| 2 | numeric, no evidence | REVISE / REJECT (policy) |
| 3 | same metric name, different definitions | REVISE, `metric_semantic_conflict`, **not** factual |
| 4 | anomaly from insufficient sample | REVISE |
| 5 | causal claim, no causal artifact | REVISE (blocking gap) |
| 6 | causal effect precedes treatment | REJECT (blocking `temporal`) |
| 7 | causal passes refutation | PASS, trust >= PROBABLE |
| 8 | forecast, drift = red | REJECT (blocking `model`) |
| 9 | forecast, no model metadata | REVISE |
| 10 | scale spend while stock cover critical | REJECT + inventory follow-up |
| 11 | strategy does not address diagnosed cause | REJECT (blocking `strategy`) |
| 12 | conflicting source data | REVISE, `source` challenge + `cross_source_reconciliation` task |
| 13 | unresolved alternative hypothesis | REVISE |
| 14 | full graph | deterministic `SkepticVerdict` structure |
| 56 | reference mission (mobile latency -> CVR -> CAC) | PASS, STRONG, confounding limitation |
| 57 | creative fatigue vs auction pressure not isolable | REVISE + follow-ups |
| 58 | checkout bug diagnosis + cut Meta budget | REJECT (mechanism mismatch) |

All fixtures are SYNTHETIC; a run over synthetic inputs adds the limitation
*"treat the verdict as a methodology check, not a business conclusion"*.
