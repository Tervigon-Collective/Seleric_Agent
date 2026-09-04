"""Regression: the full-subsystem swarm bridges must be idempotent.

When the Skeptic REVISEs, the orchestrator re-runs Diagnostic + Skeptic. Before
the fix that appended fresh artifacts every call, this left stale duplicates and
the completion gate / synthesizer read the *first* (stale) verdict.
"""

from __future__ import annotations

import pytest

from seleric_swarm.swarm.artifacts import Anomaly, Causal, Evidence
from seleric_swarm.swarm.blackboard import Blackboard
from seleric_swarm.swarm.mission import SwarmMission


def _blackboard_with_cac_scenario() -> Blackboard:
    bb = Blackboard("MS-idem")
    bb.mission_lead = "technical_agent"
    for mid, val, base, dims in [
        ("metric.purchase_cvr", 2.03, 2.95, {"device": "mobile"}),
        ("metric.purchase_cvr", 2.35, 3.10, {}),
        ("metric.mobile_lcp_seconds", 5.8, 2.2, {}),
        ("metric.js_error_rate", 6.1, 0.7, {}),
    ]:
        ev = Evidence.new(mission_id="MS-idem", created_by="observer@technical_agent",
                          metric_or_fact=mid, value=val, baseline=base, change_pct=round((val - base) / base * 100, 1),
                          dimensions=dims, time_range={"start": "2026-09-01"})
        ev.mark_synthetic()
        bb.post(ev)
    dep = Evidence.new(mission_id="MS-idem", created_by="observer@technical_agent",
                       metric_or_fact="event.frontend_deployment", value="2026-09-01T11:40:00+05:30",
                       time_range={"start": "2026-09-01"})
    dep.mark_synthetic()
    bb.post(dep)
    an = Anomaly.new(mission_id="MS-idem", created_by="anomaly_agent", metric_id="metric.purchase_cvr",
                     deviation_pct=-24.2, direction="down", start_time="2026-09-01T12:05:00+05:30")
    an.mark_synthetic()
    bb.post(an)
    return bb


def test_blackboard_discard_and_discard_by():
    bb = Blackboard("MS-d")
    a = Causal.new(mission_id="MS-d", created_by="diagnostic_agent", treatment="t", outcome="o")
    b = Causal.new(mission_id="MS-d", created_by="diagnostic_agent", treatment="t2", outcome="o2")
    c = Causal.new(mission_id="MS-d", created_by="other_agent", treatment="x", outcome="y")
    for art in (a, b, c):
        bb.post(art)
    assert len(bb.by_type("causal")) == 3
    n = bb.discard_by(created_by="diagnostic_agent", artifact_types=("causal",))
    assert n == 2
    remaining = bb.by_type("causal")
    assert len(remaining) == 1 and remaining[0]["created_by"] == "other_agent"
    bb.discard(remaining[0]["artifact_id"])
    assert bb.by_type("causal") == []


@pytest.mark.asyncio
async def test_diagnostic_bridge_is_idempotent():
    from seleric_swarm.agents.diagnostic.swarm_bridge import SwarmDiagnosticSpecialist
    from seleric_swarm.swarm.providers.fixtures import load_scenario

    bb = _blackboard_with_cac_scenario()
    mission = SwarmMission(mission_id="MS-idem", query="Why did purchase CVR drop?", time_range={},
                           initial_lead="performance_agent", intents={"diagnostic"},
                           context={"degradation_started_at": "2026-09-01T12:00:00+05:30"})
    spec = SwarmDiagnosticSpecialist(scenario=load_scenario("cac_regression"))

    await spec.run(bb, mission)
    h1, c1 = len(bb.by_type("hypothesis")), len(bb.by_type("causal"))
    assert h1 > 0 and c1 == 1

    # re-run (as the orchestrator does on a Skeptic REVISE) - counts must not grow
    await spec.run(bb, mission)
    assert len(bb.by_type("hypothesis")) == h1
    assert len(bb.by_type("causal")) == 1


@pytest.mark.asyncio
async def test_skeptic_bridge_keeps_one_latest_verdict():
    from seleric_swarm.agents.diagnostic.swarm_bridge import SwarmDiagnosticSpecialist
    from seleric_swarm.agents.skeptic.swarm_bridge import SwarmSkepticSpecialist
    from seleric_swarm.swarm.providers.fixtures import load_scenario

    bb = _blackboard_with_cac_scenario()
    mission = SwarmMission(mission_id="MS-idem", query="Why did purchase CVR drop, what should we do?",
                           time_range={}, initial_lead="performance_agent", intents={"diagnostic"},
                           context={"degradation_started_at": "2026-09-01T12:00:00+05:30"})
    await SwarmDiagnosticSpecialist(scenario=load_scenario("cac_regression")).run(bb, mission)

    sk = SwarmSkepticSpecialist()
    await sk.run(bb, mission)
    await sk.run(bb, mission)
    verdicts = bb.by_type("skeptic")
    assert len(verdicts) == 1  # exactly one, the latest
    assert verdicts[-1]["verdict"] in {"PASS", "REVISE", "REJECT"}
