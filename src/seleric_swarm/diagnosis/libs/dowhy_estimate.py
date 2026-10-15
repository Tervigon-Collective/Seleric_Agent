"""A back-door effect estimate through DoWhy (plan P1.13): the library call the current engine makes, behind a
typed function. Time-series structure (calendar, lags) is the caller's: it passes the adjusters as columns."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from seleric_swarm.diagnosis.domain.errors import LibraryFailed, LibraryUnavailable


@dataclass(frozen=True)
class BackdoorEstimate:
    effect: float
    backdoor_set: tuple[str, ...]
    n_rows: int


def estimate_backdoor_effect(
    columns: Mapping[str, Sequence[float]],
    *,
    treatment: str,
    outcome: str,
    adjusters: Sequence[str],
) -> BackdoorEstimate:
    """Effect of ``treatment`` on ``outcome`` adjusting for ``adjusters`` (each a pre-treatment cause of both)."""
    try:
        import networkx as nx
        import pandas as pd
        from dowhy import CausalModel
    except ImportError as exc:
        raise LibraryUnavailable(f"dowhy, pandas and networkx are required: {exc}") from exc
    needed = [treatment, outcome, *adjusters]
    missing = [c for c in needed if c not in columns]
    if missing:
        raise LibraryFailed(f"columns missing: {missing}")
    frame = pd.DataFrame({c: list(columns[c]) for c in needed}).dropna()
    edges = [(a, treatment) for a in adjusters] + [(a, outcome) for a in adjusters] + [(treatment, outcome)]
    try:
        model = CausalModel(data=frame, treatment=treatment, outcome=outcome, graph=nx.DiGraph(edges))
        estimand = model.identify_effect()
        backdoor = tuple(sorted(estimand.get_backdoor_variables() or []))
        if adjusters and not backdoor:
            raise LibraryFailed("DoWhy found no back-door adjustment set for this graph")
        estimate = model.estimate_effect(estimand, method_name="backdoor.linear_regression")
    except LibraryFailed:
        raise
    except Exception as exc:
        raise LibraryFailed(f"DoWhy estimation failed: {type(exc).__name__}: {exc}") from exc
    return BackdoorEstimate(effect=float(estimate.value), backdoor_set=backdoor, n_rows=len(frame))
