"""Bridge: run the full Diagnostic subsystem inside the two-axis swarm loop.

``SwarmDiagnosticSpecialist`` matches the swarm specialist interface
(``agent_id`` / ``produces`` / ``policy`` / ``run(blackboard, mission)``) but
delegates to ``agents.diagnostic.DiagnosticAgent``, then writes the equivalent
``Hypothesis`` + ``Causal`` Blackboard artifacts the synthesizer, completion gate
and existing tests expect.

Enable per run: ``run_swarm_mission(runtime, query=..., scenario_id=..., full_diagnostic=True)``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

log = logging.getLogger(__name__)

from seleric_swarm.agents.diagnostic.agent import DiagnosticAgent, diagnostic_deps_from_blackboard
from seleric_swarm.agents.diagnostic.context import DiagnosticDeps
from seleric_swarm.agents.diagnostic.contracts import DiagnosticRequest, DiagnosticResult
from seleric_swarm.agents.diagnostic.ontology import (
    graph_id_for_outcome,
    metric_confounders_to_fetch,
)
from seleric_swarm.agents.diagnostic.policies import DiagnosticPolicies
from seleric_swarm.agents.diagnostic.reasoning import LLMPortReasoningModel, NullReasoningModel
from seleric_swarm.config.settings import configured_chat_model
from seleric_swarm.agents.diagnostic.registries import (
    TemplateCausalEstimationService,
    causal_graphs_from_yaml,
)
from seleric_swarm.agents.diagnostic.services.dowhy_estimation import DoWhyCausalEstimationService
from seleric_swarm.coordinator.leadership.frontier import LeadershipController
from seleric_swarm.swarm.artifacts import Causal, Hypothesis
from seleric_swarm.swarm.blackboard import Blackboard
from seleric_swarm.swarm.mission import SwarmMission

if TYPE_CHECKING:
    from seleric_swarm.runtime import SwarmRuntime


class SwarmDiagnosticSpecialist:
    agent_class = "specialist"
    agent_id = "diagnostic_agent"
    capability = "causal_diagnosis"
    produces = "causal"

    def __init__(
        self,
        providers: Any = None,
        *,
        scenario: dict[str, Any] | None = None,
        deps: DiagnosticDeps | None = None,
        policies: DiagnosticPolicies | None = None,
        ontology: Any = None,
        trace_base: dict[str, str | None] | None = None,
        leadership: LeadershipController | None = None,
        runtime: SwarmRuntime | None = None,
    ) -> None:
        self.providers = providers
        self._scenario = scenario or {}
        self._deps = deps
        self._policies = policies or DiagnosticPolicies.load()
        self._ontology = ontology
        self._leadership = leadership
        self._runtime = runtime
        # Trace base for LangSmith (request_id, session_id, workflow, etc.)
        # Passed by coordinator graph; used when creating LLMPortReasoningModel.
        self._trace_base = trace_base or {}

    def policy(self, blackboard: Blackboard, mission: SwarmMission) -> bool:
        return mission.wants("diagnostic") and bool(blackboard.by_type("anomaly"))

    def _reasoning_for(self, mission_id: str):
        if self._deps is not None:
            return self._deps.reasoning
        runtime = self._runtime
        model = configured_chat_model(runtime.settings) if runtime is not None else ""
        if runtime is not None and model:
            return LLMPortReasoningModel(
                runtime.llm,
                model=model,
                mission_id=mission_id,
                request_id=self._trace_base.get("request_id"),
                session_id=self._trace_base.get("session_id"),
                workflow_name=self._trace_base.get("workflow_name"),
                workflow_version=self._trace_base.get("workflow_version"),
            )
        return NullReasoningModel()

    async def run(self, blackboard: Blackboard, mission: SwarmMission) -> list[str]:
        # Idempotent re-run: replace this agent's prior output, don't accumulate.
        # NOTE: This discards artifacts unconditionally. In a Skeptic-driven re-
        # diagnosis, prior artifacts may be referenced by claims in ClaimManager,
        # leading to dangling support_refs. The ClaimManager is owned by the
        # coordinator graph and not accessible here. Callers that need claim-ref
        # safety should either (a) pass a claim_ref predicate here, or (b) reset
        # affected claims when triggering a re-diagnosis. Tracked as a known
        # limitation until the swarm architecture passes governance context.
        blackboard.discard_by(created_by="diagnostic_agent", artifact_types=("hypothesis", "causal"))
        # Causal service selection:
        # - Fixture/test mode: scenario has causal_truth → use template service
        # - Production mode: no fixture → use DoWhy with template fallback
        causal_truth = self._scenario.get("causal_truth")
        primary_metric = _mission_outcome_metric(mission, blackboard)
        observations = None
        graphs = (
            self._deps.causal_graphs if self._deps is not None else causal_graphs_from_yaml()
        )
        metrics = self._deps.metrics if self._deps is not None else (
            self._runtime.metrics if self._runtime is not None else None
        )
        if causal_truth:
            causal_service = TemplateCausalEstimationService(causal_truth)
        else:
            causal_service = DoWhyCausalEstimationService(
                fallback=TemplateCausalEstimationService({}),
            )
            if primary_metric and metrics is not None and self._runtime is not None:
                await _seed_dependency_evidence(
                    blackboard, mission, primary_metric, metrics, self._runtime.business_state
                )
            treatments = _ranked_treatment_ids(blackboard, outcome=primary_metric)
            graph = graphs.get(graph_id_for_outcome(primary_metric)) if primary_metric else None
            observations = await _fetch_observations(
                self.providers,
                primary_metric,
                dict(mission.time_range),
                extra_metrics=treatments,
                confounder_metrics=metric_confounders_to_fetch(
                    graph, outcome=primary_metric, treatments=treatments, metrics=metrics
                ),
                business_state=self._runtime.business_state if self._runtime else None,
            )

        if self._deps is not None:
            base = self._deps
        else:
            ontology = self._ontology
            if ontology is None and self._runtime is not None:
                ontology = self._runtime.ontology
            base = DiagnosticDeps(
                causal_graphs=graphs,
                causal_service=causal_service,
                ontology=ontology,
                reasoning=self._reasoning_for(blackboard.mission_id),
                metrics=metrics,
            )
        deps = diagnostic_deps_from_blackboard(blackboard, base=base)
        agent = DiagnosticAgent(deps=deps, policies=self._policies)

        context: dict[str, Any] = {
            # Only fixture/replay mode (a declared causal_truth) gets the
            # metadata-confidence ceiling lifted -- estimator.py otherwise
            # caps a no-observations live run at PLAUSIBLE_CAUSAL /
            # inconclusive, which is the honest outcome when DoWhy had no
            # series to estimate from.
            "trust_metadata_causal": bool(causal_truth),
        }

        request = DiagnosticRequest(
            mission_id=blackboard.mission_id,
            question=mission.query,
            primary_metric=primary_metric,
            lead_domain=blackboard.mission_lead,
            time_range=dict(mission.time_range),
            degradation_started_at=mission.context.get("degradation_started_at"),
            observations=observations,
            context=context,
        )
        result: DiagnosticResult = await agent.diagnose(request)

        posted, retained_ids = _write_artifacts(blackboard, result)
        obs_rows = len(observations) if observations is not None else 0
        if obs_rows == 0 and not causal_truth:
            log.warning(
                "diagnostic: no causal observations for '%s' — DoWhy used template fallback "
                "(time_range=%s). Extend the mission window or check MCP series availability.",
                primary_metric,
                dict(mission.time_range),
            )
        blackboard.record_event(
            "diagnostic_done",
            hypotheses=len(result.hypotheses),
            retained=retained_ids,  # Blackboard artifact ids (not the internal ones)
            causal_confidence=result.finding.causal_confidence if result.finding else None,
            incident_type=result.incident_type,
            contradictions=len(result.contradictions),
            causal_obs_rows=obs_rows,
        )
        if result.leadership_transfer_recommended:
            blackboard.record_event(
                "leadership_transfer_recommended",
                current_lead=blackboard.mission_lead,
                recommended_lead=result.recommended_domain_lead,
                reason=result.leadership_transfer_reason,
            )
            # Diagnostic proposes; the Coordinator's LeadershipController still
            # arbitrates (evidence required, loop/hysteresis checks) before it
            # takes effect. Evidence refs are the retained hypothesis IDs the
            # recommendation was based on.
            if self._leadership is not None and result.recommended_domain_lead:
                evidence_refs = retained_ids or posted
                decision = self._leadership.decide_transfer(
                    blackboard.leadership_state(),
                    {
                        "mission_id": blackboard.mission_id,
                        "from_agent": blackboard.mission_lead,
                        "to_agent": result.recommended_domain_lead,
                        "requested_target": result.recommended_domain_lead,
                        "reason": result.leadership_transfer_reason or "",
                        "evidence_refs": evidence_refs,
                        "unresolved_question": result.leadership_transfer_reason or "",
                    },
                )
                if decision.get("accepted"):
                    blackboard.apply_transfer(decision["handoff_history"][-1])
                else:
                    blackboard.record_event(
                        "leadership_transfer_rejected",
                        reason=decision.get("error_message"),
                        error_code=decision.get("error_code"),
                    )
        return posted


_CAUSAL_EXTRA_HISTORY_DAYS: int = 30
"""Look-back extension (days) added before the mission start for DoWhy observations.

A user query window can be as short as 1 day ("yesterday") or 7 days ("last week"),
both below the 8-row floor that ``fetch_series`` enforces.  Extending by 30 days gives
DoWhy ≥30 pre-treatment observations — enough for a non-degenerate causal estimate —
while keeping the extension well below ``fetch_series``'s 60-day upper cap.

The user's visible answer is still anchored to their original query window; only the
causal evidence layer uses the extended history.
"""

# ``fetch_series`` is one MCP call per metric (granularity=day). Cap treatments;
# route by owner so a short question does not fan out across every peer KPI.
_MAX_CAUSAL_TREATMENTS = 3


def _mission_outcome_metric(mission: SwarmMission, blackboard: Blackboard) -> str:
    """Outcome series to fetch for DoWhy.

    Intake can leave ``primary_metric`` empty when the asked KPI is catalogue-
    only (not in the YAML registry). Diagnostic still resolves that KPI from
    anomalies; observations must use the same id or DoWhy gets no rows.
    """
    ctx = mission.context or {}
    for key in ("primary_metric", "resolved_metric"):
        value = str(ctx.get(key) or "").strip()
        if value:
            return value
    for hint in ctx.get("metric_hints") or []:
        value = str(hint or "").strip()
        if value:
            return value
    scored: dict[str, float] = {}
    for row in blackboard.by_type("anomaly"):
        mid = str(row.get("metric_id") or "").strip()
        if not mid or mid.startswith("event."):
            continue
        try:
            scored[mid] = max(scored.get(mid, 0.0), abs(float(row.get("deviation_pct") or 0.0)))
        except (TypeError, ValueError):
            scored.setdefault(mid, 0.0)
    if scored:
        return max(scored, key=scored.get)
    for row in blackboard.by_type("evidence"):
        mid = str(row.get("metric_id") or row.get("metric_or_fact") or "").strip()
        if mid and not mid.startswith("event."):
            return mid
    return ""


async def _seed_dependency_evidence(
    blackboard: Blackboard,
    mission: SwarmMission,
    outcome_metric: str,
    metrics: Any,
    business_state: Any,
) -> None:
    """Fall back to the outcome metric's own formula dependencies as treatment
    candidates when nothing else co-moved.

    A single-metric "why" question (e.g. "why has CAC increased") only ever
    gets the outcome itself on the blackboard -- Observer fetches what the
    classifier asked for, nothing more. ``generate_hypotheses`` then has zero
    treatment candidates and posts zero hypotheses, even though CAC's own
    ``depends_on`` (total_ad_spend, new_customers) are real, fetchable
    co-movers. Post them as ordinary evidence so hypothesis generation and
    ``_ranked_treatment_ids`` (both of which already scan blackboard evidence)
    pick them up for free -- no changes needed there.
    """
    if business_state is None or _ranked_treatment_ids(blackboard, outcome=outcome_metric):
        return  # already have real co-movers; don't dilute with formula deps
    definition = metrics.get(outcome_metric)
    if definition is None:
        return
    depends_on = [str(d) for d in (definition.raw.get("depends_on") or []) if d]
    if not depends_on:
        return

    from seleric_swarm.contracts.lookup import TimeRangeV1
    from seleric_swarm.domain.models import StateRequest
    from seleric_swarm.swarm.artifacts import Evidence

    try:
        time_range = TimeRangeV1.model_validate(dict(mission.time_range))
    except Exception:
        return
    have = {row.get("metric_or_fact") for row in blackboard.by_type("evidence")}

    async def _fetch(dep_id: str) -> tuple[str, Any] | None:
        dep_def = metrics.get(dep_id)
        if dep_def is None:
            return None
        request = StateRequest(
            metric_id=dep_id,
            time_range=time_range,
            agent_id=f"{dep_def.domain}_agent",
            need=["actual"],
        )
        try:
            state = await business_state.get_metric_state(request)
        except Exception:  # noqa: S112 - best-effort seed; missing data just skips this dep
            return None
        if state.status == "UNAVAILABLE" or state.actual is None:
            return None
        return dep_id, state.actual

    candidates = [d for d in depends_on[:_MAX_CAUSAL_TREATMENTS] if d != outcome_metric and d not in have]
    if not candidates:
        return
    import asyncio

    for result in await asyncio.gather(*(_fetch(dep_id) for dep_id in candidates)):
        if result is None:
            continue
        dep_id, actual = result
        blackboard.post(
            Evidence.new(
                mission_id=blackboard.mission_id,
                created_by="diagnostic_agent",
                metric_or_fact=dep_id,
                value=actual,
                data_origin="BUSINESS_STATE",
                synthetic=False,
            )
        )


def _ranked_treatment_ids(
    blackboard: Blackboard, *, outcome: str, limit: int = _MAX_CAUSAL_TREATMENTS
) -> list[str]:
    """Loudest co-movers only — not the full peer-probe catalogue."""
    scored: dict[str, float] = {}
    for row in blackboard.by_type("anomaly"):
        mid = str(row.get("metric_id") or "")
        if not mid or mid == outcome or mid.startswith("event."):
            continue
        try:
            scored[mid] = max(scored.get(mid, 0.0), abs(float(row.get("deviation_pct") or 0.0)))
        except (TypeError, ValueError):
            scored.setdefault(mid, 0.0)
    for row in blackboard.by_type("evidence"):
        mid = str(row.get("metric_id") or row.get("metric_or_fact") or "")
        if not mid or mid == outcome or mid.startswith("event."):
            continue
        try:
            score = abs(float(row.get("change_pct") or 0.0))
        except (TypeError, ValueError):
            score = 0.0
        scored[mid] = max(scored.get(mid, 0.0), score)
    return sorted(scored, key=lambda mid: -scored[mid])[:limit]


def _providers_for_metrics(providers: Any, metric_ids: set[str]) -> list[tuple[Any, list[str]]]:
    """Each metric goes to its owning domain's ``fetch_series`` only."""
    data = getattr(providers, "data", {}) or {}
    by_id: dict[int, tuple[Any, list[str]]] = {}
    fallback = next((p for p in data.values() if getattr(p, "fetch_series", None)), None)
    for mid in sorted(metric_ids):
        domain = None
        for provider in data.values():
            registry = getattr(provider, "_metrics", None)
            if registry is None:
                continue
            definition = registry.get(mid)
            if definition is not None:
                domain = getattr(definition, "domain", None)
                break
        provider = data.get(domain) if domain else None
        if provider is None or getattr(provider, "fetch_series", None) is None:
            provider = fallback
        if provider is None:
            continue
        key = id(provider)
        if key not in by_id:
            by_id[key] = (provider, [])
        by_id[key][1].append(mid)
    return list(by_id.values())


async def _fetch_observations_bss(
    business_state: Any,
    needed: set[str],
    outcome_metric: str,
    time_range: dict[str, Any],
) -> Any:
    """Per-day observation frame from BusinessStateService (03 §1), the same
    ``MetricState.series`` the lookup fast path already trusts, instead of a
    second MCP round-trip via ``fetch_series``.

    One ``get_metric_state`` call per metric (need=["actual", "anomaly"]);
    joined on date. Returns ``None`` on anything short of a usable frame so
    the caller can fall back to MCP ``fetch_series`` -- never partially wired.
    """
    if business_state is None:
        return None
    from seleric_swarm.contracts.lookup import TimeRangeV1
    from seleric_swarm.domain.models import StateRequest

    try:
        range_v1 = TimeRangeV1.model_validate(time_range)
    except Exception:
        return None

    columns: dict[str, dict[str, float]] = {}
    for mid in needed:
        definition = business_state.runtime.metrics.get(mid)
        if definition is None:
            continue
        request = StateRequest(
            metric_id=mid,
            time_range=range_v1,
            agent_id=f"{definition.domain}_agent",
            need=["actual", "anomaly"],
        )
        try:
            state = await business_state.get_metric_state(request)
        except Exception:  # noqa: S112 - BSS soft-fail; MCP fallback still available
            continue
        if state.status == "UNAVAILABLE" or not state.series:
            continue
        columns[mid] = {p.ts[:10]: p.value for p in state.series if p.value is not None}

    if outcome_metric not in columns or len(columns) < 2:
        return None

    import pandas as pd

    frame = pd.DataFrame(columns).dropna(how="any")
    # Same 8-row floor _fetch_observations already enforces for the MCP path.
    return frame if len(frame) >= 8 else None


async def _fetch_observations(
    providers: Any,
    outcome_metric: str,
    time_range: dict[str, Any],
    *,
    extra_history_days: int = _CAUSAL_EXTRA_HISTORY_DAYS,
    extra_metrics: set[str] | list[str] | None = None,
    confounder_metrics: set[str] | list[str] | None = None,
    business_state: Any = None,
) -> Any:
    """Real per-day observation series for DoWhy (docs/44 ROB-002).

    Tries ``BusinessStateService.get_metric_state`` first (same series the
    lookup fast path already trusts); only falls back to MCP ``fetch_series``
    via ``providers`` when BSS is unavailable or returns too little history.
    ``providers`` is a ``ProviderBundle`` (domain -> DataProvider). Only
    ``HybridMcpDataProvider`` implements ``fetch_series``; fixture/template
    providers don't, and that's fine — this stays ``None`` for them, same as
    before this fix (metadata-only estimation, honestly capped).

    ``extra_history_days`` is added before the mission start so that short
    user query windows (e.g. 7 days) do not fall below the 8-row floor that
    ``fetch_series`` enforces.  The causal estimate covers this extended period;
    the user-facing answer remains scoped to the original query window.

    ``extra_metrics`` are co-movers already observed on the blackboard (evidence
    / anomalies). Treatments are whatever actually moved, not a YAML seed list.
    ``confounder_metrics`` are fetchable graph common-ancestors for those
    treatment→outcome pairs — not an RCA template.
    """
    if not outcome_metric:
        return None

    # Extend backwards so DoWhy gets enough context for a stable estimate.
    # fetch_series rejects windows shorter than 8 days; a 30-day extension
    # ensures we always exceed that floor even for single-day queries.
    from datetime import date, timedelta

    start_str = str(time_range.get("start") or "")[:10]
    try:
        extended_start = (
            date.fromisoformat(start_str) - timedelta(days=extra_history_days)
        ).isoformat()
    except ValueError:
        # Malformed date — fall back to original range (fetch_series will
        # return None if it is too short, same as before this change).
        extended_start = start_str

    causal_time_range = {**time_range, "start": extended_start}
    log.debug(
        "_fetch_observations: mission window %s→%s extended to %s→%s (+%d days) for DoWhy",
        time_range.get("start"),
        time_range.get("end"),
        extended_start,
        time_range.get("end"),
        extra_history_days,
    )

    extras: list[str] = []
    seen_extra: set[str] = set()
    for mid in extra_metrics or ():
        key = str(mid)
        if (
            not key
            or key.startswith("event.")
            or key == outcome_metric
            or key in seen_extra
        ):
            continue
        seen_extra.add(key)
        extras.append(key)
        if len(extras) >= _MAX_CAUSAL_TREATMENTS:
            break
    needed = {outcome_metric, *extras}
    for mid in confounder_metrics or ():
        key = str(mid)
        if key.startswith("metric.") and key not in needed:
            needed.add(key)

    log.info(
        "_fetch_observations: fetching %d series metrics over %s→%s (treatments capped at %d)",
        len(needed),
        extended_start,
        time_range.get("end"),
        _MAX_CAUSAL_TREATMENTS,
    )

    bss_frame = await _fetch_observations_bss(business_state, needed, outcome_metric, causal_time_range)
    if bss_frame is not None:
        return bss_frame

    if providers is None:
        return None

    seen: set[int] = set()
    frame = None
    for provider, metric_ids in _providers_for_metrics(providers, needed):
        fetch_series = getattr(provider, "fetch_series", None)
        if fetch_series is None or id(provider) in seen:
            continue
        seen.add(id(provider))
        try:
            candidate = await fetch_series(metric_ids=sorted(metric_ids), time_range=causal_time_range)
        except Exception:  # noqa: S112 - provider soft-fail; try next source
            continue
        if candidate is None:
            continue
        if frame is None:
            frame = candidate
        else:
            import pandas as pd

            frame = pd.concat([frame, candidate], axis=1, join="outer")
            frame = frame.loc[:, ~frame.columns.duplicated()]
    if frame is None or outcome_metric not in frame.columns or len(frame.columns) < 2:
        return None
    frame = frame.dropna(how="any")
    # Same 8-row floor as fetch_series's own min_rows default — merging
    # per-provider frames on an outer join can drop rows back below it.
    return frame if len(frame) >= 8 else None


def _write_artifacts(blackboard: Blackboard, result: DiagnosticResult) -> tuple[list[str], list[str]]:
    id_map: dict[str, str] = {}
    posted: list[str] = []
    for h in result.hypotheses:
        art = Hypothesis.new(
            mission_id=blackboard.mission_id,
            created_by="diagnostic_agent",
            statement=h.statement,
            domains=h.domains,
            status="retained" if h.status == "retained" else ("rejected" if h.status == "rejected" else "testing"),
            supporting_evidence=h.supporting_evidence,
            required_tests=h.required_tests,
            score=h.posterior_score,
            evidence_refs=h.supporting_evidence,
        )
        if h.synthetic or result.synthetic:
            art.mark_synthetic()
        hid = blackboard.post(art)
        id_map[h.hypothesis_id] = hid
        posted.append(hid)

    # One Blackboard Causal per accepted finding — a secondary retained finding
    # must not leave its claim's causal_ref dangling (spec §54-55).
    findings_by_ref = {f.causal_ref: f for f in result.findings if f.causal_ref}
    to_post = result.causal_artifacts or ([result.causal_artifact] if result.causal_artifact else [])
    fallback_hyp = next((h for h in result.hypotheses if h.status == "retained"), None) or (
        result.hypotheses[0] if result.hypotheses else None
    )
    for ca in to_post:
        finding = findings_by_ref.get(ca.causal_id)
        hyp = None
        if finding and finding.retained_hypothesis_id:
            hyp = next(
                (h for h in result.hypotheses if h.hypothesis_id == finding.retained_hypothesis_id), None
            )
        hyp = hyp or fallback_hyp
        confidence = (
            finding.causal_confidence
            if finding
            else (result.finding.causal_confidence if result.finding else "")
        )
        causal = Causal.new(
            mission_id=blackboard.mission_id,
            created_by="diagnostic_agent",
            hypothesis_ref=id_map.get(hyp.hypothesis_id) if hyp else None,
            treatment=ca.treatment,
            outcome=ca.outcome,
            common_causes=ca.common_causes,
            graph_id=ca.graph_id,
            estimator=ca.estimator,
            effect=ca.estimated_effect,
            effect_ci=ca.confidence_interval,
            refutations=ca.refutation_results,
            passed=ca.passed and confidence != "REJECTED",
            confidence=confidence,
            evidence_refs=[id_map[h.hypothesis_id] for h in result.retained() if h.hypothesis_id in id_map],
        )
        if ca.synthetic or result.synthetic:
            causal.mark_synthetic()
        posted.append(blackboard.post(causal))

    retained_ids = [id_map[h.hypothesis_id] for h in result.retained() if h.hypothesis_id in id_map]
    return posted, retained_ids
