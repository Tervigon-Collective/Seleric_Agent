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
