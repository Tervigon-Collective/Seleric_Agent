"""Values holding "|" and partial breakdowns (live 2026-10-08).

MS3-ed23dd03e2: the Google campaign "[Google Build] PMax - Seasonal New | 27th May" split its Markdown row, every
later column shifted one to the left, and the answer ranked a 21,776 "net ROAS" that was the campaign's ad spend.
The merge also joined 2 of 32 campaigns because one evidence id per metric was cited.
"""

from __future__ import annotations

import pytest

from seleric_swarm.agent.validation.answer_audit import escape_labels_in_tables, ragged_table, table_cells
from seleric_swarm.services.markdown import table_cell
from seleric_swarm.state.artifacts import InMemoryArtifactStore
from seleric_swarm.toolsets import analytics
from tests.unit.test_analytics_toolset import FakeRunContext, _deps, _put_evidence

PMAX = "[Google Build] PMax - Seasonal New | 27th May"


def test_a_cell_keeps_its_pipe_inside_and_escapes_once() -> None:
    assert table_cells(f"| {table_cell(PMAX)} | 21,776.21 | 2.15 |") == [PMAX, "21,776.21", "2.15"]
    assert table_cell(table_cell(PMAX)) == table_cell(PMAX)


def test_known_labels_are_escaped_in_the_answers_table_rows_only() -> None:
    text = f"{PMAX} led.\n\n| Campaign | Spend |\n| --- | ---: |\n| {PMAX} | 21,776.21 |\n"
    fixed = escape_labels_in_tables(text, {PMAX, "TH-149"})
    assert fixed.startswith(f"{PMAX} led.")  # prose untouched
    assert ragged_table(text) is not None and ragged_table(fixed) is None
    assert escape_labels_in_tables(fixed, {PMAX}) == fixed


@pytest.mark.asyncio
async def test_the_merge_reads_every_row_of_the_cited_queries_and_escapes_names() -> None:
    store = InMemoryArtifactStore()
    first: dict[str, str] = {}
    for metric, values in (("ad_spend", {PMAX: 21776.21, "TH-149": 39700.96}), ("net_roas", {PMAX: 2.15, "TH-149": 0.93})):
        for name, value in values.items():
            aid = _put_evidence(store, metric=metric, grain="none", start="2026-10-01", end="2026-10-07", value=value,
                                dimensions={"campaign_name": name})
            first.setdefault(metric, aid)
    out = await analytics.merge_evidence_breakdowns(FakeRunContext(_deps(store)), list(first.values()))
    assert out.success, out.summary
    rows = [line for line in out.summary.splitlines() if line.startswith("| ") and "---" not in line][1:]
    assert len(rows) == 2 and ragged_table(out.summary) is None
    assert any(table_cells(r)[0] == PMAX and table_cells(r)[1:3] == ["21,776.21", "2.15"] for r in rows)


@pytest.mark.asyncio
async def test_rows_with_no_breakdown_value_are_counted_not_dropped_silently(monkeypatch) -> None:
    # live 2026-10-08: 7 Suspender Boots orders came from ads missing from the ad dimension; the answer never said so
    from seleric_swarm.toolsets import semantic
    from tests.unit.test_postmortem_20261007 import IST, _Ctx, _deps as _pm_deps
    from datetime import datetime

    class _Mcp:
        async def call(self, *, agent_id, capability, arguments):
            rows = [{"ad_name": "TH-383-SUSPENDER-UGC", "ad_spend": "12"}, {"ad_name": None, "ad_spend": "7"}]
            return {"rows": rows, "provenance": {"query_id": "q", "currency": "INR"}}

    deps = _pm_deps(mcp=_Mcp())
    result = await semantic.query_metrics(
        _Ctx(deps), "ad_spend", dimensions={"ad_name": ""},
        period_start=datetime(2026, 10, 1, tzinfo=IST), period_end=datetime(2026, 10, 7, tzinfo=IST),
    )
    assert result.success, result.summary
    assert "has no ad_name value and is not listed above" in result.summary


@pytest.mark.asyncio
async def test_a_companion_fetched_as_its_own_top_n_is_fetched_again_not_reported_missing() -> None:
    # live 2026-10-08 MS3-3b022d8647: net profit by campaign (its own top 10) left 9 of the top 10 by net sales empty
    store = InMemoryArtifactStore()
    ids = []
    for name, value in (("A", 100.0), ("B", 90.0)):
        ids.append(_put_evidence(store, metric="net_sales", grain="none", start="2026-10-01", end="2026-10-07",
                                 value=value, dimensions={"campaign_name": name}))
    from seleric_swarm.agent.artifacts import EvidenceArtifact
    from seleric_swarm.conversations.contracts import Artifact, ArtifactProvenance
    from datetime import UTC, datetime

    profit = EvidenceArtifact(metric_id="net_profit", dimensions={"campaign_name": "A"}, grain="none",
                              as_of=datetime(2026, 10, 8, tzinfo=UTC), period_start=datetime(2026, 10, 1, tzinfo=UTC),
                              period_end=datetime(2026, 10, 7, tzinfo=UTC), value=5.0,
                              source_query={"measure": "net_profit", "limit": 10})
    ids.append(store.put(Artifact(workspace_id="ws1", artifact_type="evidence", payload=profit.model_dump(mode="json"),
                                  classification="factual", evidence_ids=["raw:p"], provenance=ArtifactProvenance(),
                                  mission_id="mission-1")).id)
    out = await analytics.merge_evidence_breakdowns(FakeRunContext(_deps(store)), ids)
    assert not out.success and out.retryable
    assert "own top 10" in out.summary and "'B'" in out.summary


@pytest.mark.asyncio
async def test_a_multi_dimension_breakdown_reports_subtotals_and_shares_as_a_finding() -> None:
    # golden Q17 2026-10-08: rows by channel × campaign were added up and divided by hand, and rejected as unbacked
    from datetime import datetime

    from seleric_swarm.toolsets import semantic
    from tests.unit.test_postmortem_20261007 import IST, _Ctx, _deps as _pm_deps

    class _Mcp:
        async def call(self, *, agent_id, capability, arguments):
            rows = [{"finance_channel": "meta", "campaign_name": "A", "ad_spend": "60"},
                    {"finance_channel": "meta", "campaign_name": "B", "ad_spend": "20"},
                    {"finance_channel": "google", "campaign_name": "C", "ad_spend": "20"}]
            return {"rows": rows, "provenance": {"query_id": "q", "currency": "INR"}}

    deps = _pm_deps(mcp=_Mcp())
    result = await semantic.query_metrics(
        _Ctx(deps), "ad_spend", dimensions={"finance_channel": "", "campaign_name": ""},
        period_start=datetime(2026, 10, 1, tzinfo=IST), period_end=datetime(2026, 10, 7, tzinfo=IST),
    )
    assert result.success, result.summary
    assert "by finance_channel: meta=80" in result.summary and "(80.0%)" in result.summary
    findings = [a for a in deps.artifact_store.list_for_mission(deps.mission_id) if a.artifact_type == "finding"]
    assert findings and findings[0].payload["metrics"]["ad_spend | finance_channel=meta | share_pct"] == 80.0
