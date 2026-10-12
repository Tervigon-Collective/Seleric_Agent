"""Number formatting from unit, aggregation and the catalogue display hint (no regex)."""

from __future__ import annotations

from seleric_swarm.answer._evidence import FRACTION_OPS, Resolved
from seleric_swarm.semantic import SemanticReadModel

MISSING = "n/a"


def is_fraction(resolved: Resolved, read_model: SemanticReadModel) -> bool:
    if resolved.op in FRACTION_OPS:
        return True
    if resolved.op is None and resolved.metric_id:
        for m in read_model.metrics:
            if m.id == resolved.metric_id:
                return str(m.extra.get("display") or "").lower() in {"percent", "share", "fraction"}
    return False


def format_value(resolved: Resolved, read_model: SemanticReadModel) -> str:
    value = resolved.value
    if value is None:
        return MISSING
    if is_fraction(resolved, read_model):
        sign = "+" if resolved.op == "pct_change" and value > 0 else ""
        return f"{sign}{value * 100:.1f}%"
    unit = (resolved.unit or "").strip()
    if unit.lower() in {"percent", "pct", "%"}:
        return f"{value:.1f}%"
    # Dimensionless "ratio" from derive/catalogue: fractions → percent; larger
    # magnitudes are bare numbers. Never suffix the word "ratio" into answers.
    if unit.lower() == "ratio":
        if abs(value) <= 1.0:
            sign = "+" if resolved.op == "pct_change" and value > 0 else ""
            return f"{sign}{value * 100:.1f}%"
        return _number(value)
    text = _number(value)
    if len(unit) == 3 and unit.isalpha() and unit.isupper():  # ISO currency code
        return f"{unit} {text}"
    return f"{text} {unit}" if unit and unit.lower() not in {"count", "number", "none"} else text


def _number(value: float) -> str:
    if float(value).is_integer() or abs(value) >= 1000:
        return f"{value:,.0f}"
    return f"{value:,.2f}"
