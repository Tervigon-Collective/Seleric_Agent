"""Characterization suite for docs/46_ARCHITECTURE_CONSOLIDATION_PLAN.md, Item 2.

Item 2 found three independent MCP-query-shaping implementations that all
converge on the same two primitives (``resolve_measure`` /
``call_metrics_query``): ``McpDataProvider.fetch()``,
``McpDataProvider.fetch_series()``, and
``business_state/series.py::fetch_series()``. Before any of them are merged,
this suite is the safety net Item 2's plan calls for: it captures what each
path returns TODAY for the same live metric + time range, so a later refactor
can diff against these recorded values instead of trusting "it looks
equivalent."

This does not assert the two paths already agree -- they may not (that's
exactly what Item 2 exists to find out). It records observed behavior and
flags disagreement for a human to classify as "expected difference" (e.g.
BusinessStateService's ``actual`` is the *last day's point*, not a period
total -- see ``lookup_fast_path.py``'s own comment on this) or "real bug."

Requires live Seleric MCP credentials (skips otherwise, same as every other
``runtime``-fixture test in this repo).
"""

from __future__ import annotations

import pytest

from seleric_swarm.contracts.lookup import TimeRangeV1
from seleric_swarm.domain.models import StateRequest
from seleric_swarm.swarm.providers.mcp_data import McpDataProvider, McpFetchStats

# One representative metric per domain already exercised by
# tests/replay/test_domain_lookups.py, reused here for the same reason: known
# to resolve live rather than picking untested metric ids.
_CASES = [
    ("performance_agent", "metric.cac"),
    ("finance_agent", "metric.net_profit"),
    ("product_agent", "metric.units_sold"),
]

_AS_OF = "2026-09-03"
_DAY = "2026-08-01"


def _domain_for(agent_id: str) -> str:
    return agent_id.removesuffix("_agent")


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_id,metric_id", _CASES)
async def test_characterize_single_day_fetch_vs_business_state(runtime, agent_id, metric_id):
    """Single-day window: the case both paths are expected to agree on
    (BusinessStateService's ``actual`` = last day's point = the only point
    in a one-day range, so the "last point vs period total" distinction
    lookup_fast_path.py's docstring warns about shouldn't apply here).
    """
    provider = McpDataProvider(
        _domain_for(agent_id),
        mcp=runtime.mcp,
        stats=McpFetchStats(),
        metrics=runtime.metrics,
        agent_id=agent_id,
    )
    fetch_result = await provider.fetch(
        metric_ids=[metric_id],
        time_range={"start": _DAY, "end": _DAY},
    )

    state = await runtime.business_state.get_metric_state(
        StateRequest(
            metric_id=metric_id,
            time_range=TimeRangeV1(kind="absolute", start=_DAY, end=_DAY),
            agent_id=agent_id,
            as_of=_AS_OF,
            need=["actual"],
        )
    )

    fetch_value = fetch_result.readings[0].value if fetch_result.readings else None
    bss_value = state.actual

    # Recorded, not asserted-equal: this is the characterization record this
    # suite exists to produce. A None on either side, or a numeric mismatch,
    # is exactly the kind of finding Item 2 step 2 (diff the two
    # implementations line by line) needs -- surfaced here as a clear
    # failure message rather than silently passing either way.
    assert fetch_value is not None, (
        f"McpDataProvider.fetch() returned no reading for {metric_id} on {_DAY} "
        f"(missing={fetch_result.missing}) -- cannot characterize, MCP data unavailable"
    )
    assert bss_value is not None, (
        f"BusinessStateService.get_metric_state() returned no actual for {metric_id} on {_DAY} "
        f"(status={state.status}, error_code={state.error_code}) -- cannot characterize"
    )
    assert fetch_value == pytest.approx(bss_value, rel=1e-6), (
        f"DIVERGENCE for {metric_id} on {_DAY}: "
        f"McpDataProvider.fetch()={fetch_value} vs "
        f"BusinessStateService.get_metric_state().actual={bss_value}. "
        "Both call resolve_measure()+call_metrics_query() independently for "
        "the same single-day window -- a divergence here means the two "
        "implementations' query-shaping logic (not just their multi-day "
        "period-total-vs-last-point framing) genuinely differs. Classify "
        "this as a real bug before proceeding with docs/46 Item 2's merge."
    )


# 7-day window ending on the single-day case's date, so the "last day"
# comparison below reuses the value already proven consistent above.
_PERIOD_START = "2026-07-26"
_PERIOD_END = _DAY


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_id,metric_id", _CASES)
async def test_characterize_multi_day_window_last_point_vs_period_total(runtime, agent_id, metric_id):
    """Multi-day window: the documented divergence case.

    ``lookup_fast_path.py`` itself documents that ``BusinessStateService
    .get_metric_state()``'s ``actual`` is the metric's LAST DAY's point for a
    multi-day range, not a period aggregate -- "correct for 'today's value'
    but silently wrong for a range ask" is that file's own phrasing for why
    it stopped using BusinessStateService directly for multi-day lookups.

    This test proves that documented behavior explicitly rather than taking
    it on faith: BSS's ``actual`` over the full period must equal
    ``McpDataProvider.fetch()`` for the period's LAST DAY ALONE, not
    the period total. If BSS ever starts returning a period aggregate
    instead (a behavior change, intentional or not), this test fails and
    flags it -- which matters because Item 2's eventual merge must decide
    which behavior the unified fetcher keeps, not silently inherit whichever
    implementation happens to win the merge.
    """
    provider = McpDataProvider(
        _domain_for(agent_id),
        mcp=runtime.mcp,
        stats=McpFetchStats(),
        metrics=runtime.metrics,
        agent_id=agent_id,
    )
    last_day_fetch = await provider.fetch(
        metric_ids=[metric_id],
        time_range={"start": _PERIOD_END, "end": _PERIOD_END},
    )
    period_fetch = await provider.fetch(
        metric_ids=[metric_id],
        time_range={"start": _PERIOD_START, "end": _PERIOD_END},
    )

    state = await runtime.business_state.get_metric_state(
        StateRequest(
            metric_id=metric_id,
            time_range=TimeRangeV1(kind="absolute", start=_PERIOD_START, end=_PERIOD_END),
            agent_id=agent_id,
            as_of=_AS_OF,
            need=["actual"],
        )
    )

    last_day_value = last_day_fetch.readings[0].value if last_day_fetch.readings else None
    period_value = period_fetch.readings[0].value if period_fetch.readings else None
    bss_value = state.actual

    assert last_day_value is not None and period_value is not None and bss_value is not None, (
        f"missing data for {metric_id} over {_PERIOD_START}..{_PERIOD_END} -- "
        f"last_day={last_day_value}, period={period_value}, bss={bss_value}"
    )
    assert bss_value == pytest.approx(last_day_value, rel=1e-6), (
        f"BEHAVIOR CHANGE for {metric_id}: BusinessStateService.get_metric_state() "
        f"over a {_PERIOD_START}..{_PERIOD_END} range returned actual={bss_value}, "
        f"which no longer matches the last day's own value ({last_day_value}) -- "
        "lookup_fast_path.py's documented 'BSS returns last-day-point, not period "
        "total, for a multi-day range' assumption may no longer hold. Re-check "
        "every BSS caller that relies on this before proceeding."
    )
    # period_value is recorded, not asserted against anything -- for a
    # trending metric it will differ from last_day_value (that's the whole
    # point being characterized), but for a flat/zero-history metric it may
    # coincidentally match, so no assertion is safe here either way.


@pytest.mark.asyncio
async def test_characterize_dimensioned_fetch_has_no_business_state_equivalent(runtime):
    """Grain/breakdown fetches: a structural asymmetry, not a value mismatch.

    ``McpDataProvider.fetch(dimensions=...)`` returns one reading per
    dimension value (a real per-channel breakdown). ``BusinessStateService
    .get_metric_state()`` has no equivalent capability at all --
    ``facade.py`` reads exactly one key out of ``StateRequest.dimensions``
    (``brand_id``) and ignores everything else silently. This test proves
    that concretely: two BSS requests differing only in a ``channel``
    dimension value must return the identical ``actual``, because BSS never
    looks at it.

    This is recorded as a known, structural difference for Item 2's merge to
    decide on (does the unified fetcher gain grain support in BSS, or does
    BSS stay scalar-only and defer to McpDataProvider for breakdowns?)
    -- not something this test tries to make "pass" by working around it.
    """
    metric_id = "metric.units_sold"  # supported_dimensions: [sku, product_title]
    agent_id = "product_agent"
    dimension_key = "product_title"

    provider = McpDataProvider(
        _domain_for(agent_id),
        mcp=runtime.mcp,
        stats=McpFetchStats(),
        metrics=runtime.metrics,
        agent_id=agent_id,
    )
    breakdown = await provider.fetch(
        metric_ids=[metric_id],
        time_range={"start": _DAY, "end": _DAY},
        dimensions={dimension_key: ""},
    )
    assert len(breakdown.readings) >= 1, (
        f"McpDataProvider.fetch(dimensions={{{dimension_key!r}: ''}}) returned no rows "
        f"for {metric_id} on {_DAY} -- cannot characterize the grain asymmetry without live "
        "breakdown data"
    )
    distinct_values = {r.dimensions.get(dimension_key) for r in breakdown.readings if r.dimensions}
    probe_a, probe_b = (list(distinct_values)[:2] + ["__probe_a__", "__probe_b__"])[:2]

    state_a = await runtime.business_state.get_metric_state(
        StateRequest(
            metric_id=metric_id,
            time_range=TimeRangeV1(kind="absolute", start=_DAY, end=_DAY),
            dimensions={dimension_key: probe_a},
            agent_id=agent_id,
            as_of=_AS_OF,
            need=["actual"],
        )
    )
    state_b = await runtime.business_state.get_metric_state(
        StateRequest(
            metric_id=metric_id,
            time_range=TimeRangeV1(kind="absolute", start=_DAY, end=_DAY),
            dimensions={dimension_key: probe_b},
            agent_id=agent_id,
            as_of=_AS_OF,
            need=["actual"],
        )
    )

    assert state_a.actual is not None and state_b.actual is not None, (
        f"BusinessStateService returned no actual for {metric_id} -- "
        f"a={state_a.actual}, b={state_b.actual}"
    )
    assert state_a.actual == pytest.approx(state_b.actual, rel=1e-9), (
        "UNEXPECTED: BusinessStateService.get_metric_state() returned DIFFERENT "
        f"actual values ({state_a.actual} vs {state_b.actual}) for two requests that "
        f"differ only in a {dimension_key!r} dimension -- facade.py was expected to "
        "ignore every StateRequest.dimensions key except 'brand_id'. If this now "
        "differs, BSS gained grain support since this test was written; Item 2's "
        "'structural asymmetry' framing above is stale and should be re-checked, not "
        "just this assertion loosened."
    )
