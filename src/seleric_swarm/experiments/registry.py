"""YAML-backed experiment registry (Profile C, Sprint 4).

Mirrors the pattern ``config/model_registry.yaml`` established in Sprint 3:
version-controlled, reviewable in a diff, no migration for a table nothing
writes yet. A Postgres table is the better home *once experiments are created
by the product*; today nothing creates them, and an empty table plus a
repository class would be infrastructure ahead of need.

The registry is the record of what was actually run. ``evaluate_experiment``
refuses on an unknown id rather than scoring whatever evidence it was handed —
an experiment nobody registered is not an experiment, and measuring "lift"
against an undeclared control is how a dashboard ends up asserting a result
nobody designed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from seleric_swarm.paths import repo_root

_CANDIDATE_PATHS = (
    "config/experiment_registry.yaml",
    "config/experiment_registry.example.yaml",
)


@dataclass(frozen=True)
class Variant:
    name: str
    #: Dimension value identifying this variant's evidence, e.g. the value of
    #: `experiment_variant` stamped on drilldown rows.
    label: str
    is_control: bool = False


@dataclass(frozen=True)
class Experiment:
    experiment_id: str
    name: str
    status: str  # draft | running | complete | abandoned
    metric_id: str
    variants: list[Variant] = field(default_factory=list)
    hypothesis: str = ""
    started_at: str | None = None
    ended_at: str | None = None
    baseline_rate: float | None = None
    mde: float | None = None
    notes: str = ""

    @property
    def control(self) -> Variant | None:
        return next((v for v in self.variants if v.is_control), None)

    @property
    def treatments(self) -> list[Variant]:
        return [v for v in self.variants if not v.is_control]


class ExperimentRegistry:
    def __init__(self, experiments: dict[str, Experiment] | None = None) -> None:
        self._by_id = dict(experiments or {})

    def get(self, experiment_id: str) -> Experiment | None:
        return self._by_id.get(experiment_id)

    def all(self) -> list[Experiment]:
        return sorted(self._by_id.values(), key=lambda e: (e.started_at or "", e.experiment_id))

    def __len__(self) -> int:
        return len(self._by_id)


def _resolve(path: str | Path | None) -> Path | None:
    if path is not None:
        candidate = Path(path)
        return candidate if candidate.exists() else None
    root = repo_root()
    for name in _CANDIDATE_PATHS:
        candidate = root / name
        if candidate.exists():
            return candidate
    return None


def registry_from_yaml(path: str | Path | None = None) -> ExperimentRegistry:
    """Load the registry. A missing file yields an empty registry, not an error —
    "no experiments have been run" is a legitimate state and the toolset
    reports it as such."""
    resolved = _resolve(path)
    if resolved is None:
        return ExperimentRegistry()

    data: dict[str, Any] = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
    out: dict[str, Experiment] = {}
    for item in data.get("experiments", []) or []:
        variants = [
            Variant(
                name=str(v["name"]),
                label=str(v.get("label", v["name"])),
                is_control=bool(v.get("control", False)),
            )
            for v in (item.get("variants") or [])
        ]
        experiment = Experiment(
            experiment_id=str(item["id"]),
            name=str(item.get("name", item["id"])),
            status=str(item.get("status", "draft")),
            metric_id=str(item.get("metric_id", "")),
            variants=variants,
            hypothesis=str(item.get("hypothesis", "")),
            started_at=item.get("started_at"),
            ended_at=item.get("ended_at"),
            baseline_rate=item.get("baseline_rate"),
            mde=item.get("mde"),
            notes=str(item.get("notes", "")),
        )
        out[experiment.experiment_id] = experiment
    return ExperimentRegistry(out)
