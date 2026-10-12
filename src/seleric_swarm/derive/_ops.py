"""Typed derivation ops (M2.3).

Every op turns one or more ``ResultSet`` / ``Derivation`` inputs into an
:class:`Outcome`, or raises :class:`Refusal` when the arithmetic is not defined
for the catalogue. The engine turns a refusal into a ``Derivation`` carrying a
``StageError``, so a bad op never aborts a mission.

This module is L2: it may read ``seleric_swarm.semantic`` and the frozen
contracts, but never ``re``, an I/O SDK, or ``seleric_swarm.adapters``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from seleric_swarm.contracts.pipeline import (
    Derivation,
    JsonValue,
    ResultRow,
    ResultSet,
    WindowSpec,
)
from seleric_swarm.semantic import SemanticReadModel

DeriveInput = ResultSet | Derivation


class Refusal(Exception):
    """A known op that is not defined for these inputs. Carries a stable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Outcome:
    value: float | None
    unit: str | None = None
    rows: tuple[ResultRow, ...] = ()
    params: tuple[tuple[str, JsonValue], ...] = ()


_NON_ADDITIVE = frozenset(
    {
        "avg",
        "average",
        "mean",
        "median",
        "min",
        "max",
        "count_distinct",
        "distinct",
        "percentile",
        "rate",
    }
)


def _value(item: DeriveInput) -> float | None:
    if isinstance(item, Derivation):
        return item.value
    values = [row.value for row in item.rows if row.value is not None]
    return float(sum(values)) if values else None


def _unit(item: DeriveInput) -> str | None:
    return item.unit


def _rows(item: DeriveInput) -> tuple[ResultRow, ...]:
    return item.rows


def _window(item: DeriveInput) -> WindowSpec | None:
    return item.window if isinstance(item, ResultSet) else None


def _metric_id(item: DeriveInput) -> str | None:
    return item.metric_id if isinstance(item, ResultSet) else None


def _span_days(window: WindowSpec) -> int:
    span = (window.end - window.start).days
    return span if span > 0 else 1


def _row_key(row: ResultRow, on: Sequence[str]) -> tuple[str | None, ...]:
    return tuple(row.dimensions.get(dim) for dim in on)


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _as_string_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    if isinstance(value, str):
        return [value]
    return []


def _composition(read_model: SemanticReadModel, metric_id: str) -> Any:
    for record in read_model.metrics:
        if record.id == metric_id:
            return record.composition
    return None


def _require_count(inputs: Sequence[DeriveInput], count: int, op: str) -> None:
    if len(inputs) != count:
        raise Refusal("input_count", f"{op} takes {count} input(s), got {len(inputs)}")


def _require_result_set(item: DeriveInput, op: str) -> ResultSet:
    if not isinstance(item, ResultSet):
        raise Refusal("not_a_result_set", f"{op} needs a ResultSet input")
    return item


def _same_units(a: DeriveInput, b: DeriveInput) -> None:
    unit_a, unit_b = _unit(a), _unit(b)
    if unit_a is not None and unit_b is not None and unit_a != unit_b:
        raise Refusal("unit_mismatch", f"units differ: {unit_a!r} vs {unit_b!r}")


def _per_day_value(item: DeriveInput, op: str) -> float | None:
    value = _value(item)
    if value is None:
        return None
    window = _window(item)
    if window is None:
        raise Refusal("no_window", f"{op} per_day needs a window")
    return value / _span_days(window)


def _op_sum(
    inputs: Sequence[DeriveInput], params: Mapping[str, JsonValue], read_model: SemanticReadModel
) -> Outcome:
    _require_count(inputs, 1, "sum")
    result = _require_result_set(inputs[0], "sum")
    aggregation = read_model.aggregation_for(result.metric_id)
    if aggregation in _NON_ADDITIVE:
        raise Refusal(
            "not_additive",
            f"metric {result.metric_id!r} aggregates by {aggregation!r}; sum is not defined",
        )
    where = params.get("where")
    rows = result.rows
    if isinstance(where, Mapping) and where:
        wanted = {str(dim): set(_as_string_list(values)) for dim, values in where.items()}
        rows = tuple(
            row
            for row in rows
            if all(row.dimensions.get(dim) in allowed for dim, allowed in wanted.items())
        )
    present = [row.value for row in rows if row.value is not None]
    return Outcome(value=float(sum(present)) if present else None, unit=result.unit, rows=rows)


def _op_share(
    inputs: Sequence[DeriveInput], params: Mapping[str, JsonValue], read_model: SemanticReadModel
) -> Outcome:
    _require_count(inputs, 2, "share")
    part, whole = inputs
    _same_units(part, whole)
    whole_value = _value(whole)
    if whole_value in (None, 0):
        raise Refusal("zero_whole", "share needs a non-zero whole")
    part_value = _value(part)
    return Outcome(value=None if part_value is None else part_value / whole_value, unit=None)


def _change(
    inputs: Sequence[DeriveInput],
    params: Mapping[str, JsonValue],
    *,
    percent: bool,
) -> Outcome:
    name = "pct_change" if percent else "delta"
    _require_count(inputs, 2, name)
    before, after = inputs
    _same_units(before, after)
    per_day = bool(params.get("per_day"))
    window_before, window_after = _window(before), _window(after)
    if (
        window_before is not None
        and window_after is not None
        and not per_day
        and _span_days(window_before) != _span_days(window_after)
    ):
        raise Refusal("window_length_mismatch", f"{name} needs equal-length windows unless per_day")
    if per_day:
        base, new = _per_day_value(before, name), _per_day_value(after, name)
    else:
        base, new = _value(before), _value(after)
    if base is None or new is None:
        return Outcome(value=None, unit=None if percent else _unit(after))
    if percent:
        if base == 0:
            raise Refusal("zero_base", "pct_change needs a non-zero baseline")
        return Outcome(value=(new - base) / base, unit=None)
    return Outcome(value=new - base, unit=_unit(after))


def _op_delta(
    inputs: Sequence[DeriveInput], params: Mapping[str, JsonValue], read_model: SemanticReadModel
) -> Outcome:
    return _change(inputs, params, percent=False)


def _op_pct_change(
    inputs: Sequence[DeriveInput], params: Mapping[str, JsonValue], read_model: SemanticReadModel
) -> Outcome:
    return _change(inputs, params, percent=True)


def _op_per_day(
    inputs: Sequence[DeriveInput], params: Mapping[str, JsonValue], read_model: SemanticReadModel
) -> Outcome:
    _require_count(inputs, 1, "per_day")
    item = inputs[0]
    unit = _unit(item)
    return Outcome(value=_per_day_value(item, "per_day"), unit=f"{unit}/day" if unit else None)


def _op_ratio(
    inputs: Sequence[DeriveInput], params: Mapping[str, JsonValue], read_model: SemanticReadModel
) -> Outcome:
    _require_count(inputs, 2, "ratio")
    numerator, denominator = inputs
    declared = params.get("metric_id")
    candidate = declared if isinstance(declared, str) else _metric_id(numerator)
    if candidate is not None and _composition(read_model, candidate) is not None:
        raise Refusal("composition_defined", f"{candidate!r} declares a composition; use recompute")
    bottom = _value(denominator)
    if bottom in (None, 0):
        raise Refusal("zero_denominator", "ratio needs a non-zero denominator")
    top = _value(numerator)
    return Outcome(value=None if top is None else top / bottom, unit=None)


def _op_recompute(
    inputs: Sequence[DeriveInput], params: Mapping[str, JsonValue], read_model: SemanticReadModel
) -> Outcome:
    metric_id = params.get("metric_id")
    if not isinstance(metric_id, str) or not metric_id:
        raise Refusal("missing_metric", "recompute needs params.metric_id")
    composition = _composition(read_model, metric_id)
    if composition is None:
        raise Refusal("no_composition", f"metric {metric_id!r} declares no composition")
    components = {
        item.metric_id: _value(item) for item in inputs if isinstance(item, ResultSet)
    }

    def component_value(component_id: Any) -> float | None:
        if not isinstance(component_id, str) or component_id not in components:
            raise Refusal("missing_components", f"no input for component {component_id!r}")
        return components[component_id]

    terms: Any = composition
    if isinstance(composition, Mapping):
        numerator, denominator = composition.get("numerator"), composition.get("denominator")
        if isinstance(numerator, str) and isinstance(denominator, str):
            top, bottom = component_value(numerator), component_value(denominator)
            if bottom in (None, 0):
                raise Refusal("zero_denominator", "recompute needs a non-zero denominator")
            value = None if top is None else top / bottom
            return Outcome(value=value, unit=read_model.unit_for(metric_id))
        terms = composition.get("terms") or composition.get("components")
    if isinstance(terms, (list, tuple)):
        total = 0.0
        seen = False
        for term in terms:
            if not isinstance(term, Mapping):
                raise Refusal("unsupported_composition", "composition terms must be objects")
            coefficient = _as_float(term.get("coefficient", 1.0))
            term_value = component_value(term.get("metric"))
            if coefficient is None or term_value is None:
                continue
            total += coefficient * term_value
            seen = True
        return Outcome(value=total if seen else None, unit=read_model.unit_for(metric_id))
    raise Refusal("unsupported_composition", f"cannot read composition for {metric_id!r}")


def _op_reconcile(
    inputs: Sequence[DeriveInput], params: Mapping[str, JsonValue], read_model: SemanticReadModel
) -> Outcome:
    if len(inputs) < 2:
        raise Refusal("input_count", "reconcile needs parts and a total")
    parts, total = inputs[:-1], inputs[-1]
    total_value = _value(total)
    if total_value is None:
        raise Refusal("no_total", "reconcile needs a total value")
    for part in parts:
        _same_units(part, total)
    explained = sum(value for value in (_value(part) for part in parts) if value is not None)
    residual = total_value - explained
    rows = tuple(
        ResultRow(
            evidence_id=_rows(part)[0].evidence_id if _rows(part) else part.ref.id,
            dimensions={"part": part.ref.id},
            value=_value(part),
        )
        for part in parts
    )
    explained_share = 1.0 - residual / total_value if total_value else None
    return Outcome(
        value=residual,
        unit=_unit(total),
        rows=rows,
        params=(("residual", residual), ("explained_share", explained_share)),
    )


def _op_rank(
    inputs: Sequence[DeriveInput], params: Mapping[str, JsonValue], read_model: SemanticReadModel
) -> Outcome:
    _require_count(inputs, 1, "rank")
    item = inputs[0]
    order = params.get("order", "desc")
    if order not in ("asc", "desc"):
        raise Refusal("bad_order", "rank order must be 'asc' or 'desc'")
    limit = _as_float(params.get("limit"))
    if limit is None or limit <= 0:
        raise Refusal("bad_limit", "rank needs a positive integer limit")
    by = params.get("by", "value")

    def metric(row: ResultRow) -> float | None:
        if by in (None, "value"):
            return row.value
        return _as_float(row.dimensions.get(str(by)))

    rows = list(_rows(item))
    present = [row for row in rows if metric(row) is not None]
    absent = [row for row in rows if metric(row) is None]
    present.sort(key=lambda row: metric(row) or 0.0, reverse=order == "desc")
    return Outcome(value=None, unit=_unit(item), rows=tuple((present + absent)[: int(limit)]))


def _op_join(
    inputs: Sequence[DeriveInput], params: Mapping[str, JsonValue], read_model: SemanticReadModel
) -> Outcome:
    if len(inputs) < 2:
        raise Refusal("input_count", "join needs at least two ResultSets")
    results = [_require_result_set(item, "join") for item in inputs]
    on = _as_string_list(params.get("on"))
    if not on:
        raise Refusal("no_keys", "join needs 'on' dimensions")
    for result in results:
        missing = [dim for dim in on if dim not in set(result.breakdowns)]
        if missing:
            raise Refusal("keys_not_carried", f"rows do not carry join key(s) {missing}")
    keys = [{_row_key(row, on) for row in result.rows} for result in results[1:]]
    surviving = [
        row for row in results[0].rows if all(_row_key(row, on) in key_set for key_set in keys)
    ]
    return Outcome(value=None, unit=results[0].unit, rows=tuple(surviving))


def mix_shift_decomposition(
    baseline_weights: Mapping[str, float],
    event_weights: Mapping[str, float],
    baseline_rates: Mapping[str, float],
    event_rates: Mapping[str, float],
) -> dict[str, float]:
    """Split a ratio's change into within-segment, mix and interaction parts.

    For r = Σ wᵢ·rᵢ the exact identity is

        Δr = Σ w₀ᵢ·Δrᵢ  +  Σ Δwᵢ·r₀ᵢ  +  Σ Δwᵢ·Δrᵢ
             within        mix          interaction

    where Δrᵢ = r₁ᵢ − r₀ᵢ and Δwᵢ = w₁ᵢ − w₀ᵢ. Segments present in only one window
    enter with the missing side as 0, so a new/disappeared entity shows up in the
    mix and interaction terms rather than being silently attributed to a segment
    value change (AG-02).
    """
    within = mix = interaction = 0.0
    for key in set(baseline_weights) | set(event_weights):
        w0 = baseline_weights.get(key, 0.0)
        w1 = event_weights.get(key, 0.0)
        r0 = baseline_rates.get(key, 0.0)
        r1 = event_rates.get(key, 0.0)
        within += w0 * (r1 - r0)
        mix += (w1 - w0) * r0
        interaction += (w1 - w0) * (r1 - r0)
    return {"within": within, "mix": mix, "interaction": interaction}


OpFunc = Callable[[Sequence[DeriveInput], Mapping[str, JsonValue], SemanticReadModel], Outcome]

def _op_funnel_stages(
    inputs: Sequence[DeriveInput], params: Mapping[str, JsonValue], read_model: SemanticReadModel
) -> Outcome:
    """Recompute stage conversion / drop-off from certified counts (AG08-03).

    Stages marked missing/incompatible in ``params.funnel`` become null rows —
    never numeric zero. Largest supported leakage is recorded in params.
    """
    if len(inputs) < 1:
        raise Refusal("input_count", "funnel_stages needs stage ResultSets")
    funnel = params.get("funnel") if isinstance(params.get("funnel"), Mapping) else {}
    stage_meta = list(funnel.get("stages") or []) if isinstance(funnel, Mapping) else []
    # Inputs are the queried stages only (template skips missing ones). Match
    # catalogue status by metric_id — never by input index against the full
    # FunnelDefinition.stages list (that mis-labels later stages as "missing").
    meta_by_metric = {
        str(s.get("metric_id")): s
        for s in stage_meta
        if isinstance(s, Mapping) and s.get("metric_id")
    }
    values: list[float | None] = []
    labels: list[str] = []
    statuses: list[str] = []
    units: list[str | None] = []
    for index, item in enumerate(inputs):
        mid = _metric_id(item) or f"stage_{index}"
        labels.append(read_model.label_for(mid) or mid)
        meta = meta_by_metric.get(mid) or {}
        status = str(meta.get("status") or "ok")
        statuses.append(status)
        unit = None
        if isinstance(item, ResultSet) and item.unit:
            unit = str(item.unit).strip().lower() or None
        if unit is None and mid:
            declared = read_model.unit_for(mid)
            unit = str(declared).strip().lower() or None if declared else None
        units.append(unit)
        if status in ("missing", "incompatible", "unsupported") or (
            isinstance(item, ResultSet) and item.error is not None
        ):
            values.append(None)
            continue
        values.append(_value(item))

    rows: list[ResultRow] = []
    conversions: list[float | None] = []
    dropoffs: list[float | None] = []
    largest_leak: float | None = None
    largest_leak_stage = ""
    for index, (label, value, status) in enumerate(zip(labels, values, statuses)):
        prev = values[index - 1] if index > 0 else None
        conversion = None
        dropoff = None
        # Stage conversion is only meaningful when adjacent stages share a unit
        # (count→count, INR→INR). Cross-unit pairs (e.g. purchases count vs
        # revenue currency) must not invent a "rate" that then renders as %.
        # Unknown units on both sides keep prior behaviour (allow conversion).
        same_unit = index > 0 and (
            units[index] == units[index - 1]
            or (units[index] is None and units[index - 1] is None)
        )
        if (
            index > 0
            and status == "ok"
            and statuses[index - 1] == "ok"
            and same_unit
            and value is not None
            and prev not in (None, 0)
        ):
            conversion = value / prev
            if 0.0 <= conversion <= 1.0:
                dropoff = 1.0 - conversion
                if largest_leak is None or dropoff > largest_leak:
                    largest_leak = dropoff
                    largest_leak_stage = label
            else:
                # Expansion across stages is not a funnel conversion; keep the
                # absolute stage values only.
                conversion = None
        conversions.append(conversion)
        dropoffs.append(dropoff)
        rows.append(
            ResultRow(
                evidence_id=f"funnel:{index}:{label}",
                dimensions={
                    "stage": label,
                    "stage_index": str(index),
                    "status": status,
                },
                value=value,
            )
        )
        if conversion is not None:
            rows.append(
                ResultRow(
                    evidence_id=f"funnel:conv:{index}:{label}",
                    dimensions={
                        "stage": label,
                        "stage_index": str(index),
                        "metric": "stage_conversion",
                        "status": status,
                    },
                    value=conversion,
                )
            )
        if dropoff is not None:
            rows.append(
                ResultRow(
                    evidence_id=f"funnel:drop:{index}:{label}",
                    dimensions={
                        "stage": label,
                        "stage_index": str(index),
                        "metric": "stage_dropoff",
                        "status": status,
                    },
                    value=dropoff,
                )
            )

    overall = None
    first_idx = next((i for i, (v, s) in enumerate(zip(values, statuses)) if s == "ok" and v not in (None, 0)), None)
    last_idx = next(
        (i for i, (v, s) in reversed(list(enumerate(zip(values, statuses)))) if s == "ok" and v is not None),
        None,
    )
    if first_idx is not None and last_idx is not None and first_idx != last_idx:
        first_u, last_u = units[first_idx], units[last_idx]
        first, last = values[first_idx], values[last_idx]
        if first not in (None, 0) and last is not None and (
            first_u == last_u or (first_u is None and last_u is None)
        ):
            candidate = last / first
            if 0.0 <= candidate <= 1.0:
                overall = candidate

    # unit stays None: stage rows are heterogeneous (absolute counts/amounts plus
    # conversion fractions). Putting unit="ratio" on the derivation made every
    # absolute stage render as "N ratio". Overall conversion lives in ``value``
    # (a fraction) and is formatted via the derive op at resolve time.
    return Outcome(
        value=overall,
        unit=None,
        rows=tuple(rows),
        params=(
            ("kind", str(params.get("kind") or funnel.get("kind") or "session")),
            ("largest_leakage", largest_leak),
            ("largest_leakage_stage", largest_leak_stage),
            ("overall_conversion", overall),
            ("disclosures", list(params.get("disclosures") or funnel.get("disclosures") or [])),
            ("stage_count", len(values)),
            ("supported_stages", sum(1 for s in statuses if s == "ok")),
        ),
    )


OPS: dict[str, OpFunc] = {
    "sum": _op_sum,
    "share": _op_share,
    "delta": _op_delta,
    "pct_change": _op_pct_change,
    "per_day": _op_per_day,
    "ratio": _op_ratio,
    "recompute": _op_recompute,
    "reconcile": _op_reconcile,
    "rank": _op_rank,
    "join": _op_join,
    "funnel_stages": _op_funnel_stages,
}
