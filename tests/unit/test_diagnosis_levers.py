"""The lever ladder, the change log, the window pin and the failed-diagnosis gate (live 2026-10-10 thread_d1844eb0).

Invented metric, dimension and view names throughout: the tool must plan from the catalogue's metadata, never
from a name.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import warnings
from datetime import UTC, date, datetime, timedelta
from typing import Any

import numpy as np
import pytest

from seleric_swarm.agent.artifacts import EvidenceArtifact
from seleric_swarm.agent.dependencies import DIAGNOSIS_FAILED, ExecutionLimits, SelericDeps
from seleric_swarm.agent.output import MissionResult
from seleric_swarm.agent.validation import EvidenceValidator
from seleric_swarm.contracts.lookup import TimeRangeV1
from seleric_swarm.conversations.contracts import Artifact, ArtifactProvenance, ContextBundle, Principal
from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta, CatalogueSnapshot
from seleric_swarm.state.artifacts import InMemoryArtifactStore
from seleric_swarm.toolsets import diagnosis

pytestmark = pytest.mark.filterwarnings("ignore")
warnings.filterwarnings("ignore")
logging.getLogger("dowhy").setLevel(logging.ERROR)

AS_OF = datetime(2026, 10, 10, 12, tzinfo=UTC)
EVENT = date(2026, 10, 9)
DAYS = [EVENT - timedelta(days=i) for i in range(40, -1, -1)]


def _d(depends: list[str], aggregation: str, view: str, grain: str, unit: str = "count") -> dict[str, Any]:
    return {"aggregation": aggregation, "grain": grain, "unit": unit, "cube_mapping": {"view": view}, "formula": {"depends_on": depends}}


DEFS: dict[str, dict[str, Any]] = {
    # yield = value / cost ; cost-side counts live in "media", the value and its buyers in "shop"
    "zz_cost": _d([], "additive", "media", "ad_day", "INR"),
    "zz_views": _d([], "additive", "media", "ad_day"),
    "zz_taps": _d([], "additive", "media", "ad_day"),
    "zz_value": _d([], "additive", "shop", "buyer_day", "INR"),
    "zz_buyers": _d([], "additive", "shop", "buyer_day"),
    "zz_yield": _d(["zz_value", "zz_cost"], "ratio", "shop", "buyer_day", "ratio"),
    "zz_cost_per_view": _d(["zz_cost", "zz_views"], "ratio", "media", "ad_day", "INR"),
    "zz_tap_rate": _d(["zz_taps", "zz_views"], "ratio", "media", "ad_day", "ratio"),
    "zz_cost_per_tap": _d(["zz_cost", "zz_taps"], "ratio", "media", "ad_day", "INR"),
    "zz_cost_per_buyer": _d(["zz_cost", "zz_buyers"], "ratio", "shop", "buyer_day", "INR"),
    # the change log: counts of edits, found by grain
    "zz_edits": _d([], "additive", "audit", "edit_change_event"),
}
DIMS = {v: ["brand_id", "zz_source"] for v in ("media", "shop", "audit")}


def _data() -> dict[str, dict[date, float]]:
    rng = np.random.default_rng(7)
    n = len(DAYS)
    cost = 1000 + 40 * rng.normal(size=n)
    views = 40000 + 800 * rng.normal(size=n)
    taps = 0.02 * views * (1 + 0.02 * rng.normal(size=n))
    buyers = 0.02 * taps * (1 + 0.05 * rng.normal(size=n))
    basket = 2000 + 40 * rng.normal(size=n)
    i = DAYS.index(EVENT)
    cost[i] *= 2.0  # spend doubles on the event day ...
    views[i] *= 2.6
    taps[i] *= 3.0
    buyers[i] *= 0.9  # ... and buyers barely move
    basket[i] *= 1.3  # ... while each is worth more
    value = buyers * basket
    edits = np.zeros(n)
    edits[i] = 4.0
    S = lambda a: {d: float(v) for d, v in zip(DAYS, a, strict=True)}  # noqa: E731
    return {
        "zz_cost": S(cost), "zz_views": S(views), "zz_taps": S(taps), "zz_buyers": S(buyers), "zz_value": S(value),
        "zz_yield": S(value / cost), "zz_cost_per_view": S(cost / views), "zz_tap_rate": S(taps / views),
        "zz_cost_per_tap": S(cost / taps), "zz_cost_per_buyer": S(cost / buyers), "zz_edits": S(edits),
    }


class FakeMcp:
    def __init__(self) -> None:
        self.data = _data()
        self.queries: list[dict[str, Any]] = []

    async def call(self, *, agent_id: str, capability: str, arguments: dict[str, Any]) -> Any:
        if capability == "seleric.catalogue_get_metrics":
            return {"metrics": {m: {**DEFS[m], "id": m} for m in arguments["metric_ids"] if m in DEFS}}
        assert capability == "seleric.metrics_query", capability
        self.queries.append(arguments)
        m = arguments["measures"][0]
        start = date.fromisoformat(arguments["time_range"]["start"])
        end = date.fromisoformat(arguments["time_range"]["end"])
        dims = arguments.get("dimensions") or []
        rows = []
        for d, v in self.data[m].items():
            if not (start <= d <= end):
                continue
            stamp = f"{d.isoformat()}T00:00:00.000"
            if not dims:
                rows.append({"x.day.day": stamp, m: v})
            else:
                for seg, w in (("a", 0.5), ("b", 0.5)):
                    rows.append({"x.day.day": stamp, f"x.{dims[0]}": seg, m: v if DEFS[m]["aggregation"] == "ratio" else v * w})
        return {"rows": rows, "provenance": {"query_id": "q"}}


def _deps(mcp: FakeMcp, **extra: Any) -> SelericDeps:
    metrics = tuple(
        CatalogueMetricMeta(id=m, view=d["cube_mapping"]["view"], supported_dimensions=list(DIMS[d["cube_mapping"]["view"]]), raw={"aggregation": d["aggregation"]})
        for m, d in DEFS.items()
    )
    return SelericDeps(
        mission_id="m-lev", as_of=AS_OF,
        principal=Principal(principal_id="p", workspace_id="ws", user_id="u"),
        thread_id="t", run_id="r", trace_id="tr", context=ContextBundle(), mcp_client=mcp,
        artifact_store=InMemoryArtifactStore(), limits=ExecutionLimits(),
        catalogue=CatalogueSnapshot(metrics=metrics, dimensions=("brand_id", "zz_source")),
        **extra,
    )


class Ctx:
    def __init__(self, deps: SelericDeps) -> None:
        self.deps = deps


def _at(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _fresh_definition_cache():
    diagnosis._definitions_cache.update({"at": 0.0, "ids": frozenset(), "defs": {}})


def _diagnose(deps: SelericDeps, **kw: Any):
    kw.setdefault("event_start", _at(EVENT))
    kw.setdefault("event_end", _at(EVENT))
    kw.setdefault("compare_start", _at(EVENT - timedelta(days=1)))
    kw.setdefault("compare_end", _at(EVENT - timedelta(days=1)))
    return asyncio.run(diagnosis.diagnose_metric_change(Ctx(deps), "zz_yield", **kw))  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------- the window pin
def test_event_day_inside_the_named_window_is_not_pinned_onto_its_own_comparison():
    """"last 3 days … why worse yesterday": the event is a day inside the window; pinning it to the window put it on
    top of the comparison and the diagnosis was refused (live 2026-10-10)."""
    window = TimeRangeV1(kind="relative", start=(EVENT - timedelta(days=2)).isoformat(), end=EVENT.isoformat(), relative_token="last_3_days")
    res = _diagnose(_deps(FakeMcp(), resolved_window=window))
    assert res.success, res.summary
    assert "must end before" not in res.summary


def test_a_drifted_window_is_still_pinned_when_it_leaves_a_valid_call():
    window = TimeRangeV1(kind="relative", start=(EVENT - timedelta(days=1)).isoformat(), end=EVENT.isoformat(), relative_token="last_2_days")
    # an event asked one day early (drift) with a comparison well before it: the pin applies and the call stays valid
    res = _diagnose(
        _deps(FakeMcp(), resolved_window=window),
        event_start=_at(EVENT - timedelta(days=1)), event_end=_at(EVENT - timedelta(days=1)),
        compare_start=_at(EVENT - timedelta(days=9)), compare_end=_at(EVENT - timedelta(days=8)),
    )
    assert res.success, res.summary
    assert any("corrected" in w for w in res.warnings), res.warnings


def test_an_event_on_its_comparison_is_refused_with_a_message_that_says_what_to_pass():
    res = _diagnose(_deps(FakeMcp()), compare_start=_at(EVENT), compare_end=_at(EVENT))
    assert not res.success
    assert "compare_start" in res.summary and str(EVENT) in res.summary


# --------------------------------------------------------------------------------------- the lever ladder
def _effects(summary: str) -> tuple[list[float], float]:
    ladder = next(line for line in summary.splitlines() if line.startswith("EXACT LEVER LADDER"))
    stages = [float(x) for x in re.findall(r"moving [A-Za-z ]+? ([+-]\d+\.\d)%", ladder)]
    total = float(re.search(r"together [A-Za-z ]+? ([+-]\d+\.\d)%", ladder).group(1))  # type: ignore[union-attr]
    return stages, total


def test_ratio_outcome_without_drivers_splits_into_what_a_buyer_cost_and_was_worth():
    res = _diagnose(_deps(FakeMcp()))
    assert res.success, res.summary
    stages, total = _effects(res.summary)
    assert len(stages) == 2, res.summary  # cost per buyer, value per buyer
    assert math.isclose(math.prod(1 + s / 100 for s in stages), 1 + total / 100, rel_tol=2e-2)


def test_named_rates_open_the_funnel_and_the_effects_multiply_to_the_outcome():
    mcp = FakeMcp()
    res = _diagnose(_deps(mcp), drivers=["zz_cost_per_view", "zz_tap_rate", "zz_cost_per_tap"])
    assert res.success, res.summary
    stages, total = _effects(res.summary)
    assert len(stages) == 4, res.summary  # views, taps, buyers, value: one stage between each pair
    assert math.isclose(math.prod(1 + s / 100 for s in stages), 1 + total / 100, rel_tol=3e-2)
    # exact against the series: the yield's own change over the same two days
    y = mcp.data["zz_yield"]
    truth = (y[EVENT] / y[EVENT - timedelta(days=1)] - 1) * 100
    assert abs(total - truth) < 0.2, (total, truth)
    # the first stage reads as a cost, not as a count per unit of spend
    assert "1 ÷ zz cost per 1,000 zz views" in res.summary


def test_a_scoped_outcome_lists_the_edits_logged_on_the_event_day():
    res = _diagnose(_deps(FakeMcp()), filters={"zz_source": ["north"]})
    assert res.success, res.summary
    assert "CHANGES LOGGED" in res.summary
    assert "0.0 → 4.0 per day" in res.summary


def test_an_unscoped_outcome_does_not_list_account_wide_edits():
    res = _diagnose(_deps(FakeMcp()))
    assert "CHANGES LOGGED" not in res.summary


def test_pooled_entities_are_flagged_so_each_can_be_checked():
    res = _diagnose(_deps(FakeMcp()), filters={"zz_source": ["north", "south"]})
    assert any("pooled into one series" in w for w in res.warnings), res.warnings


# --------------------------------------------------------------------------------------- the gate
def _seeded(deps: SelericDeps) -> None:
    for day in range(1, 11):
        stamp = datetime(2026, 9, day, tzinfo=UTC)
        payload = EvidenceArtifact(
            metric_id="zz_yield", grain="day", as_of=AS_OF, period_start=stamp, period_end=stamp, value=1.0 + day,
            source_query={"measure": "zz_yield"},
        )
        deps.artifact_store.put(Artifact(
            workspace_id="ws", artifact_type="evidence", payload=payload.model_dump(mode="json"), classification="factual",
            evidence_ids=[f"raw:{day}"], provenance=ArtifactProvenance(query_version="q1"), mission_id=deps.mission_id,
        ))


def _answer(status: str = "completed") -> MissionResult:
    text = (
        "Yield fell from 0.80 to 0.70 yesterday because buyers converted less after spend doubled across the campaign, "
        "and each buyer was worth more, which hid part of the drop in the overall figure."
    )
    return MissionResult.model_validate({
        "mission_id": "m-lev", "status": status, "query": "why", "as_of": AS_OF, "final_response": text,
        "evidence_ids": [], "finding_ids": [],
    })


def test_an_answer_whose_diagnosis_was_refused_is_sent_back():
    deps = _deps(FakeMcp())
    _seeded(deps)
    deps.call_counts[DIAGNOSIS_FAILED] = "the comparison period must end before 2026-10-07"
    outcome = EvidenceValidator().validate(_answer(), deps=deps)
    assert not outcome.ok
    assert "diagnose_metric_change was refused" in (outcome.reason or "")


def test_a_partial_answer_is_not_blocked_and_a_later_success_clears_the_gate():
    deps = _deps(FakeMcp())
    _seeded(deps)
    deps.call_counts[DIAGNOSIS_FAILED] = "refused"
    assert "diagnose_metric_change was refused" not in (EvidenceValidator().validate(_answer("partial"), deps=deps).reason or "")
    res = _diagnose(deps)  # a successful diagnosis through the tool clears the record
    assert res.success and DIAGNOSIS_FAILED not in deps.call_counts
    assert "diagnose_metric_change was refused" not in (EvidenceValidator().validate(_answer(), deps=deps).reason or "")


# --------------------------------------------------------------------------------------- ladder hygiene, scope echo
def _ladder(extra_series: dict[str, list[float]], extra_meta: dict[str, tuple[str, str]], named: list[str]):
    """_funnel_ladder on synthetic series: cost -> views -> taps -> buyers, value counted with the buyers."""
    from seleric_swarm.causal import diagnosis as engine

    days = [date(2026, 10, 1) + timedelta(days=i) for i in range(10)]
    ev, rf = [days[-1]], [days[-2]]
    base = {
        "zz_cost": [100.0] * 8 + [100.0, 200.0], "zz_views": [5000.0] * 8 + [5000.0, 9000.0],
        "zz_taps": [500.0] * 8 + [500.0, 700.0], "zz_buyers": [20.0] * 8 + [20.0, 15.0],
        "zz_value": [2000.0] * 8 + [2000.0, 2400.0], **extra_series,
    }
    base["zz_ratio"] = [v / c for v, c in zip(base["zz_value"], base["zz_cost"], strict=True)]
    series = {m: dict(zip(days, vals, strict=True)) for m, vals in base.items()}
    lineage = {
        "zz_cost": engine.MetricMeta("zz_cost", "additive", "media", (), "Spend", "ad_day", "INR"),
        "zz_value": engine.MetricMeta("zz_value", "additive", "shop", (), "Value", "buyer_day", "INR"),
        "zz_views": engine.MetricMeta("zz_views", "additive", "media", (), "Views", "ad_day", "count"),
        "zz_taps": engine.MetricMeta("zz_taps", "additive", "media", (), "Taps", "ad_day", "count"),
        "zz_buyers": engine.MetricMeta("zz_buyers", "additive", "shop", (), "Buyers", "buyer_day", "count"),
        "zz_ratio": engine.MetricMeta("zz_ratio", "ratio", "shop", ("zz_value", "zz_cost"), "Yield", "buyer_day", "ratio"),
        **{m: engine.MetricMeta(m, "additive", view, (), m, "x", "count") for m, (view, _) in extra_meta.items()},
    }
    return diagnosis._funnel_ladder("zz_ratio", ["zz_buyers"], named, lineage, series, days[:-2], ev, rf)


def test_a_stage_that_widens_in_either_window_is_not_a_step():
    # "mid" is smaller than the taps on the reference day (600 vs 500 -> ok) but the order flips on the event day
    # (650 vs 700): a count of other traffic, not a narrowing of the same one. One of the pair must go.
    text, figures, stages = _ladder({"zz_mid": [600.0] * 8 + [600.0, 650.0]}, {"zz_mid": ("media", "")}, ["zz_views", "zz_mid", "zz_taps"])
    assert not ({"zz_mid", "zz_taps"} <= set(stages)), stages
    assert text.startswith("EXACT LEVER LADDER")
    stage_effects = [v for k, v in figures.items() if k.endswith("effect on zz_ratio %")]
    assert math.isclose(math.prod(1 + e / 100 for e in stage_effects), 1 + figures["zz_ratio | lever ladder change %"] / 100, rel_tol=1e-9)


def test_a_second_count_of_the_closing_event_is_dropped():
    text, _, stages = _ladder({"zz_buyers_ledger": [20.0] * 8 + [20.0, 15.0]}, {"zz_buyers_ledger": ("sales", "")}, ["zz_views", "zz_buyers_ledger"])
    assert "zz_buyers_ledger" not in stages and stages[-2:] == ["zz_buyers", "zz_value"], stages


def test_every_filtered_diagnosis_says_whose_it_is():
    res = _diagnose(_deps(FakeMcp()), filters={"zz_source": "north"})
    assert res.summary.splitlines()[0].startswith("SCOPE OF THIS DIAGNOSIS")
    assert "zz source = north" in res.summary.splitlines()[0]
    assert not _diagnose(_deps(FakeMcp())).summary.startswith("SCOPE OF THIS DIAGNOSIS")
