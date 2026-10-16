"""How the report writes a number and a metric: counts, percent points, multiples, currency, labels and changes (moved
from ``causal/diagnosis.py``, plan P2.2). The rules for shares live behind the ``ValueFormatting`` port."""

from __future__ import annotations

import math
from dataclasses import replace

from seleric_swarm.diagnosis.domain.lineage import MetricMeta
from seleric_swarm.diagnosis.domain.numeric import Series, _finite
from seleric_swarm.diagnosis.ports.formatting import ValueFormatting


def _fmt(v: float | None) -> str:
    if v is None or not math.isfinite(v):
        return "n/a"
    a = abs(v)
    if a >= 100:
        return f"{v:,.0f}"
    if a >= 1:
        return f"{v:,.2f}"
    return f"{v:.4g}"


def _pct(v: float | None) -> str:
    return "n/a" if v is None or not math.isfinite(v) else f"{v * 100:+.1f}%"


_SHARE_UNITS = frozenset({"ratio", "percent", "%"})


_MULTIPLE_UNIT = "multiple"


def _label(metric: str, lineage: dict[str, MetricMeta]) -> str:
    meta = lineage.get(metric)
    return (meta.label if meta and meta.label and meta.label != metric else metric.replace("_", " ")).strip()


def _val(v: float | None, metric: str, lineage: dict[str, MetricMeta], fmt: ValueFormatting) -> str:
    if v is None or not math.isfinite(v):
        return "n/a"
    meta = lineage.get(metric)
    unit = (meta.unit if meta else "") or ""
    if unit == _MULTIPLE_UNIT:
        return f"{v:.2f}x"
    # Share ratios (CTR, CVR, …): percent points, never double-scaled when the
    # source already stored 0–100. ROAS-like ratios stay plain numbers even when
    # the catalogue unit is ``ratio``.
    if fmt.is_percent_share(metric, unit) or (unit.lower() in _SHARE_UNITS and fmt.is_percent_share(metric, "ratio")):
        return f"{fmt.percent_points(v):.2f}%"
    if unit.isalpha() and unit.isupper() and len(unit) == 3:  # ISO currency code
        return f"{unit} {_fmt(v)}"
    return _fmt(v)


def _chg(new: float | None, old: float | None) -> str:
    if new is None or old is None or not math.isfinite(new) or not math.isfinite(old) or abs(old) < 1e-12:
        return "n/a"
    change = (new / old - 1) * 100
    return f"{change:+.1f}%" if abs(change) < 100 else f"{change:+.0f}%"  # a whole percent invites the model to recompute


def _share(v: float | None) -> str:
    return "n/a" if v is None or not math.isfinite(v) else f"{abs(v) * 100:.0f}%"


def _offsetting(shares: list[float | None]) -> bool:
    """Parts pull against each other harder than the net moved: a share of the change above 100% only says the
    net is small (live 2026-10-09: ROAS "+694% / −594% of the change"), so the parts are stated in the outcome's
    own units instead."""
    return any(v is not None and math.isfinite(v) and abs(v) > 1.0 for v in shares)


def _effect(contribution: float, outcome: str, lineage: dict[str, MetricMeta], *, fmt: ValueFormatting) -> str:
    """A part's effect on the outcome, signed, in the outcome's unit (a percent-point rate keeps its points)."""
    if not math.isfinite(contribution):
        return "n/a"
    text = _val(abs(contribution), outcome, lineage, fmt)
    return ("+" if contribution >= 0 else "−") + text


def _display_lineage(lineage: dict[str, MetricMeta], series: dict[str, Series]) -> dict[str, MetricMeta]:
    """Lineage with ratio units the data show are multiples, not shares.

    A catalogue "ratio" is a share (clicks per impression) when every observed
    value lies in [0, 1] and a multiple (sales per unit of spend) otherwise;
    a multiple printed as a percent reads "116%" for 1.16x.
    """
    out = dict(lineage)
    for m, meta in lineage.items():
        vals = [float(v) for v in series.get(m, {}).values() if _finite(v)]
        if meta.unit.lower() in _SHARE_UNITS and vals and max(abs(v) for v in vals) > 1.0:
            out[m] = replace(meta, unit=_MULTIPLE_UNIT)
    return out
