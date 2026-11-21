"""User-written rules: a list of (field, op, value) conditions joined by all/any. Interpreted, never evaluated as code.

A value is a literal or a reference ``{"ref": "guardrail.allowable_cpa", "multiply": 1.5}`` /
``{"ref": "baseline.ctr_link.p10"}``, so a rule can say "3-day CPA above 1.5x allowable" without hardcoding a number.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from seleric_swarm.services.live_monitor.core.model import Entity, FieldDef

if TYPE_CHECKING:
    from seleric_swarm.services.live_monitor.core.context import RuleContext

OPS_BY_TYPE: dict[str, tuple[str, ...]] = {
    "number": ("gt", "gte", "lt", "lte", "eq", "ne", "exists", "missing"),
    "string": ("eq", "ne", "in", "not_in", "contains", "not_contains", "exists", "missing"),
    "string_list": ("contains", "not_contains", "exists", "missing"),
    "bool": ("eq", "ne", "exists", "missing"),
}
ALL_OPS = tuple(sorted({op for ops in OPS_BY_TYPE.values() for op in ops}))
NO_VALUE_OPS = {"exists", "missing"}
GUARDRAIL_REFS = ("be_roas_platform", "target_roas", "allowable_cpa")
BASELINE_METRICS = ("spend", "impressions", "cpm", "ctr_link", "cpa", "roas", "aov", "lpv_per_click", "cvr_lpv",
                    "purchases", "frequency")
BASELINE_STATS = ("p10", "p50", "p90", "mean")
MAX_CONDITIONS = 10
MAX_STRING = 200
MAX_LIST = 50


class ConditionError(ValueError):
    """A custom rule is not acceptable. The message is safe to show the user."""


def _check_ref(ref: Any, where: str) -> dict[str, Any]:
    if not isinstance(ref, dict) or set(ref) - {"ref", "multiply"} or not isinstance(ref.get("ref"), str):
        raise ConditionError(f"{where}: a reference is {{ref, multiply}}")
    parts = ref["ref"].split(".")
    if parts[0] == "guardrail" and len(parts) == 2 and parts[1] in GUARDRAIL_REFS or parts[0] == "baseline" and len(parts) == 3 and parts[1] in BASELINE_METRICS and parts[2] in BASELINE_STATS:
        pass
    else:
        raise ConditionError(f"{where}: ref must be guardrail.<{'|'.join(GUARDRAIL_REFS)}> or "
                             f"baseline.<metric>.<{'|'.join(BASELINE_STATS)}>")
    mul = ref.get("multiply", 1.0)
    if isinstance(mul, bool) or not isinstance(mul, int | float) or not math.isfinite(mul) or not 0 < mul <= 100:
        raise ConditionError(f"{where}: multiply must be a number above 0 and at most 100")
    return {"ref": ref["ref"], "multiply": float(mul)}


def validate_conditions(conditions: Any, level: str, catalogue: list[FieldDef]) -> list[dict[str, Any]]:
    if not isinstance(conditions, list) or not conditions:
        raise ConditionError("conditions must be a non-empty list")
    if len(conditions) > MAX_CONDITIONS:
        raise ConditionError(f"at most {MAX_CONDITIONS} conditions per rule")
    fields = {f.path: f for f in catalogue if level in f.levels}
    out = []
    for i, c in enumerate(conditions):
        where = f"conditions[{i}]"
        if not isinstance(c, dict) or set(c) - {"field", "op", "value"}:
            raise ConditionError(f"{where}: allowed keys are field, op, value")
        fd = fields.get(c.get("field"))
        if fd is None:
            raise ConditionError(f"{where}: {c.get('field')!r} is not a field available at {level} level")
        op = c.get("op")
        if op not in OPS_BY_TYPE[fd.type]:
            raise ConditionError(f"{where}: {fd.path} ({fd.type}) allows {', '.join(OPS_BY_TYPE[fd.type])}")
        item: dict[str, Any] = {"field": fd.path, "op": op}
        if op not in NO_VALUE_OPS:
            item["value"] = _check_value(c.get("value"), fd, op, where)
        out.append(item)
    return out


def _check_value(v: Any, fd: FieldDef, op: str, where: str) -> Any:
    if fd.type == "number":
        if isinstance(v, dict):
            return _check_ref(v, where)
        if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v):
            raise ConditionError(f"{where}: {fd.path} needs a number or a reference")
        return float(v)
    if fd.type == "bool":
        if not isinstance(v, bool):
            raise ConditionError(f"{where}: {fd.path} needs true or false")
        return v
    if op in ("in", "not_in"):
        if not isinstance(v, list) or not v or len(v) > MAX_LIST or not all(isinstance(x, str) and len(x) <= MAX_STRING for x in v):
            raise ConditionError(f"{where}: {op} needs a list of up to {MAX_LIST} texts")
        return v
    if not isinstance(v, str) or len(v) > MAX_STRING:
        raise ConditionError(f"{where}: {fd.path} needs a text of at most {MAX_STRING} characters")
    return v


def resolve_value(v: Any, e: Entity, ctx: RuleContext) -> Any:
    if not isinstance(v, dict):
        return v
    head, *rest = v["ref"].split(".")
    if head == "guardrail":
        base = getattr(ctx.guard(e), rest[0])
    else:
        band = ctx.band(e, rest[0])
        base = None if band is None else getattr(band, rest[1])
    return None if base is None else float(base) * float(v.get("multiply", 1.0))


def _test(op: str, actual: Any, expected: Any) -> bool:
    if op == "exists":
        return actual not in (None, "", [])
    if op == "missing":
        return actual in (None, "", [])
    if actual is None or expected is None:
        return False
    if op == "eq":
        return actual == expected
    if op == "ne":
        return actual != expected
    if op == "in":
        return actual in expected
    if op == "not_in":
        return actual not in expected
    if op == "contains":
        return expected in actual if isinstance(actual, list | str) else False
    if op == "not_contains":
        return expected not in actual if isinstance(actual, list | str) else False
    try:
        a, b = float(actual), float(expected)
    except (TypeError, ValueError):
        return False
    return {"gt": a > b, "gte": a >= b, "lt": a < b, "lte": a <= b}[op]


def evaluate(conditions: list[dict[str, Any]], match: str, e: Entity, ctx: RuleContext) -> tuple[bool, dict[str, Any]]:
    results, evidence = [], {}
    for c in conditions:
        actual = e.get(c["field"])
        expected = resolve_value(c.get("value"), e, ctx)
        ok = _test(c["op"], actual, expected)
        results.append(ok)
        evidence[c["field"]] = {"observed": actual, "op": c["op"], "value": expected, "met": ok}
    return (all(results) if match == "all" else any(results)), evidence
