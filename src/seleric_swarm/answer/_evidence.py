"""Resolve claim refs to values over the mission's ResultSets and Derivations."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from seleric_swarm.contracts.pipeline import Claim, Derivation, Ref, ResultRow, ResultSet

Item = ResultSet | Derivation

# Derive ops whose scalar value is a fraction (rendered as a percent).
# funnel_stages is intentionally omitted: stage rows are absolute; only the
# derivation's overall conversion (resolved with no row_key) is remapped to
# ``share`` in ``Evidence.resolve``.
FRACTION_OPS = frozenset({"share", "pct_change"})


@dataclass(frozen=True)
class Resolved:
    value: float | None
    unit: str | None
    metric_id: str | None
    op: str | None
    evidence_ids: tuple[str, ...]


class Evidence:
    """Everything a draft may cite, keyed by ref id."""

    def __init__(self, items: Iterable[Item]) -> None:
        self._items: dict[str, Item] = {i.ref.id: i for i in items}
        self._rows: dict[str, tuple[ResultRow, Item]] = {
            r.evidence_id: (r, i) for i in self._items.values() for r in i.rows
        }

    def get(self, ref: Ref) -> Item | None:
        # Direct item lookup by ref.id (ResultSet/Driver ref.id)
        item = self._items.get(ref.id)
        if item is not None:
            return item
        # Row reference: return the owning ResultSet
        if ref.kind == "evidence":
            hit = self._rows.get(ref.id)
            if hit is not None:
                return hit[1]  # owner ResultSet
        return None

    def derivations(self) -> tuple[Derivation, ...]:
        return tuple(i for i in self._items.values() if isinstance(i, Derivation))

    def result_sets(self) -> tuple[ResultSet, ...]:
        return tuple(i for i in self._items.values() if isinstance(i, ResultSet))

    def resolve(self, ref: Ref, row_key: Mapping[str, str] | None = None) -> Resolved | None:
        """One value for ``ref`` (+ ``row_key``); None when it does not name exactly one value."""
        if ref.kind == "evidence":
            # First try as a row reference (keyed by row's evidence_id)
            hit = self._rows.get(ref.id)
            if hit is not None:
                row, owner = hit
                return _resolved(owner, row.value, (row.evidence_id,))
            # Fall back to a ResultSet reference (keyed by ResultSet's ref.id)
            # with optional row_key to pick a specific row.
            found = self._items.get(ref.id)
            if found is not None and isinstance(found, ResultSet) and found.error is None:
                return self._resolve_result_set(found, row_key)
            return None
        found = self._items.get(ref.id)
        if found is None or found.error is not None:
            return None
        item = found
        key = dict(row_key or {})
        if isinstance(item, Derivation) and not key:
            ids = tuple(r.evidence_id for r in item.rows)
            # funnel_stages.scalar = overall conversion (fraction). Remap op so
            # format_value prints a percent without treating stage rows as %.
            if item.op == "funnel_stages":
                metric = item.params.get("metric_id")
                return Resolved(
                    item.value,
                    None,
                    metric if isinstance(metric, str) else None,
                    "share",
                    ids,
                )
            return _resolved(item, item.value, ids)
        rows = [r for r in item.rows if all(r.dimensions.get(k) == v for k, v in key.items())]
        if not key:
            rows = [r for r in rows if not r.dimensions] or rows
        if len(rows) != 1:
            return None
        if isinstance(item, Derivation) and item.op == "funnel_stages":
            row = rows[0]
            row_metric = row.dimensions.get("metric")
            if row_metric in {"stage_conversion", "stage_dropoff"}:
                return Resolved(row.value, None, None, "share", (row.evidence_id,))
            # Absolute stage total — no unit inheritance from the derivation.
            return Resolved(row.value, None, None, None, (row.evidence_id,))
        return _resolved(item, rows[0].value, (rows[0].evidence_id,))

    def _resolve_result_set(
        self, rs: ResultSet, row_key: Mapping[str, str] | None
    ) -> Resolved | None:
        key = dict(row_key or {})
        rows = [r for r in rs.rows if all(r.dimensions.get(k) == v for k, v in key.items())]
        if not key:
            rows = [r for r in rows if not r.dimensions] or rows
        if len(rows) != 1:
            return None
        return _resolved(rs, rows[0].value, (rows[0].evidence_id,))


def _resolved(item: Item, value: float | None, ids: tuple[str, ...]) -> Resolved:
    if isinstance(item, ResultSet):
        return Resolved(value, item.unit, item.metric_id, None, ids)
    metric = item.params.get("metric_id")
    return Resolved(value, item.unit, metric if isinstance(metric, str) else None, item.op, ids)


def resolve_claim(evidence: Evidence, claim: Claim) -> Resolved | None:
    return evidence.resolve(claim.ref, claim.row_key)
