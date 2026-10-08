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
