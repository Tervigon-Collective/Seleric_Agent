# 03 — What must be defined to make Business State functional

Schemas and agents already exist for evidence / anomaly / forecast envelopes. What’s missing is the **contracts + policies** between raw MCP rows and a trustworthy `MetricState`.

Without these, implementation would be another Observer wrapper.

## Already defined (reuse)

| Piece | Where |
|---|---|
| Metric identity / catalogue | `MetricRegistry` + MCP catalogue |
| Point / window fetch | Observer `_fetch_seleric` / `seleric.metrics_query` |
| Evidence envelope | `EvidenceArtifact` + `make_evidence` |
| Comparison deltas | Observer `_comparison_deltas` (thin; not full features) |
| Time windows | `services/time_range.py` |
| Anomaly / forecast JSON shapes | `schemas/anomaly_artifact.schema.json`, `forecast_artifact.schema.json` |
| Prediction fallback ladder | `config/prediction_policies.yaml` |
| Claim Gate basics | support_refs / model_ref rules |

---

## Must define (blocking)

### 1. `MetricState` — core output type

Freeze fields first; add `schemas/metric_state.schema.json` + Pydantic model beside `EvidenceArtifact`.

```text
metric_id, catalogue_metric_id
as_of, window {start, end, grain, timezone}
dimensions
actual                 # latest / window aggregate
series[]               # ordered points used for features (optional on cache hits)
features{}             # feature_id → {value, window, strategy_version}
baseline               # optional
anomaly?               # AnomalyArtifact subset
forecast?              # ForecastArtifact subset
freshness              # CURRENT | LATE | STALE | UNKNOWN
finality               # INTRADAY | PROVISIONAL | FINAL
confidence             # 0–1 or tier
direction_bad          # from registry
quality_flags[]
provenance {
  mcp_query_ids[], catalogue_version?, metric_version,
  feature_profile_id, detector_profile_id?, forecast_profile_id?,
  computed_at
}
status                 # OK | PARTIAL | UNAVAILABLE
error_code?            # align with INSUFFICIENT_EVIDENCE etc.
```

### 2. `StateRequest` — input contract

Agents must not invent ad-hoc kwargs.

```text
metric_id | catalogue_metric_id
time_range             # reuse TimeRangeV1
dimensions
agent_id               # MCP allowlist / module pin
as_of?
profile_id?            # feature/detector/forecast bundle
need: [actual, features, anomaly, forecast]
```

### 3. Series contract (largest gap)

Observer today is mostly point / short-window evidence. Features, anomaly, and forecast need a time series.

Define:

- How `metrics_query` is called for history (grain, start/end, dims)
- Normalized series shape: `{ts, value, finality?}` sorted ascending
- Minimum points per capability (`features ≥ N`, `anomaly ≥ N`, `forecast ≥ N`)
- Gap / null policy: pick one — `drop` | `UNAVAILABLE` | (avoid silent forward-fill in V0)
- Max lookback (e.g. 90d) so missions don’t pull unbounded history

**[Validated live, 2026-09-14 — de-risked, not still open]** freshness is
**already computed per query**, no new plumbing needed: every
`metrics_query` response carries `provenance.freshness = {source,
expected_cadence, cube_last_refresh}` plus a top-level `generated_at`.
`MetricState.freshness` (`CURRENT | LATE | STALE | UNKNOWN`) is a threshold
classification of `generated_at - cube_last_refresh` against
`freshness.stale_after_hours` / `late_after_hours` (already in the
`default_v1` profile draft below) — not a value `series.py` has to derive
from raw rows. Also live-confirmed: unscoped queries silently default to
`brand_id=20`, and `catalogue_list_brands` confirms 5 active brands exist
(20/26/25/27/24) — `series.py` must always pass `brand_id` explicitly
(pinned to `"20"` for V0 per scope decision, see
[04](04_DOMAIN_HEALTH_SNAPSHOTS.md#domainstatesnapshot-shape-contract-sketch-not-final)),
never rely on the MCP's silent default, so the pin stays a documented
choice, not an accidental single-brand narrowing with no flag raised.

**[Validated — new rule, add to gap/null policy]** cross-view ratios are
not safe to compute generically. A live `metrics_query(["refund_count",
"orders"])` returned `composed=true` with `parts[]` on two different date
axes (`refund_date` vs `order_date`) and an explicit warning not to join or
sum across parts. Any feature/health-signal that divides two catalogue
metrics (not a single pre-built ratio metric like `net_roas`) must first
check both metrics resolve to the **same view and same date axis** in
`catalogue_get_metric`/`catalogue_search_metrics` output — otherwise reject
with a new flag (e.g. `CROSS_AXIS_RATIO_UNSUPPORTED`) rather than silently
dividing misaligned series.

### 4. Feature / detector / forecast profile (config)

File: `config/business_state_profiles.yaml`

Start with **one** `default_v1` profile:

```yaml
profiles:
  default_v1:
    features:
      - { id: current_value, strategy: current_value }
      - { id: period_delta_pct, strategy: period_delta_pct, window: compare }
      - { id: rolling_mean_7d, strategy: rolling_mean, window: 7d, min_points: 5 }
      - { id: rolling_std_7d, strategy: rolling_std, window: 7d, min_points: 5 }
      - { id: freshness_age, strategy: freshness_age }
    anomaly:
      strategy: robust_zscore
      window: 28d
      min_points: 14
      z_threshold: 3.0
      use_direction_bad: true
    forecast:
      strategy: ewma          # or drift_projection from prediction_policies
      horizon: 7d
      min_points: 8
      interval_z: 1.28
    freshness:
      stale_after_hours: 36
      late_after_hours: 12
```

Per-domain overrides later.

### 5. Quality flags + failure semantics

Freeze the enum Observer / Claim Gate / Skeptic will see:

```text
MISSING_DATA
STALE
LATE
SPARSE_HISTORY          # below min_points
CATALOGUE_MISS
BINDING_UNSUPPORTED_DIM
PARTIAL_SERIES
MCP_ERROR
INSUFFICIENT_EVIDENCE
CROSS_AXIS_RATIO_UNSUPPORTED   # [validated] two metrics needed for a ratio don't share a date axis
```

Rule: any of `MISSING_DATA` / `SPARSE_HISTORY` / `CATALOGUE_MISS` → **no** anomaly/forecast numbers; return `status=UNAVAILABLE` + flags. Never invent.

### 6. Evidence mapping

How `MetricState` becomes existing artifacts (Claim Gate expects refs).

| Evidence `metric_or_fact` | value |
|---|---|
| `{metric_id}` | actual |
| `{metric_id}.feature.{feature_id}` | feature value |
| `{metric_id}.anomaly` | observed / expected / score (or AnomalyArtifact + ref) |
| `{metric_id}.forecast` | point + interval (or ForecastArtifact + `model_ref`) |

- `source`: e.g. `deterministic.business_state`
- `provenance`: profile ids + MCP query ids (Skeptic provenance validator)

### 7. Caller matrix (wiring contract)

| Caller | Calls | Needs |
|---|---|---|
| **Observer** | `get_metric_state(need=[actual, features])` | grounded facts only |
| **Anomaly agent** (`swarm/specialists/anomaly.py`, live) | `get_metric_state(need=[actual, features, anomaly])` | detector → AnomalyArtifact |
| **Prediction agent** (`swarm/specialists/prediction.py`, live) | `get_metric_state(need=[forecast])` or `forecast()` | ForecastArtifact; reuse prediction_policies ladder |
| **Coordinator** | never computes | only routes |

Replace Observer’s ad-hoc `_comparison_deltas` with Business State features once the profile exists (same numbers, one path).

Note: the *live* Anomaly/Prediction agents are `swarm/specialists/anomaly.py` and
`swarm/specialists/prediction.py` (wired via `coordinator/graph.py`) — not the
dead `agents/intelligence/anomaly.py` / `prediction.py` stubs, which just
`return {"status": "not_implemented"}` and aren't imported by the live
mission path. Once `services/business_state/detectors.py` and `forecasts.py`
exist, they become one more selectable `AnomalyDetector` / `Forecaster`
implementation behind the provider seam in §10 — not a rewrite of the
specialists themselves, which already consume providers generically.

### 8. Pilot scope

Pick **2–3 metrics** and freeze acceptance. Suggested:

- `metric.net_sales` (commerce) — catalogue `commerce_net_revenue_daily`
- `metric.spend` or `metric.cac` (performance)

For each, define:

- default window (e.g. last 28d daily)
- expected feature set
- one golden series fixture → expected rolling_mean / z-score / EWMA point
- MCP live smoke: returns `OK` with provenance **or** `UNAVAILABLE` with flags — never crash

### 9. Runtime wiring surface

```text
runtime.business_state: BusinessStateService
```

Boot in `bootstrap.py` beside `mcp` and `metrics`.  
Constructor deps: `mcp`, `metrics`, profiles YAML, clock.  
No new microservice, no new DB required for V0.

### 10. Provider configurability (anomaly / forecast)

`swarm/providers/base.py` already defines `AnomalyDetector` and `Forecaster`
as `typing.Protocol` ports on `ProviderBundle` — the seam is designed to be
pluggable ("swap them for MCP- and model-backed implementations without
touching any agent code," per the module docstring). The gap is that
`build_hybrid_bundle()` (`swarm/providers/mcp_data.py:564-566`)
**hardcodes** the choice:

```python
anomaly=TemplateAnomalyDetector(),   # RelativeEffectAnomalyDetector
forecaster=TemplateForecaster(),
```

There is no config knob to select a different implementation per domain,
metric, or environment. Define, before implementing:

- A provider-selection config (e.g. `config/provider_registry.yaml` or an
  extension of `agent_registry.yaml`) mapping `{domain | metric_id} ->
  {anomaly_strategy, forecast_strategy}`, with a default fallback.
- `build_hybrid_bundle()` reads that config and instantiates the selected
  `AnomalyDetector`/`Forecaster` instead of a hardcoded `Template*` — same
  Protocol, no agent-side change required (`swarm/specialists/anomaly.py`
  and `prediction.py` already call `self.providers.anomaly` /
  `self.providers.forecaster` generically).
- Once `BusinessStateService`'s `detectors.py` (robust z-score) and
  `forecasts.py` (EWMA / drift projection) exist, they register as one more
  selectable strategy behind this same seam — not a competing path.
- Delete the dead `agents/intelligence/anomaly.py` / `prediction.py` stubs
  once confirmed unused, to stop them from reading as "the anomaly agent is
  broken" when the live specialists are fine.

---

## Should define soon (not blocking first PR)

- Finality rules (when is a day FINAL vs PROVISIONAL for commerce vs ads)
- Cache key `(metric_id, window, dims_hash, profile_id)` + TTL
- Align AnomalyArtifact `detector` object with strategy id/version
- Forecast `model_id` = `statistical_baseline:{strategy}` so Claim Gate `model_ref` is always set
- Node / health ontology — skip until Strategy needs it

## Explicitly do not define yet

Control Plane bundles, ClickHouse marts, outbox events, separate BSS deployable, full Feast/feature store, champion/challenger registry.

---

## Definition checklist (order)

1. `MetricState` + `StateRequest` schemas
2. Series fetch + null/gap + min_points rules
3. One `business_state_profiles.yaml` (`default_v1`)
4. Quality flag enum + UNAVAILABLE rules
5. Evidence / Anomaly / Forecast mapping
6. Caller matrix (Observer / Anomaly / Prediction)
7. Pilot metrics + golden fixtures
8. `runtime.business_state` boot
9. Provider-selection config for `AnomalyDetector` / `Forecaster` (§10)

After these nine exist in schemas/config (and this doc), implementation is: facade → MCP series → strategies → evidence.

---

## Suggested first implementation slice

1. Add schemas + `default_v1` profile YAML (no agent change).
2. Implement `series.py` + `get_metric_state(need=[actual, features])` for `metric.net_sales`.
3. Unit test with fixture series.
4. Optional: Observer path behind a flag to use Business State for features instead of `_comparison_deltas`.
5. Then anomaly strategy + register it as a selectable provider (§10) behind
   the existing `AnomalyDetector` Protocol — the live `swarm/specialists/anomaly.py`
   already works, it just needs a config choice instead of the hardcoded
   `TemplateAnomalyDetector`.
