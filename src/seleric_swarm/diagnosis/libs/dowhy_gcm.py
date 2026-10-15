"""Attribution of a change in a metric's distribution to the mechanisms that changed, with DoWhy's graphical causal
models (``dowhy.gcm.distribution_change``; plan WHY_PERIOD).

``gcm`` can only be seeded globally. The call below seeds it from the caller's run-derived seed and puts the global
numpy and ``random`` state back afterwards, so the rest of the process is undisturbed and the result is reproducible. Its worker-pool and progress-bar
settings are global too and are restored the same way."""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence

from seleric_swarm.diagnosis.domain.errors import LibraryFailed, LibraryUnavailable


def attribute_mean_change(
    old: Mapping[str, Sequence[float]],
    new: Mapping[str, Sequence[float]],
    edges: Sequence[tuple[str, str]],
    target: str,
    *,
    seed: int,
    num_samples: int = 2000,
) -> dict[str, float]:
    """How much of the change in the mean of ``target`` each node's mechanism explains, linear mechanisms."""
    try:
        import networkx as nx
        import numpy as np
        import pandas as pd
        from dowhy import gcm
    except ImportError as exc:
        raise LibraryUnavailable(f"dowhy (gcm) is required: {exc}") from exc
    graph = nx.DiGraph(list(edges))
    if target not in graph:
        raise LibraryFailed(f"target {target!r} is not in the graph")
    columns = list(graph.nodes)
    if not all(c in old and c in new for c in columns):
        raise LibraryFailed(f"old and new data must both hold {sorted(columns)}")
    old_frame = pd.DataFrame({c: list(old[c]) for c in columns})
    new_frame = pd.DataFrame({c: list(new[c]) for c in columns})
    model = gcm.StructuralCausalModel(graph)
    for node in columns:
        if graph.in_degree(node) == 0:
            model.set_causal_mechanism(node, gcm.EmpiricalDistribution())
        else:
            model.set_causal_mechanism(node, gcm.AdditiveNoiseModel(gcm.ml.create_linear_regressor()))
    numpy_state, python_state = np.random.get_state(), random.getstate()
    jobs_before, bars_before = gcm.config.default_n_jobs, gcm.config.show_progress_bars
    try:
        gcm.util.general.set_random_seed(seed)
        gcm.config.disable_progress_bars()
        # One process: the default spawns a worker pool for its independence tests, ~10 s of start-up per call
        # on this host for a computation that takes 0.1 s, and a pool inside the API container.
        gcm.config.set_default_n_jobs(1)
        result = gcm.distribution_change(
            model, old_frame, new_frame, target, num_samples=num_samples,
            difference_estimation_func=lambda before, after: float(np.mean(after) - np.mean(before)),
        )
    except Exception as exc:
        raise LibraryFailed(f"gcm.distribution_change failed: {type(exc).__name__}: {exc}") from exc
    finally:
        np.random.set_state(numpy_state)
        random.setstate(python_state)
        gcm.config.set_default_n_jobs(jobs_before)
        gcm.config.show_progress_bars = bars_before
    return {str(node): float(score) for node, score in result.items()}
