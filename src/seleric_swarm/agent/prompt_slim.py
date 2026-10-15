"""What the loop trims from the text it sends on every step (the prefix was ~13.9k tokens; this is ~11k).

Chosen by an A/B on 2026-10-14 (6 questions x 2 rounds x 4 variants, blind gpt-4o judge, ClickHouse/Core checks): the
trimmed prompt kept every check, scored the same on depth / clarity / completeness and ran 18% faster with 20% fewer
429 fallbacks. See plans/SLIM_LOOP_PLAN.md.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from pydantic_ai.tools import ToolDefinition

# Core guide sections that duplicate the Answer rules or the working rules (start marker, end marker, replacement).
_GUIDE_CUTS = (
    (
        "USER-FACING RESPONSE FORMAT", "NON-NEGOTIABLE RULES",
        ("When intent is clear, apply reasonable defaults and state them briefly; do not ask a clarifying question "
         "when a useful best-effort answer can be given. Never show internal plumbing (query ids, cube names, metric ids, "
         "schema paths). A missing value is \"No data available\", never \"null\".\n\n"),
    ),
    (
        "5. Never calculate derived results yourself.", "6. Ground every number",
        ("5. Prefer tool-computed deltas, percentages and ratios: metrics_query with compare_period, insights_explain; "
         "other arithmetic goes through run_python.\n\n"),
    ),
    (
        "9. Answer reconciliation questions directly.", "10. Do not speculate.",
        ("9. Reconciliation questions (\"why don't these numbers match?\"): pick the natural framing, query the metrics "
         "and explain the gap from returned figures (deductions, refunds, timing, P&L treatment) without asking the user "
         "to choose a query design first.\n\n"),
    ),
)

def slim_guide(text: str) -> str:
    """The Core guide without the sections that repeat the Answer rules or the working rules."""
    for start, end, replacement in _GUIDE_CUTS:
        head, found, rest = text.partition(start)
        if not found:
            continue
        _, found_end, tail = rest.partition(end)
        if found_end:
            text = head + replacement + end + tail
    return text


def _unwrap_nullable(node: Any) -> Any:
    if isinstance(node, dict):
        any_of = node.get("anyOf")
        if isinstance(any_of, list) and len(any_of) == 2:
            kept = [a for a in any_of if not (isinstance(a, dict) and a.get("type") == "null")]
            if len(kept) == 1:
                merged = {k: v for k, v in node.items() if k != "anyOf"}
                merged.update(kept[0])
                node = merged
        return {k: _unwrap_nullable(v) for k, v in node.items() if not (k == "default" and v is None)}
    if isinstance(node, list):
        return [_unwrap_nullable(v) for v in node]
    return node


def slim_defs(defs: list[ToolDefinition]) -> list[ToolDefinition]:
    return [
        dataclasses.replace(
            d, parameters_json_schema=_unwrap_nullable(d.parameters_json_schema),
        )
        for d in defs
    ]
