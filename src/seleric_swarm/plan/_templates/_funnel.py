"""Funnel-safe planning (AG08-03): validate stage populations against Core grains/views."""

from __future__ import annotations

from itertools import pairwise

from seleric_swarm.contracts.pipeline import AnalysisPlan, DeriveStep, Ref
from seleric_swarm.contracts.pipeline._report import FunnelDefinition, FunnelStage
from seleric_swarm.plan._templates._base import PlanContext, Prepared, Steps, carried, is_analysis
from seleric_swarm.semantic import SemanticReadModel

# Grains that count sessions (compatible with each other for stage ratios).
_SESSION_GRAINS = frozenset({"session", "sessions", "web_session"})
# Ad-delivery day grains: impressions/clicks progression, never chained into session funnel.
_AD_GRAINS = frozenset({"ad_day", "ad", "campaign_day", "paid_media_day"})
# Order / return grains that must not substitute for session purchases.
_ORDER_GRAINS = frozenset(
    {
        "brand_order_day",
        "brand_order_day_channel_ad",
        "order",
        "order_day",
        "return",
        "return_day",
    }
)


def _basis(model: SemanticReadModel, metric_id: str) -> str:
    grain = (model.grain_for(metric_id) or "").casefold()
    if grain in _SESSION_GRAINS or "session" in grain:
        return "session"
    if grain in _AD_GRAINS or grain.startswith("ad_"):
        return "ad_delivery"
    if grain in _ORDER_GRAINS or "order" in grain or "return" in grain:
        return "order"
    agg = model.aggregation_for(metric_id)
    if agg == "additive" and (model.unit_for(metric_id) or "").casefold() in ("count", "ratio", ""):
        return grain or "unknown"
    return grain or "unknown"


def validate_funnel(
    metric_ids: tuple[str, ...],
    read_model: SemanticReadModel,
) -> FunnelDefinition:
    """Typed funnel definition. Incompatible / missing stages are disclosed, never zeroed."""
    if len(metric_ids) < 2:
        return FunnelDefinition(kind="unsupported", disclosures=("need_at_least_two_stages",))

    stages: list[FunnelStage] = []
    for mid in metric_ids:
        if not read_model.has_metric(mid):
            stages.append(
                FunnelStage(metric_id=mid, status="missing", note="not_in_catalogue")
            )
            continue
        basis = _basis(read_model, mid)
        view = read_model.view_for(mid) or ""
        agg = read_model.aggregation_for(mid)
        status: str = "ok"
        note = ""
        if agg not in (None, "additive", "sum", "count") and not read_model.ratio_components_for(mid):
            # Rates without components are usable only when we also query their volume base.
            if agg == "ratio":
                note = "ratio_without_components"
            else:
                status = "incompatible"
                note = f"aggregation:{agg}"
        stages.append(
            FunnelStage(
                metric_id=mid,
                counting_basis=basis,
                view=view,
                status=status,  # type: ignore[arg-type]
                note=note,
            )
        )

    ok_bases = {s.counting_basis for s in stages if s.status == "ok"}
    disclosures: list[str] = []
    kind = "session"
    if ok_bases <= _SESSION_GRAINS | {"session"} or ok_bases == {"session"}:
        kind = "session"
    elif ok_bases <= {"ad_delivery"}:
        kind = "ad_delivery"
    elif len(ok_bases) > 1:
        kind = "mixed"
        disclosures.append("mixed_counting_bases:" + ",".join(sorted(ok_bases)))
        # Mark non-dominant stages incompatible so ratios are not computed across bases.
        dominant = "session" if "session" in ok_bases else next(iter(ok_bases))
        fixed: list[FunnelStage] = []
        for s in stages:
            if s.status == "ok" and s.counting_basis != dominant:
                fixed.append(
                    s.model_copy(
                        update={
                            "status": "incompatible",
                            "note": f"basis:{s.counting_basis}_vs_{dominant}",
                        }
                    )
                )
            else:
                fixed.append(s)
        stages = fixed
    elif not ok_bases:
        kind = "unsupported"
        disclosures.append("no_compatible_stages")

    # Order-grain metrics must not stand in as session purchases.
    fixed2: list[FunnelStage] = []
    for s in stages:
        if s.status == "ok" and kind == "session" and s.counting_basis == "order":
            fixed2.append(
                s.model_copy(
                    update={"status": "incompatible", "note": "order_cannot_substitute_session_purchase"}
                )
            )
            disclosures.append(f"rejected_order_stage:{s.metric_id}")
        else:
            fixed2.append(s)
    stages = fixed2

    views = {s.view for s in stages if s.status == "ok" and s.view}
    if len(views) > 1:
        disclosures.append("cross_view_stages:" + ",".join(sorted(views)))

    return FunnelDefinition(stages=tuple(stages), kind=kind, disclosures=tuple(disclosures))  # type: ignore[arg-type]


class FunnelTemplate:
    """Query each compatible stage, then derive funnel_stages (not adjacent share)."""

    id = "funnel"

    def matches(self, prepared: Prepared) -> bool:
        return is_analysis(prepared) and prepared.shape == "funnel" and len(prepared.metrics) >= 2

    def build(self, prepared: Prepared, ctx: PlanContext) -> AnalysisPlan | None:
        model = ctx.read_model
        definition = validate_funnel(prepared.metrics, model)
        steps = Steps()
        stage_ids: list[str] = []
        queryable = [s for s in definition.stages if s.status in ("ok", "unsupported")]
        # Still query "ok" stages; missing/incompatible are recorded via funnel_stages params.
        for stage in definition.stages:
            if stage.status not in ("ok",):
                continue
            # Prefer certified count components over rates when available.
            components = model.ratio_components_for(stage.metric_id)
            metric_id = stage.metric_id
            if components and components.get("numerator"):
                # Query numerator (count) so stage ratios recompute from certified components.
                num = components["numerator"]
                if model.has_metric(num) and model.aggregation_for(num) in (
                    None,
                    "additive",
                    "sum",
                    "count",
                ):
                    metric_id = num
            sid = steps.query(
                metric_id,
                role=prepared.event,
                breakdowns=carried(model, metric_id, prepared.breakdowns),
                filters=prepared.spec.filters,
                grain=prepared.spec.grain,
            )
            stage_ids.append(sid)
        if len(stage_ids) < 2 and not any(s.status == "missing" for s in definition.stages):
            # Fall back: query whatever was named and let derive mark unsupported stages.
            stage_ids = []
            for metric_id in prepared.metrics:
                stage_ids.append(
                    steps.query(
                        metric_id,
                        role=prepared.event,
                        breakdowns=carried(model, metric_id, prepared.breakdowns),
                        filters=prepared.spec.filters,
                        grain=prepared.spec.grain,
                    )
                )
        if not stage_ids:
            return None
        steps.derive(
            "funnel_stages",
            stage_ids,
            funnel=definition.model_dump(mode="json"),
            kind=definition.kind,
            disclosures=list(definition.disclosures),
        )
        return steps.plan(self.id)
