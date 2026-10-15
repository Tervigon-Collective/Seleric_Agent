"""Ordinary least squares with autocorrelation-robust (Newey-West / HAC) errors: daily series are serially
correlated, so plain OLS errors are too small (plan P-5: the in-house interrupted-time-series estimator)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from seleric_swarm.diagnosis.domain.errors import LibraryFailed, LibraryUnavailable


@dataclass(frozen=True)
class OlsHacFit:
    params: Mapping[str, float]
    stderr: Mapping[str, float]
    pvalues: Mapping[str, float]
    n_rows: int
    maxlags: int


def newey_west_lags(n_rows: int) -> int:
    """The standard automatic bandwidth, floor(4 * (n / 100) ** (2 / 9))."""
    return int(4 * (n_rows / 100) ** (2 / 9))


def ols_hac(
    y: Sequence[float],
    columns: Mapping[str, Sequence[float]],
    *,
    maxlags: int | None = None,
) -> OlsHacFit:
    """``y ~ const + columns``. Raises ``LibraryFailed`` when the design cannot be fitted."""
    try:
        import numpy as np
        import statsmodels.api as sm
    except ImportError as exc:
        raise LibraryUnavailable(f"statsmodels is required: {exc}") from exc
    n_rows = len(y)
    if any(len(v) != n_rows for v in columns.values()):
        raise LibraryFailed("every column must have as many rows as y")
    if n_rows <= len(columns) + 1:
        raise LibraryFailed(f"{n_rows} rows cannot fit {len(columns) + 1} parameters")
    lags = newey_west_lags(n_rows) if maxlags is None else maxlags
    names = ["const", *columns]
    design = np.column_stack([np.ones(n_rows), *[np.asarray(v, dtype=float) for v in columns.values()]])
    try:
        fit = sm.OLS(np.asarray(y, dtype=float), design).fit(cov_type="HAC", cov_kwds={"maxlags": lags})
    except Exception as exc:
        raise LibraryFailed(f"OLS/HAC fit failed: {type(exc).__name__}: {exc}") from exc
    return OlsHacFit(
        params=dict(zip(names, map(float, fit.params), strict=True)),
        stderr=dict(zip(names, map(float, fit.bse), strict=True)),
        pvalues=dict(zip(names, map(float, fit.pvalues), strict=True)),
        n_rows=n_rows,
        maxlags=lags,
    )
