"""Campaign / ad ranking templates (AG08-04).

Criterion metrics are selected by catalogue facts (category, unit, aggregation,
ratio_components), never by hardcoded name literals in business copy. Identity
uses stable_key dimensions; null identities go to reconciliation, not named ranks.
"""

from __future__ import annotations

from seleric_swarm.contracts.pipeline import AnalysisPlan, RankingSpec
from seleric_swarm.plan._templates._base import (
    PlanContext,
    Prepared,
    Steps,
    carried,
    is_analysis,
)
from seleric_swarm.semantic import SemanticReadModel
from seleric_swarm.toolsets import policy_config as P

# Criterion families expressed as catalogue signals (category tokens / unit patterns).
_CRITERIA: tuple[tuple[str, str, str, str], ...] = (
    # label_key, category_hint, unit_hint, order
    ("spend", "paid_media", "inr", "desc"),
    ("delivery_efficiency", "paid_media", "ratio", "desc"),
    ("attributed_sales", "paid_media", "inr", "desc"),
    ("net_profit", "profit", "inr", "desc"),
    ("largest_loss", "profit", "inr", "asc"),
)


def _category(model: SemanticReadModel, mid: str) -> str:
    return (model.category_for(mid) or "").casefold()


def _unit(model: SemanticReadModel, mid: str) -> str:
    return (model.unit_for(mid) or "").casefold()


def _pick_criterion_metrics(
    model: SemanticReadModel, available: tuple[str, ...]
) -> list[tuple[str, str, str, RankingSpec]]:
    """Return (label, metric_id, order, RankingSpec) for each criterion we can bind."""
    out: list[tuple[str, str, str, RankingSpec]] = []
    used: set[str] = set()
    for label, cat_hint, unit_hint, order in _CRITERIA:
        pick = None
        for mid in available:
            if mid in used:
                continue
            cat = _category(model, mid)
            unit = _unit(model, mid)
            agg = model.aggregation_for(mid)
            if label == "spend" and ("paid" in cat or "media" in cat or "ad" in cat) and unit in (
                "inr",
                "usd",
                "currency",
                "money",
            ):
                pick = mid
            elif label == "delivery_efficiency" and agg == "ratio" and model.ratio_components_for(mid):
                # Efficiency ratios (CTR, CPC inverted sense handled by order) with components.
                if "paid" in cat or "media" in cat or unit in ("ratio", "inr"):
                    pick = mid
            elif label == "attributed_sales" and (
                "sales" in mid.casefold() or "revenue" in mid.casefold() or "order" in cat
            ) and unit in ("inr", "usd", "currency", "money", "count"):
                pick = mid
            elif label in ("net_profit", "largest_loss") and (
                "profit" in cat or "pnl" in cat or "profit" in mid.casefold()
            ):
                pick = mid
            elif cat_hint in cat and (unit_hint in unit or unit_hint == "ratio" and agg == "ratio"):
                pick = mid
            if pick:
                break
        if pick is None:
            continue
        used.add(pick)
        limit = int(getattr(P, "RANK_DEFAULT_LIMIT", 10) or 10)
        out.append(
            (
                label,
                pick,
                order,
                RankingSpec(by_metric_id=pick, order=order, limit=limit),  # type: ignore[arg-type]
            )
        )
    return out


def _identity_dims(model: SemanticReadModel, entity: str | None, breakdowns: tuple[str, ...]) -> tuple[str, ...]:
    """Prefer stable_key companion + display name; fall back to the entity dim."""
    dims: list[str] = []
    primary = entity or (breakdowns[0] if breakdowns else None)
    if not primary:
        return ()
    key = model.stable_key_for(primary)
    if key and key != primary:
        dims.append(key)
    dims.append(primary)
    for d in breakdowns:
        if d not in dims:
            dims.append(d)
    return tuple(dims)


class RankingTemplate:
    """One ranking query per explicit criterion; null-identity rows reconciled separately."""

    id = "ranking"
    shapes = frozenset({"ranking", "entity_comparison"})

    def matches(self, prepared: Prepared) -> bool:
        if not is_analysis(prepared):
            return False
        if prepared.shape not in self.shapes and prepared.ranking is None:
            return False
        return bool(prepared.metrics or prepared.ranking) and bool(
            prepared.entity or prepared.breakdowns
        )

    def build(self, prepared: Prepared, ctx: PlanContext) -> AnalysisPlan | None:
        model = ctx.read_model
        steps = Steps()
        dims = _identity_dims(model, prepared.entity, prepared.breakdowns)
        if not dims:
            return None

        metrics = list(prepared.metrics)
        if prepared.ranking is not None and prepared.ranking.by_metric_id not in metrics:
            metrics.insert(0, prepared.ranking.by_metric_id)

        # Explicit single ranking from the user.
        if prepared.ranking is not None and prepared.shape != "ranking":
            mid = prepared.ranking.by_metric_id
            sliced = carried(model, mid, dims)
            if not sliced:
                return None
            steps.query(
                mid,
                role=prepared.event,
                breakdowns=sliced,
                filters=prepared.spec.filters,
                grain=prepared.spec.grain,
                ranking=prepared.ranking,
            )
            # Null-identity reconciliation: sum of the unsliced total vs named rows.
            steps.query(
                mid,
                role=prepared.event,
                breakdowns=(),
                filters=prepared.spec.filters,
                grain=prepared.spec.grain,
            )
            return steps.plan(self.id)

        criteria = _pick_criterion_metrics(model, tuple(metrics))
        if not criteria and prepared.ranking is not None:
            criteria = [
                (
                    "explicit",
                    prepared.ranking.by_metric_id,
                    prepared.ranking.order,
                    prepared.ranking,
                )
            ]
        if not criteria:
            # Unclear criterion: emit one ranking per available metric, labelled explicitly.
            for mid in metrics:
                sliced = carried(model, mid, dims)
                if not sliced:
                    continue
                ranking = RankingSpec(
                    by_metric_id=mid,
                    order="desc",
                    limit=int(getattr(P, "RANK_DEFAULT_LIMIT", 10) or 10),
                )
                sid = steps.query(
                    mid,
                    role=prepared.event,
                    breakdowns=sliced,
                    filters=prepared.spec.filters,
                    grain=prepared.spec.grain,
                    ranking=ranking,
                )
                steps.derive("rank", [sid], order="desc", limit=ranking.limit, criterion=mid)
            return steps.plan(self.id) if steps.has_steps else None

        for label, mid, order, ranking in criteria:
            sliced = carried(model, mid, dims)
            if not sliced:
                continue
            # Stable id must be in the slice when Core declares one.
            sid = steps.query(
                mid,
                role=prepared.event,
                breakdowns=sliced,
                filters=prepared.spec.filters,
                grain=prepared.spec.grain,
                ranking=ranking,
            )
            steps.derive(
                "rank",
                [sid],
                order=order,
                limit=ranking.limit,
                criterion=label,
                metric_id=mid,
            )
            # Unsliced total for reconciliation of unnamed / null-identity spend.
            total_id = steps.query(
                mid,
                role=prepared.event,
                breakdowns=(),
                filters=prepared.spec.filters,
                grain=prepared.spec.grain,
            )
            steps.derive("reconcile", [sid, total_id], criterion=label)

        return steps.plan(self.id) if steps.has_steps else None
