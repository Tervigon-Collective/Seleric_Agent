"""Domain ontology: candidate mechanisms per outcome metric.

Deterministic seed set the hypothesis generator draws from before (optionally)
asking the LLM for more. Keeps generation bounded and business-grounded:
each entry names a treatment metric, the owning domain(s), a mechanism sentence
and the parent/outcome it plausibly drives.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class MechanismTemplate:
    key: str
    statement: str
    mechanism: str
    treatment_metric: str
    outcome_metric: str
    domains: tuple[str, ...]
    evidence_hints: tuple[str, ...] = field(default_factory=tuple)
    is_symptom_only: bool = False


# outcome metric -> ordered candidate mechanisms (most specific first)
_ONTOLOGY: dict[str, tuple[MechanismTemplate, ...]] = {
    "metric.purchase_cvr": (
        MechanismTemplate(
            "mobile_latency_regression",
            "A frontend regression raised mobile latency, degrading mobile purchase conversion.",
            "higher LCP / JS error rate on mobile lowers checkout completion",
            "metric.mobile_lcp_seconds",
            "metric.purchase_cvr",
            ("technical", "funnel"),
            ("event.frontend_deployment", "metric.js_error_rate", "metric.mobile_lcp_seconds"),
        ),
        MechanismTemplate(
            "js_error_spike",
            "A JavaScript error spike broke checkout interactions, lowering conversion.",
            "client-side exceptions abort add-to-cart / checkout",
            "metric.js_error_rate",
            "metric.purchase_cvr",
            ("technical", "funnel"),
            ("metric.js_error_rate",),
        ),
        MechanismTemplate(
            "price_or_discount_change",
            "A price or discount change reduced conversion.",
            "higher effective price lowers willingness to purchase",
            "metric.avg_price",
            "metric.purchase_cvr",
            ("commerce",),
            ("event.price_change", "metric.discount_rate"),
        ),
        MechanismTemplate(
            "stock_availability",
            "Stock availability fell for high-traffic SKUs.",
            "out-of-stock PDPs cannot convert",
            "metric.in_stock_rate",
            "metric.purchase_cvr",
            ("inventory", "commerce"),
            ("metric.in_stock_rate",),
        ),
        MechanismTemplate(
            "payment_failure",
            "Payment failures rose, blocking completed purchases.",
            "declined / errored transactions prevent order creation",
            "metric.payment_failure_rate",
            "metric.purchase_cvr",
            ("technical", "commerce"),
            ("metric.payment_failure_rate",),
        ),
        MechanismTemplate(
            "traffic_mix_shift",
            "Paid traffic quality deteriorated (lower-intent clicks).",
            "worse-intent sessions convert less",
            "metric.paid_traffic_share",
            "metric.purchase_cvr",
            ("performance",),
            ("metric.ctr", "metric.cpc"),
        ),
        MechanismTemplate(
            "tracking_regression",
            "Conversion tracking broke, understating purchases.",
            "lost purchase events depress the measured rate without a real drop",
            "metric.tracking_coverage",
            "metric.purchase_cvr",
            ("technical",),
            ("event.tag_change",),
            is_symptom_only=False,
        ),
    ),
    "metric.cac": (
        MechanismTemplate(
            "downstream_cvr_decline",
            "A downstream purchase-conversion decline raised CAC while media stayed healthy.",
            "same spend / fewer orders inflates cost per acquisition",
            "metric.purchase_cvr",
            "metric.cac",
            ("performance", "funnel"),
            ("metric.purchase_cvr", "metric.spend"),
        ),
        MechanismTemplate(
            "auction_pressure",
            "Rising auction pressure increased CPM and therefore CAC.",
            "higher CPM at constant conversion raises acquisition cost",
            "metric.cpm",
            "metric.cac",
            ("performance",),
            ("metric.cpm", "metric.frequency"),
        ),
        MechanismTemplate(
            "creative_fatigue",
            "Creative fatigue lowered CTR, raising effective CPC and CAC.",
            "declining CTR at rising frequency raises cost per click",
            "metric.ctr",
            "metric.cac",
            ("performance",),
            ("metric.ctr", "metric.frequency"),
        ),
        MechanismTemplate(
            "attribution_change",
            "An attribution / tracking change shifted credited orders, moving reported CAC.",
            "fewer attributed orders at constant spend raises reported CAC",
            "metric.attributed_orders",
            "metric.cac",
            ("performance", "technical"),
            ("event.attribution_change", "event.tag_change"),
        ),
    ),
    "metric.net_sales": (
        MechanismTemplate(
            "conversion_decline",
            "A purchase-conversion decline reduced net sales.",
            "fewer completed orders at constant traffic lowers revenue",
            "metric.purchase_cvr",
            "metric.net_sales",
            ("funnel", "commerce"),
            ("metric.purchase_cvr",),
        ),
        MechanismTemplate(
            "returns_spike",
            "A returns spike cut net sales.",
            "higher return rate reduces net of gross sales",
            "metric.return_rate",
            "metric.net_sales",
            ("commerce",),
            ("metric.return_rate",),
        ),
        MechanismTemplate(
            "traffic_decline",
            "A traffic decline reduced net sales.",
            "fewer sessions at constant conversion lowers revenue",
            "metric.sessions",
            "metric.net_sales",
            ("funnel", "performance"),
            ("metric.sessions",),
        ),
    ),
}


def mechanisms_for(outcome_metric: str) -> tuple[MechanismTemplate, ...]:
    return _ONTOLOGY.get(outcome_metric, ())


def known_outcomes() -> list[str]:
    return list(_ONTOLOGY)


# Coarse routing label for downstream Prediction/Strategy (spec §122) — a
# classification hint, never a causal claim. Keyed on the stable mechanism
# ``key`` rather than ``domains`` because e.g. payment_failure and
# mobile_latency_regression share the "technical" domain but are distinct
# incident classes for routing purposes.
_INCIDENT_TYPE_BY_KEY: dict[str, str] = {
    "mobile_latency_regression": "technical",
    "js_error_spike": "technical",
    "price_or_discount_change": "pricing",
    "stock_availability": "inventory",
    "payment_failure": "payment",
    "traffic_mix_shift": "acquisition",
    "tracking_regression": "tracking",
    "downstream_cvr_decline": "funnel",
    "auction_pressure": "acquisition",
    "creative_fatigue": "acquisition",
    "attribution_change": "tracking",
    "conversion_decline": "funnel",
    "returns_spike": "commerce",
    "traffic_decline": "acquisition",
}


def incident_type_for_key(mechanism_key: str) -> str | None:
    return _INCIDENT_TYPE_BY_KEY.get(mechanism_key)


def incident_type_for_treatment(outcome_metric: str, treatment_metric: str) -> str | None:
    """Resolve incident_type from the ontology template a hypothesis came from.

    Hypotheses don't retain the template ``key`` (only the (treatment, outcome)
    pair), so this matches back on that pair — stable because each outcome's
    template set uses a unique treatment metric per mechanism.
    """
    for tmpl in mechanisms_for(outcome_metric):
        if tmpl.treatment_metric == treatment_metric:
            return incident_type_for_key(tmpl.key)
    return None
