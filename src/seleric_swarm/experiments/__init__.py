"""Experiment design and evaluation (Profile C, Sprint 4).

Registry is YAML-backed (``config/experiment_registry.yaml``), mirroring the
model-registry pattern from Sprint 3. Statistics are real power analysis via
``statsmodels``. See the two submodules for the reasoning behind each.
"""

from seleric_swarm.experiments.registry import (
    Experiment,
    ExperimentRegistry,
    Variant,
    registry_from_yaml,
)
from seleric_swarm.experiments.stats import (
    Lift,
    SampleSizeResult,
    SampleSizeUnavailable,
    estimate_sample_size,
    lift,
)

__all__ = [
    "Experiment",
    "ExperimentRegistry",
    "Lift",
    "SampleSizeResult",
    "SampleSizeUnavailable",
    "Variant",
    "estimate_sample_size",
    "lift",
    "registry_from_yaml",
]
