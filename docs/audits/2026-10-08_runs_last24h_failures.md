# Seleric_Agent run failures — last 24 hours

**Window:** 2026-10-07 19:11 UTC → 2026-10-08 19:11 UTC  
**Source:** `seleric_swarm` Postgres (`runs`, `run_attempts`, `run_events`, `missions`)  
**Queried at:** 2026-10-08 19:11 UTC

---

## Headline

| Scope | Completed | Failed | Partial | Cancelled | Notes |
| --- | ---: | ---: | ---: | ---: | --- |
| Runs | 111 | **5** | — | 1 | 3 additional runs completed after retry |
| Missions | 162 | **6** | **16** | 1 | 1 failed mission has no linked `runs` row |
| Tool calls | 460 ok | **33 fail** | — | — | 6.7% tool failure rate |

**Terminal hard failures are dominated by answer validation**, not missing data. In every `INSUFFICIENT_EVIDENCE` failure below, `query_metrics` (or prefetch) had already returned live rows; the V3 validator rejected the final answer after revision budget was exhausted (or rejected as unsalvageable).

---

## Root-cause taxonomy

### RC-1 — Answer grounding / table-total validation (PRIMARY)

**Surfaces as:** mission `failed` + `error_code=INSUFFICIENT_EVIDENCE`, or mission `partial` + `VALIDATION_REVISIONS_EXHAUSTED`.

**Mechanism:** After the agent drafts `final_response`, `agent/validation` checks that:
1. every reported figure appears in fetched/derived evidence,
2. stated totals match the sum of the printed table,
3. named entities/filters in the question are covered by evidence scope,
4. `evidence_ids` cite real artifacts.

When revisions (up to 4) cannot fix it, `_exhausted` ships `partial` if a salvageable draft + mission evidence exist; otherwise `failed` with the generic “could not back this answer…” message. The real reason is in `result_json.trace.validation.reason` / `limitations[1]`.

**Contributing agent mistakes that feed RC-1:**
- Aggregating channel/campaign rows in prose without `run_python` recording the derived totals (validator then cannot ground them).
- Printing a “total” that is actually a row count / wrong column while the table sums revenue.
- Passing `finding` / `chart_spec` ids into `analyze` / `run_python` / `generate_visualization` (needs `artifact_type=evidence`) → derived figures never get recorded → later grounding fails.

### RC-2 — Artifact-type misuse (tool-level)

**Count:** 20 of 33 tool failures (analyze=10, run_python=7, generate_visualization=3).  
**Error shape:** `artifact … is artifact_type='finding'/'chart_spec', not evidence`.  
**Code:** `toolsets/analytics.py` `_resolve_evidence` refuses non-evidence artifacts (retryable when backing `evidence_ids` exist).

### RC-3 — query_metrics catalogue / dimension / empty-window errors

**Count:** 11 tool failures.

| Pattern | Example |
| --- | --- |
| Invalid filter dimension | `Filter dimension 'ad_spend' is not valid on view 'order_pnl'/'product'` (treated as filter instead of metric) |
| Invalid filter dimension | `Filter dimension 'new_customers' is not valid on view 'unit_economics'` |
| Unknown dimension value | `Value 'google_organic' is not a known value for dimension 'finance_channel'` |
| Empty window | `no data for net_sales over 2026-10-08..2026-10-08`; product_* metrics empty for some product×window slices |

These usually did **not** alone terminate the run (agent often recovered), but they degraded multi-metric questions and fed RC-1.

### RC-4 — Diagnose window policy

**Count:** 2 tool failures.  
**Error:** `event window … is 30 days; diagnose at most 14 days at a time`.  
One run (`run_56d835f1…`) also hit `LLM_RATE_LIMITED` on attempt 1 while iterating shorter windows; attempt 2 completed.

### RC-5 — Transient execution / LLM (recovered)

| error_message | Attempts | Final run status |
| --- | ---: | --- |
| `V3_AGENT_FAILED` | 2 | both COMPLETED on attempt 2 |
| `LLM_RATE_LIMITED` | 1 | COMPLETED on attempt 2 |

Docker container logs for the recovery worker did not retain the underlying exception text (rotated / low retention). Langfuse legacy traces API is unavailable for this project; no `langfuse_trace_id` was stored on those missions.

### RC-6 — Client cancel

1 run/mission cancelled ~3s after start: query “which ads and campaign performance are defoliating”.

---

## Terminal FAILED runs (5)

### 1. `run_60ffe54627cf49acacd03e71b22b8eed` / `MS3-c410a03d38`

| Field | Value |
| --- | --- |
| When (UTC) | 2026-10-07 19:52 → 19:54 (132s) |
| Thread | `thread_54c3451314904d57aeae034fa116c651` |
| Query | Analyze Pawveralls Suspender Boots … 60 days … reconcile attributed vs total |
| Attempt error | `INSUFFICIENT_EVIDENCE` |
| Tool noise | `run_python` ×2 refused finding artifact `artifact_6f8ab1b4…` |
| Validation trail | rev0–1: ungrounded figures (925892, 571021, …); rev2–3: table total 7,644.06 vs table sum 1,851,784.60 |
| **Root cause** | **RC-1** — agent computed channel rollups in prose (and failed `run_python` on findings); validator rejected after 4 revisions. Data *was* fetched (`product_net_revenue` 21 rows). |

### 2. `run_e568637c22fb4c2cb9bacbae1cd15a18` / `MS3-871d7f81fd`

| Field | Value |
| --- | --- |
| When (UTC) | 2026-10-07 21:59 → 22:01 (107s) |
| Thread | `thread_cbe854693bca4b5896c3fa567d4e6177` |
| Query | Plot orders + revenue trend for these campaigns, last 2 months |
| Attempt error | `INSUFFICIENT_EVIDENCE` |
| Tool noise | `generate_visualization` on finding; later `run_python` on `chart_spec` |
| Validation trail | rev0–2: ungrounded 965 / 1.58e6; rev3: total 239 vs table sum 1,585,828.48 |
| **Root cause** | **RC-1 + RC-2** — viz/python called with wrong artifact types; final answer figures not grounded in evidence. |

### 3. `run_e8cc47328788418d92b11b79dc2b5678` / `MS3-8c381f3641`

| Field | Value |
| --- | --- |
| When (UTC) | 2026-10-08 11:24 → 11:27 (203s) |
| Thread | `thread_6a61cff1e31149269291d88618bd4451` |
| Query | AOV and COD order count by product in September |
| Attempt error | `INSUFFICIENT_EVIDENCE` |
| Prefetch | aov + cod_orders both succeeded (173 product rows each) |
| Validation trail | stated totals 173 / 82 vs table sums ~102,313.42 + 25 |
| **Root cause** | **RC-1** — “total” / summary numbers conflict with printed product table (likely mixing AOV INR with COD counts). Evidence existed. |

### 4. `run_b5feef9bea9649c4b3ce6371190d0283` / `MS3-1204465838`

| Field | Value |
| --- | --- |
| When (UTC) | 2026-10-08 11:39 → 11:43 (233s) |
| Thread | `thread_ab191b2c36fa45ca99278c3e695ba54d` |
| Query | Same Pawveralls Suspender Boots 60-day multi-channel reconcile |
| Attempt error | `INSUFFICIENT_EVIDENCE` |
| Tool noise | `run_python` refused finding `artifact_87bdb694…` |
| Validation trail | all 4 revisions: figures 257856 / 405838 / 205 / 103 not in fetched/derived evidence |
| **Root cause** | **RC-1 + RC-2** — Meta-only slice answered; rollups done in prose after failed python; never recorded as derived evidence. |

### 5. `run_d3b2b8a93cec493c8754d9723e23567c` / `MS3-90cbdba8d7`

| Field | Value |
| --- | --- |
| When (UTC) | 2026-10-08 17:34 → 17:37 (174s) |
| Thread | `thread_f8a28ce953f546338078fe4172848cf0` |
| Query | Same Pawveralls Suspender Boots reconcile |
| Attempt error | `INSUFFICIENT_EVIDENCE` |
| Tool noise | `analyze` refused finding `artifact_16d492a7…` |
| Validation trail | ungrounded 1.27687e6 / 1.63445e6 / 357582 / 21.9% / … |
| **Root cause** | **RC-1 + RC-2** — reconciliation arithmetic not captured via `run_python` on evidence rows. |

---

## Failed mission without FAILED run (1)

### `MS3-6e3080f931` (no `runs` row)

| Field | Value |
| --- | --- |
| When (UTC) | 2026-10-07 21:17 |
| Query | Same Pawveralls Suspender Boots reconcile |
| Error | `INSUFFICIENT_EVIDENCE` |
| Validation | totals 30.00 / 6,955,496 vs table sums 3,625,706 + 2,438,315 + … |
| **Root cause** | **RC-1** (same Pawveralls pattern). Likely a direct `/v1/missions` path or orphaned mission row. |

**Pawveralls Suspender Boots reconcile** accounts for **4 of 6** mission failures in-window.

---

## Recovered attempt failures (not terminal)

| Run | Attempt 1 | Attempt 2 | Query (short) |
| --- | --- | --- | --- |
| `run_3cb2ae06…` | `V3_AGENT_FAILED` | COMPLETED | Compare this month to same days last month |
| `run_7083fac2…` | `V3_AGENT_FAILED` | COMPLETED | “yes” (follow-up) |
| `run_56d835f1…` | `LLM_RATE_LIMITED` | COMPLETED | Diagnose on shorter event windows |

**Root cause:** **RC-5** (transient). Retry path in recovery worker worked as designed.

---

## Cancelled (1)

| Run / Mission | Query | Detail |
| --- | --- | --- |
| `run_06b4bfd2…` / `MS3-81da45c478` | which ads and campaign performance are defoliating | Cancelled by client after ~2.8s; route stayed `pending` |

---

## Partial missions (16) — soft failures

All are V3 validation soft-fails that still returned a draft. Grouped by limitation code / theme:

| Theme | Count | Mission IDs (short) | Root cause |
| --- | ---: | --- | --- |
| Ungrounded / mismatched figures | 4 | `8e62a71a2b`, `4c62daedeb`, `6d88e04cc7`, (+ parts of others) | RC-1 |
| Scope coverage (named filter not applied) | 4 | `d53c9dc83e` (other→payment_method), `c731cf60a4`/`a7d729dac9` (meta filter), `7f27ced677` (earned) | RC-1 scope |
| Cited metric but no value shown | 4 | `b03cb19204`, `01c9d25ea2`, `f23af5a954`, `a434ce5dd1` | RC-1 grounding |
| Missing `evidence_ids` citations | 2 | `e3daf635e1` (+ `MODEL_UNAVAILABLE`), `14b398543a` | RC-1 + RC-5 mid-revision |
| Internal inconsistency across queries | 2 | `f528c2cde3`, `6d88e04cc7` (product_orders day conflicts) | RC-1 consistency |
| Finding used where sums needed | 1 | `007ace6e67` | RC-2 (limitation names finding artifact) |
| Unresolved artifact id typo | 1 | `ed24d4ab92` | RC-1 citation |

---

## All tool failures (33) — compact log

```
analyze / finding-not-evidence                          ×10
run_python / finding-not-evidence                        ×6
run_python / chart_spec-not-evidence                     ×1
generate_visualization / finding-not-evidence            ×3
query_metrics / invalid filter dim ad_spend              ×4
query_metrics / invalid filter dim new_customers         ×1
query_metrics / unknown value google_organic             ×1
query_metrics / empty window (net_sales today / product_*) ×5
diagnose_metric_change / window > 14 days                ×2
```

---

## Fix priorities (from this window)

1. **Force derived math through `run_python` on evidence** (or auto-record rollups) for channel reconciles — kills most Pawveralls hard fails and many partials.
2. **Auto-rewrite finding → backing evidence_ids** inside analyze/run_python/viz instead of hard-refusing (hint already exists; model still ignores it).
3. **Soften / fix table-total validator** when the “total” is a count of rows or a different measure than the summed column (false positives like AOV×COD boards).
4. **Catalogue guardrails:** reject `ad_spend` / metrics-as-dimensions before MCP; map `google_organic` → known `finance_channel` values.
5. Keep retry on `V3_AGENT_FAILED` / `LLM_RATE_LIMITED` (already working); improve logging of the underlying exception into `run_attempts.error_message`.

---

## Appendix — SQL used

```sql
-- status rollup
SELECT status, COUNT(*) FROM runs
WHERE created_at >= NOW() - INTERVAL '24 hours' GROUP BY 1;

SELECT status, COUNT(*) FROM missions
WHERE created_at >= NOW() - INTERVAL '24 hours' GROUP BY 1;

-- failed attempts
SELECT ra.* FROM run_attempts ra
JOIN runs r ON r.id = ra.run_id
WHERE r.created_at >= NOW() - INTERVAL '24 hours'
  AND ra.status IN ('FAILED','CANCELLED');

-- tool failures
SELECT summary FROM run_events
WHERE created_at >= NOW() - INTERVAL '24 hours'
  AND event_type = 'agent.tool_completed'
  AND payload->>'success' = 'false';
```
