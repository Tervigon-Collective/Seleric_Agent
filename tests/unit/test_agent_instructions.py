"""Content guard for ``agent/instructions.py``.

Live incident (2026-09-21): a "top-selling product" mission had no guidance
on how to find a valid dimension name for a non-time breakdown, so the model
guessed dimension key spellings one tool call at a time until it hit
pydantic_ai's ``tool_calls_limit`` (``UsageLimits`` in
``agent/validation/__init__.py``) without ever answering. ``get_metric_definition``
was already a registered tool returning ``supported_dimensions`` — the fix
is instructing the model to use it, not adding a new tool.
"""

from __future__ import annotations

from seleric_swarm.agent.instructions import INSTRUCTIONS


def test_non_time_breakdowns_are_told_to_check_supported_dimensions_first():
    assert "get_metric_definition" in INSTRUCTIONS
    assert "supported_dimensions" in INSTRUCTIONS
    assert "do not guess the" in INSTRUCTIONS.lower()


def test_agent_is_told_not_to_ask_permission_to_continue_a_lookup():
    """Live incident (2026-09-21): a plain "net sales last month" lookup
    called search_semantics, got the metric id, then ended the mission
    asking the user "want me to pull that number now?" instead of calling
    query_metrics itself — status "completed" with zero evidence. Rule 3
    ("you decide the next tool call yourself") already existed but wasn't
    blunt enough to override the model's instinct to ask permission."""
    lowered = INSTRUCTIONS.lower()
    assert "never end your turn to ask the user" in lowered
    assert "want me to pull the number now" in lowered


def test_agent_is_told_to_switch_metrics_for_entity_level_breakdowns_it_cant_support():
    """Live spot-check (2026-09-21): a metric on a summary-level view (only
    brand/date dimensions) can't answer "which products/ads drove this" no
    matter how it's queried, and nothing in the catalogue automatically
    points to the sibling metric that does support that breakdown. The model
    must know to re-search the catalogue for a sibling metric that carries
    the needed dimension instead of forcing a drilldown that will fail or
    silently answering with the wrong metric's number. Phrased generically
    (no hardcoded metric/dimension names) so it generalizes across catalogue
    changes instead of only matching the specific metrics from the incident."""
    lowered = INSTRUCTIONS.lower()
    assert "search the catalogue again" in lowered
    assert "supported_dimensions" in lowered
    assert "not necessarily identical" in lowered


def test_agent_labels_per_unit_figures_by_their_denominator():
    """Stress-test L9 (P3): a per-order average was labeled "LTV per customer".
    The label must match the denominator — orders are not customers."""
    lowered = INSTRUCTIONS.lower()
    assert "per order" in lowered and "per customer" in lowered
    assert "do not relabel orders as customers" in lowered


def test_agent_ranks_profitability_by_net_profit_not_contribution_margin():
    """Stress-test L8 (P3): Meta was called the "best" channel on contribution
    margin while it had the most-negative net profit. CM is pre-advertising, so
    it must not decide "best", and it must be labeled as pre-advertising."""
    collapsed = " ".join(INSTRUCTIONS.lower().split())
    assert "pre-advertising" in collapsed
    assert "rank by net profit, not contribution margin" in collapsed


def test_agent_ranks_superlative_comparisons_with_ordered_query_not_drilldown_python():
    """Live incident (2026-09-26): "compare the top product Aug'26 vs Aug'25"
    ran drilldown (one evidence row per product, ~180 rows/period) then tried
    run_python to pick the max. The model had to transcribe hundreds of opaque
    artifact ids into the sandbox, dropped a trailing char off several, and got
    INSUFFICIENT_EVIDENCE — a wasted round and most of the 72s. query_metrics
    already supports order+limit; a superlative-across-periods question is just
    a top-N query per period. Phrased generically (no product/metric/period
    names) so it generalizes."""
    lowered = INSTRUCTIONS.lower()
    assert "superlative entity is still a top-n query" in lowered
    assert "do not ``drilldown`` every row and then pick the max/min in" in lowered
    assert "never for a ranking or selection" in lowered


def test_agent_is_told_to_batch_independent_metric_fetches_in_one_turn():
    """Live incident (2026-09-21): a question needing three independent
    metrics (no metric derived from another) fetched them one per turn,
    ~30-40s apart each due to slow live queries, and blew the mission's 120s
    wall-clock budget after only 3 tool calls. query_metrics's metric_id
    parameter is a frozen signature (CONTRACTS.md) — it cannot take a list,
    so the fix is getting the model to issue independent query_metrics calls
    together in one turn (which run in parallel) instead of spread across
    turns (which run serially)."""
    lowered = INSTRUCTIONS.lower()
    assert "issue those" in lowered
    assert "run in" in lowered and "parallel" in lowered
