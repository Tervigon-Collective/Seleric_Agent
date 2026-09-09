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
from typing import Any

log = logging.getLogger(__name__)

from seleric_swarm.agents.diagnostic.agent import DiagnosticAgent, diagnostic_deps_from_blackboard
from seleric_swarm.agents.diagnostic.context import DiagnosticDeps
from seleric_swarm.agents.diagnostic.contracts import DiagnosticRequest, DiagnosticResult
from seleric_swarm.agents.diagnostic.policies import DiagnosticPolicies
from seleric_swarm.agents.diagnostic.ontology import common_causes_for_outcome, mechanisms_for
from seleric_swarm.agents.diagnostic.registries import (
    TemplateCausalEstimationService,
    causal_graphs_from_yaml,
)
from seleric_swarm.agents.diagnostic.services.dowhy_estimation import DoWhyCausalEstimationService
from seleric_swarm.coordinator.leadership.frontier import LeadershipController
from seleric_swarm.swarm.artifacts import Causal, Hypothesis
from seleric_swarm.swarm.blackboard import Blackboard
from seleric_swarm.swarm.mission import SwarmMission


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
    ) -> None:
        self.providers = providers
        self._scenario = scenario or {}
        self._deps = deps
        self._policies = policies or DiagnosticPolicies.load()
        self._ontology = ontology
        self._leadership = leadership
        # Trace base for LangSmith (request_id, session_id, workflow, etc.)
        # Passed by coordinator graph; used when creating LLMPortReasoningModel.
        self._trace_base = trace_base or {}

    def policy(self, blackboard: Blackboard, mission: SwarmMission) -> bool:
        return mission.wants("diagnostic") and bool(blackboard.by_type("anomaly"))

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
        primary_metric = str(mission.context.get("primary_metric") or "")
        observations = None
        if causal_truth:
            causal_service = TemplateCausalEstimationService(causal_truth)
        else:
            causal_service = DoWhyCausalEstimationService(
                fallback=TemplateCausalEstimationService({}),
            )
            observations = await _fetch_observations(
                self.providers, primary_metric, dict(mission.time_range)
            )

        base = self._deps or DiagnosticDeps(
            causal_graphs=causal_graphs_from_yaml(),
            causal_service=causal_service,
            ontology=self._ontology,
        )
        deps = diagnostic_deps_from_blackboard(blackboard, base=base)
        agent = DiagnosticAgent(deps=deps, policies=self._policies)

        context: dict[str, Any] = {
            # fixture/replay mode: the template causal truth is authoritative
            "trust_metadata_causal": True,
        }
        if observations is not None:
            # DoWhy needs exact column matches — only declare common causes we
            # actually fetched real data for (some ontology common causes,
            # e.g. "campaign"/"device", are categorical dimensions, not
            # metrics fetch_series can pull; dropping the unfetchable ones
            # here beats DoWhy rejecting the whole estimate for missing columns).
            context["common_causes"] = [
                c for c in common_causes_for_outcome(primary_metric) if c in observations.columns
            ]

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
        if obs_rows == 0:
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


async def _fetch_observations(
    providers: Any,
    outcome_metric: str,
    time_range: dict[str, Any],
    *,
    extra_history_days: int = _CAUSAL_EXTRA_HISTORY_DAYS,
) -> Any:
    """Real per-day observation series for DoWhy (docs/44 ROB-002).

    ``providers`` is a ``ProviderBundle`` (domain -> DataProvider). Only
    ``HybridMcpDataProvider`` implements ``fetch_series``; fixture/template
    providers don't, and that's fine — this stays ``None`` for them, same as
    before this fix (metadata-only estimation, honestly capped).

    ``extra_history_days`` is added before the mission start so that short
    user query windows (e.g. 7 days) do not fall below the 8-row floor that
    ``fetch_series`` enforces.  The causal estimate covers this extended period;
    the user-facing answer remains scoped to the original query window.
    """
    if providers is None or not outcome_metric:
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

    needed = {outcome_metric, *common_causes_for_outcome(outcome_metric)}
    needed |= {tmpl.treatment_metric for tmpl in mechanisms_for(outcome_metric)}

    seen: set[int] = set()
    frame = None
    for provider in getattr(providers, "data", {}).values():
        fetch_series = getattr(provider, "fetch_series", None)
        if fetch_series is None or id(provider) in seen:
            continue
        seen.add(id(provider))
        try:
            candidate = await fetch_series(metric_ids=sorted(needed), time_range=causal_time_range)
        except Exception:
            continue
        if candidate is None:
            continue
        if frame is None:
            frame = candidate
        else:
            import pandas as pd

            frame = pd.concat([frame, candidate], axis=1, join="outer")
            frame = frame.loc[:, ~frame.columns.duplicated()]
    if frame is None or outcome_metric not in frame.columns:
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

    ca = result.causal_artifact
    if ca is not None:
        primary_hyp = next((h for h in result.hypotheses if h.status == "retained"), None) or (
            result.hypotheses[0] if result.hypotheses else None
        )
        causal = Causal.new(
            mission_id=blackboard.mission_id,
            created_by="diagnostic_agent",
            hypothesis_ref=id_map.get(primary_hyp.hypothesis_id) if primary_hyp else None,
            treatment=ca.treatment,
            outcome=ca.outcome,
            common_causes=ca.common_causes,
            graph_id=ca.graph_id,
            estimator=ca.estimator,
            effect=ca.estimated_effect,
            effect_ci=ca.confidence_interval,
            refutations=ca.refutation_results,
            passed=ca.passed and (result.finding.causal_confidence != "REJECTED" if result.finding else ca.passed),
            confidence=result.finding.causal_confidence if result.finding else "",
            evidence_refs=[id_map[h.hypothesis_id] for h in result.retained() if h.hypothesis_id in id_map],
        )
        if ca.synthetic or result.synthetic:
            causal.mark_synthetic()
        posted.append(blackboard.post(causal))

    retained_ids = [id_map[h.hypothesis_id] for h in result.retained() if h.hypothesis_id in id_map]
    return posted, retained_ids
