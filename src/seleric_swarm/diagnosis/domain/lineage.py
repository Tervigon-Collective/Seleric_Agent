"""What the engine knows about a catalogue metric: its aggregation, unit, grain and what it is computed from, and the
questions asked of that (what is built from it, what it is built from, the units it counts). Moved from
``causal/diagnosis.py`` (plan P2.2); pure, no catalogue calls."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from seleric_swarm.diagnosis.domain.config import DEFAULT_CALENDAR_GRAIN_TOKENS


@dataclass(frozen=True)
class MetricMeta:
    """The lineage facts the engine needs about one catalogue metric."""

    id: str
    aggregation: str = ""
    view: str = ""
    depends_on: tuple[str, ...] = ()
    label: str = ""
    grain: str = ""
    unit: str = ""

    @property
    def additive(self) -> bool:
        return self.aggregation == "additive"

    def entity_tokens(
        self, scope_tokens: frozenset[str] = frozenset(), calendar_tokens: tuple[str, ...] = DEFAULT_CALENDAR_GRAIN_TOKENS
    ) -> frozenset[str]:
        """The units a metric is counted on, from its catalogue grain.

        ``brand_order_day_channel_ad`` -> {order, channel, ad}: calendar words and
        scope keys (the tenant dimension every view carries) are not units.
        """
        tokens = {t for t in self.grain.lower().replace("-", "_").split("_") if t}
        return frozenset(tokens - set(calendar_tokens) - scope_tokens)


def lineage_from_definitions(definitions: dict[str, dict[str, Any]]) -> dict[str, MetricMeta]:
    """Catalogue definitions (``catalogue_get_metric(s)``) -> lineage map."""
    out: dict[str, MetricMeta] = {}
    for mid, d in definitions.items():
        if not isinstance(d, dict):
            continue
        raw_formula = d.get("formula")
        formula: dict[str, Any] = raw_formula if isinstance(raw_formula, dict) else {}
        view = (d.get("cube_mapping") or {}).get("view") or d.get("view") or ""
        out[mid] = MetricMeta(
            id=mid,
            aggregation=str(d.get("aggregation") or "").lower(),
            view=str(view),
            depends_on=tuple(str(x) for x in (formula.get("depends_on") or []) if x),
            label=str(d.get("display_name") or d.get("label") or mid),
            grain=str(d.get("grain") or ""),
            unit=str(d.get("unit") or d.get("currency_default") or ""),
        )
    return out


def unit_count_metric(metric_id: str, lineage: dict[str, MetricMeta]) -> str | None:
    """The count of the units an outcome is recorded on — its catalogue grain's own count (grain ``order`` →
    ``orders``, ``session`` → ``sessions``) on the same view — or None. A driver in lockstep with it counts the
    outcome's own events: the web funnel's purchases follow orders at r=0.97 but gross sales only at 0.94, and were
    named the cause of a sales drop (live 2026-10-10 thread_066b9cd1, as on 2026-09-24)."""
    meta = lineage.get(metric_id)
    if meta is None or not meta.grain:
        return None
    for unit in sorted(meta.entity_tokens()):
        for cand in (unit, f"{unit}s"):
            m = lineage.get(cand)
            if cand != metric_id and m is not None and m.additive and m.view == meta.view:
                return cand
    return None


def descendants(metric_id: str, lineage: dict[str, MetricMeta]) -> set[str]:
    """Metrics computed (directly or transitively) from ``metric_id``."""
    children: dict[str, set[str]] = {}
    for m in lineage.values():
        for dep in m.depends_on:
            children.setdefault(dep, set()).add(m.id)
    seen: set[str] = set()
    stack = [metric_id]
    while stack:
        for child in children.get(stack.pop(), ()):
            if child not in seen:
                seen.add(child)
                stack.append(child)
    seen.discard(metric_id)
    return seen


def lineage_in_degree(lineage: dict[str, MetricMeta]) -> dict[str, int]:
    """How many catalogue metrics are built on each metric (a hub score)."""
    deg: dict[str, int] = {}
    for m in lineage.values():
        for dep in m.depends_on:
            deg[dep] = deg.get(dep, 0) + 1
    return deg
