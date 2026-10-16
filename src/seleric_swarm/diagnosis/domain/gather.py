"""The plan a diagnosis gathers its data by, frozen (plan P2.6, D-46).

Everything the tool reads from the catalogue to decide what to fetch (which series, in which role, which segment
dimensions, which drivers) is written down here once, so the gather step that follows is a function of the plan and the
data source alone. A run record that carries the plan can therefore re-derive the engine input from the recorded
series, and a bug in the gather shows up in a replay instead of hiding in the tool.

Phase 3 changes how ``drivers`` is chosen; it does not change anything that consumes the plan."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, cast

from seleric_swarm.diagnosis.domain.inputs import DiagnosisInput
from seleric_swarm.diagnosis.domain.lineage import MetricMeta
from seleric_swarm.diagnosis.domain.report import Direction
from seleric_swarm.diagnosis.domain.subgraph import Subgraph


@dataclass(frozen=True)
class GatherPlan:
    outcome: str
    start: date  # first day fetched (history before the event)
    end: date  # last day fetched (the last event day)
    scope: Mapping[str, Any]  # filters the outcome is scoped by: dimension -> value or list of values
    event_days: tuple[date, ...]
    partial_days: frozenset[date]
    baseline_days: tuple[date, ...]
    claimed_direction: Direction | None
    lineage: Mapping[str, MetricMeta]
    series_ids: tuple[str, ...]  # every series fetched, outcome first, no repeats
    core: frozenset[str]  # the series the answer needs whatever their role (a dropped filter cannot remove these)
    change_log: frozenset[str]  # logged edits on the scope's entities
    ladder_ends: frozenset[str]  # the funnel between a ratio's denominator and numerator
    bridge_pool: tuple[str, ...]  # same-unit additive metrics that may sum to the outcome
    drivers: tuple[str, ...]  # candidate causes, in order, before the ones with no data are dropped
    dims: tuple[str, ...]  # dimensions the outcome is split by
    chain_dims: tuple[tuple[str, str, tuple[str, ...]], ...]  # (rate, base, dimensions) for each proposed rate chain
    scope_tokens: frozenset[str]
    max_segment_rows: int = 6000  # a segment answer bigger than this is a cardinality blow-up, not a split
    subgraph: Subgraph | None = None  # what the reviewed graph says about this outcome (P3.1); None when it was not built

    @property
    def outcome_additive(self) -> bool:
        return self.lineage[self.outcome].additive

    def to_dict(self) -> dict[str, Any]:
        out = self._core_dict()
        if self.subgraph is not None:  # absent for a plan made before the subgraph existed, so old records read unchanged
            out["subgraph"] = self.subgraph.to_dict()
        return out

    def _core_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome, "start": self.start.isoformat(), "end": self.end.isoformat(),
            "scope": {k: (list(v) if isinstance(v, (list, tuple)) else v) for k, v in sorted(self.scope.items())},
            "event_days": [d.isoformat() for d in self.event_days],
            "partial_days": sorted(d.isoformat() for d in self.partial_days),
            "baseline_days": [d.isoformat() for d in self.baseline_days],
            "claimed_direction": self.claimed_direction,
            "lineage": {m: asdict(meta) | {"depends_on": list(meta.depends_on)} for m, meta in sorted(self.lineage.items())},
            "series_ids": list(self.series_ids), "core": sorted(self.core), "change_log": sorted(self.change_log),
            "ladder_ends": sorted(self.ladder_ends), "bridge_pool": list(self.bridge_pool),
            "drivers": list(self.drivers), "dims": list(self.dims),
            "chain_dims": [[rate, base, list(dims)] for rate, base, dims in self.chain_dims],
            "scope_tokens": sorted(self.scope_tokens), "max_segment_rows": self.max_segment_rows,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> GatherPlan:
        return cls(
            outcome=str(raw["outcome"]), start=date.fromisoformat(raw["start"]), end=date.fromisoformat(raw["end"]),
            scope=dict(raw["scope"]),
            event_days=tuple(date.fromisoformat(d) for d in raw["event_days"]),
            partial_days=frozenset(date.fromisoformat(d) for d in raw["partial_days"]),
            baseline_days=tuple(date.fromisoformat(d) for d in raw["baseline_days"]),
            claimed_direction=cast(Direction | None, raw["claimed_direction"]),
            lineage={m: MetricMeta(**(dict(meta) | {"depends_on": tuple(meta["depends_on"])})) for m, meta in raw["lineage"].items()},
            series_ids=tuple(raw["series_ids"]), core=frozenset(raw["core"]), change_log=frozenset(raw["change_log"]),
            ladder_ends=frozenset(raw["ladder_ends"]), bridge_pool=tuple(raw["bridge_pool"]),
            drivers=tuple(raw["drivers"]), dims=tuple(raw["dims"]),
            chain_dims=tuple((str(r), str(b), tuple(d)) for r, b, d in raw["chain_dims"]),
            scope_tokens=frozenset(raw["scope_tokens"]), max_segment_rows=int(raw["max_segment_rows"]),
            subgraph=Subgraph.from_dict(raw["subgraph"]) if raw.get("subgraph") else None,
        )


@dataclass
class Gathered:
    """What gathering produced: the engine input, the data-quality notes, and for each series the query it came from and
    the unit the source reported it in (the evidence rows cite them). ``failure`` is set when the outcome itself could
    not be read; ``input`` is then None."""

    input: DiagnosisInput | None
    notes: list[str] = field(default_factory=list)
    series_args: dict[str, dict[str, Any]] = field(default_factory=dict)
    segment_args: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    units: dict[str, str] = field(default_factory=dict)
    history_days: list[date] = field(default_factory=list)
    weight: str | None = None
    numerator: str | None = None
    failure: str | None = None  # "fetch_failed" | "no_data"
    failure_detail: str = ""
