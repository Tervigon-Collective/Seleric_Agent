"""Dynamic two-axis swarm orchestrator (architecture sec. 25, 37, 40).

Assembles a temporary analytical team (Domain lead x Intelligence specialists),
runs an evidence-driven investigation loop where leadership transfers when the
causal frontier crosses a domain boundary, then validates and synthesizes.

Not a fixed pipeline: the Performance -> Funnel -> Technical transfer in the
reference mission is produced by ``DomainAgent.evaluate_handoff`` reading
anomalies off the Blackboard, not by a hardcoded sequence.

Providers are injected. The default ``build_fixture_bundle()`` is deterministic,
offline and SYNTHETIC; pass your own ``ProviderBundle`` to go live.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import uuid4

import structlog

from seleric_swarm.coordinator.intake import apply_full_flags, resolve_mission_time_range
from seleric_swarm.leadership.manager import LeadershipManager
from seleric_swarm.observability.tracing import traced_span
from seleric_swarm.runtime import SwarmRuntime
from seleric_swarm.swarm.blackboard import Blackboard
from seleric_swarm.swarm.domain.base import DomainAgent
from seleric_swarm.swarm.domain.configs import build_domain_configs
from seleric_swarm.swarm.envelope import Intent, SwarmMessage
from seleric_swarm.swarm.mission import SwarmMission, SwarmMissionResult, TeamMember
from seleric_swarm.swarm.providers.base import ProviderBundle
from seleric_swarm.swarm.providers.fixtures import (
    DEFAULT_SCENARIO,
    build_fixture_bundle,
    load_scenario,
)
from seleric_swarm.swarm.specialists.anomaly import AnomalyAgent
from seleric_swarm.swarm.specialists.diagnostic import DiagnosticAgent
from seleric_swarm.swarm.specialists.observer import ObserverAgent
from seleric_swarm.swarm.specialists.prediction import PredictionAgent
from seleric_swarm.swarm.specialists.skeptic import SkepticAgent
from seleric_swarm.swarm.specialists.strategy import StrategyAgent
from seleric_swarm.swarm.synthesis import build_response  # noqa: F401 — kept for callers/tests
from seleric_swarm.swarm.transport import InProcessTransport

_log = structlog.get_logger("seleric_swarm.swarm.orchestrator")

_DIAGNOSTIC_RE = re.compile(
    r"\b(why|root cause|reason for|caused?|explain|driver of|driving|drove|diagnose|"
    r"what changed|change in|drop in|fall in|decline in|increase in|rise in|spike in|"
    r"what.s behind|what is behind|attributed to)\b",
    re.IGNORECASE,
)
_PREDICTIVE_RE = re.compile(
    r"\b(forecast|predict|what happens|if this continues|next week|projection)\b",
    re.IGNORECASE,
)
_PRESCRIPTIVE_RE = re.compile(
    r"\b(what should|recommend|what do we do|how do we fix|action)\b",
    re.IGNORECASE,
)
_HEALTH_RE = re.compile(r"\b(how are we doing|how is (the )?business|health check)\b", re.IGNORECASE)


def classify_intents(query: str) -> set[str]:
    intents: set[str] = set()
    if _HEALTH_RE.search(query):
        intents.add("executive_health")
        intents.add("diagnostic")
    if _DIAGNOSTIC_RE.search(query):
        intents.add("diagnostic")
    if _PREDICTIVE_RE.search(query):
        intents.add("predictive")
    if _PRESCRIPTIVE_RE.search(query):
        intents.add("prescriptive")
    if not intents:
        intents.add("diagnostic")
    return intents


def _initial_lead(query: str) -> str:
    q = query.lower()
    if any(k in q for k in ("cac", "roas", "cpm", "cpc", "ctr", "ad spend", "acquisition")):
        return "performance_agent"
    if any(k in q for k in ("attribut", "last-touch", "last touch", "channel mix")):
        return "attribution_agent"
    if any(k in q for k in ("net sales", "gross sales", "orders", "returns", "revenue")):
        return "commerce_agent"
    if any(k in q for k in ("units sold", "sku", "product margin", "assortment")):
        return "product_agent"
    if any(k in q for k in ("repeat rate", "ltv", "cohort", "retention")):
        return "customer_agent"
    if any(k in q for k in ("refund", "fulfillment", "ops sla")):
        return "operations_agent"
    if any(k in q for k in ("profit", "margin", "cogs")):
        return "finance_agent"
    if any(k in q for k in ("sessions", "checkout", "add to cart", "conversion", "funnel")):
        return "funnel_agent"
    return "performance_agent"


async def run_swarm_mission(
    runtime: SwarmRuntime,
    *,
    query: str,
    timezone: str = "Asia/Kolkata",
    as_of: str | None = None,
    providers: ProviderBundle | None = None,
    scenario_id: str = DEFAULT_SCENARIO,
    session_id: str | None = None,
    request_id: str | None = None,
    full_skeptic: bool = False,
    full_diagnostic: bool = False,
    full_prediction: bool = False,
    mission_id: str | None = None,
) -> SwarmMissionResult:
    mid = mission_id or f"MS-{uuid4().hex[:10]}"
    mission_id = mid
    rid = request_id or uuid4().hex
    sid = session_id or uuid4().hex
    tracing = runtime.settings.langsmith_tracing

    scenario = load_scenario(scenario_id)
    providers = providers or build_fixture_bundle(scenario_id)
    intents = apply_full_flags(
        classify_intents(query),
        full_diagnostic=full_diagnostic,
        full_prediction=full_prediction,
        full_skeptic=full_skeptic,
    )
    initial_lead = _initial_lead(query)
    time_range = resolve_mission_time_range(scenario, timezone=timezone, as_of=as_of)

    mission = SwarmMission(
        mission_id=mid,
        query=query,
        time_range=time_range,
        intents=intents,
        complexity="L5" if len(intents) >= 2 else "L4",
        initial_lead=initial_lead,
        context={
            "primary_metric": "metric.cac" if initial_lead == "performance_agent" else "metric.net_sales",
            "degradation_started_at": (scenario.get("domains", {}).get("technical", {}) or {}).get("degradation_started_at"),
        },
    )

    # -- assemble domain agents (all DOMAIN_WIRING peers) + specialists --------
    ontology = getattr(runtime, "ontology", None)
    domains: dict[str, DomainAgent] = {}
    for cfg in build_domain_configs(runtime.metrics, runtime.agents).values():
        agent = DomainAgent(cfg, providers.data_for(cfg.domain), peers=providers.data, metrics=runtime.metrics)
        if ontology is not None and cfg.seleric_module:
            agent.attach_ontology(await ontology.for_module(cfg.seleric_module))
        domains[cfg.agent_id] = agent
    observer = ObserverAgent(providers, domains)
    anomaly = AnomalyAgent(providers)
    if full_diagnostic:
        # Delegate to the full explicit-hypothesis subsystem (agents/diagnostic/)
        # while keeping the in-loop specialist interface + the Hypothesis/Causal
        # Blackboard artifacts the synthesizer / completion gate expect.
        from seleric_swarm.agents.diagnostic.swarm_bridge import SwarmDiagnosticSpecialist

        diagnostic: Any = SwarmDiagnosticSpecialist(
            providers, scenario=scenario, ontology=getattr(runtime, "ontology", None)
        )
    else:
        diagnostic = DiagnosticAgent(providers)
    if full_prediction:
        # Delegate to the full forecast-orchestration subsystem (agents/prediction/)
        # while keeping the in-loop specialist interface + the Prediction artifact.
        from seleric_swarm.agents.prediction.swarm_bridge import SwarmPredictionSpecialist

        prediction: Any = SwarmPredictionSpecialist(providers, scenario=scenario)
    else:
        prediction = PredictionAgent(providers)
    strategy = StrategyAgent(providers)
    if full_skeptic:
        # Delegate to the full verification subsystem (agents/skeptic/) while
        # keeping the in-loop specialist interface + the Skeptic Blackboard
        # artifact the synthesizer / completion gate expect.
        from seleric_swarm.agents.skeptic.swarm_bridge import SwarmSkepticSpecialist

        skeptic: Any = SwarmSkepticSpecialist(
            providers, ontology=getattr(runtime, "ontology", None)
        )
    else:
        skeptic = SkepticAgent(providers)

    blackboard = Blackboard(mission_id)
    blackboard.mission_lead = initial_lead
    leadership = LeadershipManager()

    # -- transport: every activation goes through a seleric.swarm.v1 message ---
    transport = InProcessTransport()
    for spec in (observer, anomaly, diagnostic, prediction, strategy, skeptic):
        async def _handler(msg: SwarmMessage, _spec=spec) -> dict[str, Any]:
            ids = await _spec.run(blackboard, mission)
            return {"ok": True, "artifact_refs": ids, "produced": _spec.produces}
        transport.register(spec.agent_id, _handler)

    async def activate(agent_id: str, objective: str, intent: Intent = Intent.TASK_REQUEST) -> dict[str, Any]:
        blackboard.active_specialist = agent_id
        msg = SwarmMessage.request(
            mission_id=mission_id,
            from_agent="swarm_coordinator",
            to_agent=agent_id,
            intent=intent,
            objective=objective,
            scope=time_range,
            mission_context=blackboard.leadership_state(),
        )
        return await transport.send(msg)

    team: list[TeamMember] = [TeamMember(initial_lead, "domain", "lead"), TeamMember("observer_agent", "specialist", "support")]
    if "diagnostic" in intents:
        team += [TeamMember("anomaly_agent", "specialist", "support"), TeamMember("diagnostic_agent", "specialist", "support")]
    if "predictive" in intents:
        team.append(TeamMember("prediction_agent", "specialist", "support"))
    if "prescriptive" in intents:
        team.append(TeamMember("strategy_agent", "specialist", "support"))
    if "diagnostic" in intents:
        team.append(TeamMember("skeptic_agent", "specialist", "support"))

    limitations: list[str] = [
        (
            "PROTOTYPE: all evidence is SYNTHETIC (fixture/template providers). "
            "Wire real MCP data + models before acting."
        )
    ]

    with traced_span(
        "mission.swarm_v1",
        {
            "request_id": rid, "session_id": sid, "mission_id": mission_id,
            "workflow_name": "swarm_v1", "workflow_version": "0.1.0",
            "agent_name": "swarm_coordinator", "agent_version": "0.1.0",
        },
        tracing,
        inputs={"query": query, "intents": sorted(intents), "initial_lead": initial_lead},
    ) as span:
        # -- investigation loop: observe -> detect -> maybe transfer leadership
        max_transfers = int(getattr(runtime.settings, "max_leadership_transfers", 6))
        for _ in range(max_transfers + 1):
            lead = blackboard.mission_lead or initial_lead
            await activate("observer_agent", f"Observe {lead} metrics for the mission window")
            await activate("anomaly_agent", "Detect anomalies across current evidence", Intent.MODEL_REQUEST)

            proposal = domains[lead].evaluate_handoff(blackboard)
            if proposal is None:
                break
            decision = leadership.decide(
                blackboard.leadership_state(), proposal.to_leadership_proposal(mission_id)
            )
            if not decision.get("accepted"):
                blackboard.record_event("handoff_rejected", reason=decision.get("error_message"))
                limitations.append(f"Leadership transfer rejected: {decision.get('error_message')}")
                break
            blackboard.handoff_history = list(decision["handoff_history"])
            blackboard.leadership_epoch = int(decision["leadership_epoch"])
            blackboard.mission_lead = decision["mission_lead"]
            blackboard.record_event(
                "leadership_transfer",
                **decision["handoff_history"][-1],
            )

        # -- intelligence pipeline (adaptive, not linear) --------------------
        if "diagnostic" in intents:
            await activate("diagnostic_agent", "Generate + causally validate hypotheses", Intent.MODEL_REQUEST)
        if "predictive" in intents:
            await activate("prediction_agent", "Forecast impact if the trend continues", Intent.MODEL_REQUEST)
        if "prescriptive" in intents:
            await activate("strategy_agent", "Design mechanism-fit interventions", Intent.MODEL_REQUEST)
        managed_claims: list[dict[str, Any]] = []
        remediation_round = 0
        if "diagnostic" in intents:
            result = await activate("skeptic_agent", "Attack the candidate conclusion", Intent.CHALLENGE)
            # read the LATEST skeptic verdict - a re-check appends a second artifact
            skeptic_art = (blackboard.by_type("skeptic") or [{}])[-1]
            verdict = skeptic_art.get("verdict")
            followups = list(skeptic_art.get("required_followups") or [])
            retained_hyps = [h for h in blackboard.by_type("hypothesis") if h.get("status") == "retained"]
            claim_statement = retained_hyps[0]["statement"] if retained_hyps else "candidate conclusion"
            from seleric_swarm.coordinator.artifacts.claims import ClaimManager
            from seleric_swarm.coordinator.governance.remediation import (
                execute_targeted_remediation,
                targeted_remediation_plan,
            )
            from seleric_swarm.coordinator.governance.skeptic_gate import apply_skeptic_gate

            claim_mgr = ClaimManager()
            claim = claim_mgr.propose(
                mission_id=mission_id,
                statement=claim_statement,
                claim_type="causal",
                support_refs=blackboard.refs_by_type("causal"),
                origin_agent="diagnostic_agent",
                synthetic=True,
            )
            if verdict == "PASS":
                claim_mgr.transition(claim.claim_id, "SUPPORTED")
            gate = apply_skeptic_gate(
                claim_manager=claim_mgr,
                claim_id=claim.claim_id,
                verdict=str(verdict or "PASS"),
                followups=followups,
                mission_id=mission_id,
                remediation_round=remediation_round,
                max_remediation_rounds=int(getattr(runtime.settings, "max_remediation_rounds", 3)),
            )
            managed_claims = claim_mgr.dump()
            blackboard.record_event(gate.get("event") or "skeptic_gate", verdict=verdict)

            if verdict == "REVISE":
                # Targeted remediation — never blindly re-run entire Diagnostic.
                plan = gate.get("remediation") or targeted_remediation_plan(
                    mission_id=mission_id, followups=followups
                )
                blackboard.record_event(
                    "skeptic_revise_remediation",
                    avoid_full_diagnostic=plan.get("avoid_full_diagnostic"),
                    kinds=plan.get("kinds"),
                )

                async def _activate(agent_id: str, objective: str, intent: str = "task_request", extra: dict | None = None):
                    intent_enum = {
                        "task_request": Intent.TASK_REQUEST,
                        "model_request": Intent.MODEL_REQUEST,
                        "challenge": Intent.CHALLENGE,
                    }.get(intent, Intent.TASK_REQUEST)
                    # causal_validation_only is recorded for audit; specialist still runs once
                    if extra:
                        blackboard.record_event("remediation_activate", agent=agent_id, **extra)
                    return await activate(agent_id, objective, intent_enum)

                await execute_targeted_remediation(plan=plan, activate=_activate)
                remediation_round += 1
                await activate("skeptic_agent", "Re-check after targeted remediation", Intent.CHALLENGE)
                skeptic_art = (blackboard.by_type("skeptic") or [{}])[-1]
                verdict = skeptic_art.get("verdict")
                gate = apply_skeptic_gate(
                    claim_manager=claim_mgr,
                    claim_id=claim.claim_id,
                    verdict=str(verdict or "PASS"),
                    followups=list(skeptic_art.get("required_followups") or []),
                    mission_id=mission_id,
                    remediation_round=remediation_round,
                    max_remediation_rounds=int(getattr(runtime.settings, "max_remediation_rounds", 3)),
                )
                managed_claims = claim_mgr.dump()
            elif verdict == "REJECT":
                blackboard.record_event("skeptic_reject", claim_id=claim.claim_id)
            _ = result

        from seleric_swarm.coordinator.synthesis.response_builder import build_claim_aware_response

        final_response = build_claim_aware_response(
            blackboard, mission, managed_claims=managed_claims
        )

        retained = [h for h in blackboard.by_type("hypothesis") if h.get("status") == "retained"]
        skeptic_ok = (blackboard.by_type("skeptic") or [{}])[-1].get("verdict") == "PASS"
        challenged = any(c.get("state") == "CHALLENGED" for c in managed_claims)
        prov = blackboard.synthetic_summary()
        # Successful synthetic fixtures stay "completed" for API backward-compat;
        # response banner + synthetic flag carry prototype semantics. swarm_v2 may
        # emit prototype_completed via the completion gate.
        if "diagnostic" in intents and retained and skeptic_ok and not challenged:
            status = "completed"
        elif challenged:
            status = "partial"
            limitations.append("Primary claim remains CHALLENGED after Skeptic REVISE.")
        elif retained or blackboard.by_type("evidence"):
            status = "partial"
        else:
            status = "failed"

        artifacts = {
            t: blackboard.refs_by_type(t)
            for t in ("evidence", "anomaly", "hypothesis", "causal", "prediction", "strategy", "skeptic")
        }
        prov = blackboard.synthetic_summary()
        if not prov["all_synthetic"] and limitations and limitations[0].startswith("PROTOTYPE"):
            limitations = limitations[1:]
        if prov["mixed"]:
            limitations.insert(
                0,
                f"MIXED PROVENANCE: {prov['synthetic']}/{prov['total']} artifacts are synthetic; "
                "claims resting on them are unverified.",
            )
        span.set_outputs(
            {
                "status": status,
                "mission_lead": blackboard.mission_lead,
                "leadership_epoch": blackboard.leadership_epoch,
                "handoffs": blackboard.handoff_history,
                "artifact_counts": {k: len(v) for k, v in artifacts.items()},
                "provenance": prov,
                "a2a_messages": transport.log,
            }
        )

    mission_result = SwarmMissionResult(
        mission_id=mission_id,
        status=status,
        query=query,
        complexity=mission.complexity,
        initial_mission_lead=initial_lead,
        mission_lead=blackboard.mission_lead or initial_lead,
        leadership_epoch=blackboard.leadership_epoch,
        team=[t.__dict__ for t in team],
        handoff_history=blackboard.handoff_history,
        artifacts=artifacts,
        final_response=final_response,
        limitations=limitations,
        synthetic=bool(prov["synthetic"]),
        events=blackboard.events,
    )
    # Persist so GET /v1/missions/{id} can return a swarm mission. The store's
    # typed view is the lookup MissionResult; the full swarm dict rides in raw.
    try:
        runtime.store.put(
            _swarm_mission_view(mission_result, rid, sid),
            {"route": "swarm", **mission_result.as_dict()},
        )
    except Exception as exc:  # persistence must never fail a completed mission
        _log.warning("swarm.persist_failed", mission_id=mission_id, error=str(exc))
    return mission_result


def _swarm_mission_view(result: SwarmMissionResult, request_id: str, session_id: str) -> Any:
    from typing import cast

    from seleric_swarm.api.status import coerce_typed_status
    from seleric_swarm.contracts.lookup import (
        HandoffView,
        MissionError,
        MissionResult,
        MissionStatus,
        TraceInfo,
    )

    status = coerce_typed_status(result.status, default="partial")
    return MissionResult(
        mission_id=result.mission_id,
        status=cast(MissionStatus, status),
        mission_lead=result.mission_lead,
        initial_mission_lead=result.initial_mission_lead,
        leadership_epoch=result.leadership_epoch,
        handoff_history=[HandoffView.model_validate(h) for h in result.handoff_history],
        limitations=list(result.limitations),
        final_response=result.final_response,
        error=MissionError(code=result.error_code, message=result.error_code) if result.error_code else None,
        trace=TraceInfo(request_id=request_id, session_id=session_id),
    )
