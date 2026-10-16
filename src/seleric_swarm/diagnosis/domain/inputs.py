"""What the analysis stages are given: the fetched series and segments, the lineage facts and the plan's choices (which
metrics are candidate drivers, which days are compared). ``to_dict``/``from_dict`` give it a JSON form, which is how a
fixture or a run record carries an engine input; the round trip is exact."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, cast

from seleric_swarm.diagnosis.domain.lineage import MetricMeta
from seleric_swarm.diagnosis.domain.numeric import Series
from seleric_swarm.diagnosis.domain.report import Direction


@dataclass
class DiagnosisInput:
    outcome: str
    event_days: list[date]
    series: dict[str, Series]
    lineage: dict[str, MetricMeta]
    # metric -> dimension -> segment -> day -> value
    segments: dict[str, dict[str, dict[str, Series]]] = field(default_factory=dict)
    candidate_drivers: list[str] = field(default_factory=list)
    partial_days: set[date] = field(default_factory=set)
    claimed_direction: Direction | None = None
    denominators: dict[str, str] = field(default_factory=dict)  # rate metric -> weight metric
    numerators: dict[str, str] = field(default_factory=dict)  # ratio metric -> its verified numerator
    # Period comparison: the window the event is compared with, day for day (same
    # length as ``event_days``). Empty = compare with the metric's usual level.
    baseline_days: list[date] = field(default_factory=list)
    scope_tokens: frozenset[str] = frozenset()  # grain tokens naming the tenant scope, not a unit
    # Same-unit additive metrics that may sum to the outcome (verified on the data).
    bridge_candidates: list[str] = field(default_factory=list)
    # Candidate driver -> the metrics its effect must pass through to reach the outcome, nearest the driver first (from the
    # reviewed graph; only those with a series). A driver whose path stayed quiet at the event cannot have caused it.
    driver_paths: dict[str, list[str]] = field(default_factory=dict)
    # True when the reviewed graph chose ``candidate_drivers``: the metrics that count the same events as the outcome are
    # already set aside by its edges, so WHY_EVENT does not screen for them again with data heuristics.
    candidates_from_graph: bool = False
    # (metric, peer, expected): pairs the reviewed graph says count the same events in two systems, both with a series.
    measurement_pairs: list[tuple[str, str, str]] = field(default_factory=list)


_NON_FINITE = {"nan": float("nan"), "inf": float("inf"), "-inf": float("-inf")}


def _number(v: float) -> float | str:
    if math.isfinite(v):
        return v
    return "nan" if math.isnan(v) else ("inf" if v > 0 else "-inf")


def _back(v: float | str) -> float:
    return _NON_FINITE[v] if isinstance(v, str) else float(v)


def _series(s: Series) -> dict[str, float | str]:
    return {d.isoformat(): _number(v) for d, v in sorted(s.items())}


def _series_back(raw: Mapping[str, float | str]) -> Series:
    return {date.fromisoformat(d): _back(v) for d, v in raw.items()}


def input_to_dict(inp: DiagnosisInput) -> dict[str, Any]:
    return {
        "outcome": inp.outcome,
        "event_days": [d.isoformat() for d in inp.event_days],
        "series": {m: _series(s) for m, s in sorted(inp.series.items())},
        "lineage": {m: asdict(meta) | {"depends_on": list(meta.depends_on)} for m, meta in sorted(inp.lineage.items())},
        "segments": {
            m: {dim: {seg: _series(s) for seg, s in sorted(segs.items())} for dim, segs in sorted(dims.items())}
            for m, dims in sorted(inp.segments.items())
        },
        "candidate_drivers": list(inp.candidate_drivers),
        "partial_days": sorted(d.isoformat() for d in inp.partial_days),
        "claimed_direction": inp.claimed_direction,
        "denominators": dict(sorted(inp.denominators.items())),
        "numerators": dict(sorted(inp.numerators.items())),
        "baseline_days": [d.isoformat() for d in inp.baseline_days],
        "scope_tokens": sorted(inp.scope_tokens),
        "bridge_candidates": list(inp.bridge_candidates),
        "driver_paths": {c: list(p) for c, p in sorted(inp.driver_paths.items())},
        "candidates_from_graph": inp.candidates_from_graph,
        "measurement_pairs": [list(p) for p in inp.measurement_pairs],
    }


def input_from_dict(raw: Mapping[str, Any]) -> DiagnosisInput:
    return DiagnosisInput(
        outcome=str(raw["outcome"]),
        event_days=[date.fromisoformat(d) for d in raw["event_days"]],
        series={m: _series_back(s) for m, s in raw["series"].items()},
        lineage={m: MetricMeta(**(dict(meta) | {"depends_on": tuple(meta["depends_on"])})) for m, meta in raw["lineage"].items()},
        segments={
            m: {dim: {seg: _series_back(s) for seg, s in segs.items()} for dim, segs in dims.items()}
            for m, dims in raw["segments"].items()
        },
        candidate_drivers=list(raw["candidate_drivers"]),
        partial_days={date.fromisoformat(d) for d in raw["partial_days"]},
        claimed_direction=cast(Direction | None, raw["claimed_direction"]),
        denominators=dict(raw["denominators"]),
        numerators=dict(raw["numerators"]),
        baseline_days=[date.fromisoformat(d) for d in raw["baseline_days"]],
        scope_tokens=frozenset(raw["scope_tokens"]),
        bridge_candidates=list(raw["bridge_candidates"]),
        driver_paths={c: list(p) for c, p in (raw.get("driver_paths") or {}).items()},
        candidates_from_graph=bool(raw.get("candidates_from_graph", False)),
        measurement_pairs=[(str(m), str(p), str(e)) for m, p, e in raw.get("measurement_pairs") or []],
    )
