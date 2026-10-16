"""Huber robust regression (statsmodels RLM) behind the engine's calendar fit."""

from __future__ import annotations

from typing import Any

from seleric_swarm.diagnosis.domain.errors import LibraryFailed, LibraryUnavailable


def huber_fit(design: Any, response: Any) -> tuple[list[float], float]:
    """``(parameters, scale)`` of ``response ~ design`` by Huber M-estimation."""
    try:
        import statsmodels.api as sm
    except ImportError as exc:
        raise LibraryUnavailable("statsmodels is not installed") from exc
    try:
        res = sm.RLM(response, design, M=sm.robust.norms.HuberT()).fit()
        return [float(p) for p in res.params], float(res.scale)
    except Exception as exc:
        raise LibraryFailed(f"robust fit failed: {exc}") from exc
