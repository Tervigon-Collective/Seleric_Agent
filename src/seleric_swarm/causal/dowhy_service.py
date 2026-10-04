"""DoWhy boundary.

Do not let agents call arbitrary causal estimators without a registered causal
question / graph. ``DoWhyService.estimate`` builds a ``CausalModel``, identifies
the estimand, estimates the effect and runs the configured refuters. It is
import-lazy and defensive: any failure raises ``DoWhyUnavailable`` so callers
can fall back to a metadata-only audit rather than fake a causal result.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Literal

_log = logging.getLogger(__name__)

_DEFAULT_REFUTERS = ("placebo_treatment_refuter", "random_common_cause", "data_subset_refuter")


class DoWhyUnavailable(RuntimeError):
    """DoWhy is not installed or the estimation pipeline failed."""


@dataclass
class CausalRequest:
    treatment: str
    outcome: str
    common_causes: list[str]
    graph_id: str = ""
    estimator: str = "backdoor.linear_regression"
    refuters: list[str] = field(default_factory=lambda: list(_DEFAULT_REFUTERS))


@dataclass
class RefuterOutcome:
    name: str
    estimated_effect: float
    new_effect: float
    passed: bool
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class DoWhyEstimate:
    treatment: str
    outcome: str
    effect: float
    estimator: str
    common_causes: list[str]
    refutations: list[RefuterOutcome]
    n_rows: int
    confidence_interval: list[float] = field(default_factory=list)
    dropped_collinear_common_causes: list[str] = field(default_factory=list)

    @property
    def refutations_passed(self) -> int:
        return sum(1 for r in self.refutations if r.passed)

    def as_dict(self) -> dict[str, Any]:
        return {
            "treatment": self.treatment,
            "outcome": self.outcome,
            "effect": self.effect,
            "estimator": self.estimator,
            "common_causes": list(self.common_causes),
            "n_rows": self.n_rows,
            "confidence_interval": list(self.confidence_interval),
            "dropped_collinear_common_causes": list(self.dropped_collinear_common_causes),
            "refutations": [
                {"name": r.name, "passed": r.passed, "estimated_effect": r.estimated_effect,
                 "new_effect": r.new_effect, **r.detail}
                for r in self.refutations
            ],
        }


def _normalize_confidence_interval(raw: Any) -> list[float]:
    """Normalize DoWhy CI output to ``[lo, hi]`` or ``[]`` if unavailable."""
    if raw is None:
        return []
    try:
        if hasattr(raw, "tolist"):
            raw = raw.tolist()
        if isinstance(raw, dict):
            for key in ("default", "bootstrap", "ci", "interval"):
                if key in raw:
                    return _normalize_confidence_interval(raw[key])
            vals = list(raw.values())
            return _normalize_confidence_interval(vals[0] if len(vals) == 1 else vals)
        if isinstance(raw, (list, tuple)):
            if len(raw) == 2 and all(isinstance(x, (int, float)) for x in raw):
                lo, hi = float(raw[0]), float(raw[1])
                return [lo, hi] if lo <= hi else [hi, lo]
            # Nested [[lo, hi], ...] from some DoWhy versions
            if len(raw) == 1:
                return _normalize_confidence_interval(raw[0])
            if len(raw) >= 2 and isinstance(raw[0], (list, tuple)):
                return _normalize_confidence_interval(raw[0])
    except (TypeError, ValueError):
        return []
    return []


_COLLINEARITY_THRESHOLD = 0.98


def _drop_collinear_common_causes(
    data: Any, treatment: str, common_causes: list[str]
) -> tuple[list[str], list[str]]:
    """Drop common causes that are near-perfectly correlated with the treatment.

    Ad-platform metrics routinely move together (clicks/impressions/spend all
    spike from the same tracking or scale change), which makes the design
    matrix for ``backdoor.linear_regression`` singular or numerically unstable
    and used to blow the whole estimate up into a ``DoWhyUnavailable`` ->
    template-fallback (a real, sufficiently-sized observation frame producing
    no real causal estimate at all). Dropping the offending covariate keeps
    the estimate real; the caller is told what was dropped so it isn't silent.
    """
    if not common_causes:
        return [], []
    kept: list[str] = []
    dropped: list[str] = []
    treatment_col = data[treatment]
    for cause in common_causes:
        try:
            corr = float(treatment_col.corr(data[cause]))
        except Exception:
            _log.warning("collinearity_check_failed", exc_info=True, extra={"cause": cause})
            kept.append(cause)
            continue
        if not math.isnan(corr) and abs(corr) >= _COLLINEARITY_THRESHOLD:
            dropped.append(cause)
        else:
            kept.append(cause)
    return kept, dropped


class DoWhyService:
    """Thin wrapper over ``dowhy.CausalModel``. Sync internals; call from a thread
    if you need to keep an event loop free (estimation is CPU-bound)."""

    def __init__(self, *, relative_tolerance: float = 0.25) -> None:
        self._tol = relative_tolerance

    def estimate(self, request: CausalRequest, data: Any) -> DoWhyEstimate:
        try:
            import pandas as pd
            from dowhy import CausalModel
        except Exception as exc:  # pragma: no cover - env without dowhy
            raise DoWhyUnavailable(f"dowhy/pandas import failed: {exc}") from exc

        if not isinstance(data, pd.DataFrame):
            raise DoWhyUnavailable("estimate() requires a pandas DataFrame of observations")

        needed = [request.treatment, request.outcome, *request.common_causes]
        missing = [c for c in needed if c not in data.columns]
        if missing:
            raise DoWhyUnavailable(f"dataset missing columns: {missing}")

        common_causes, dropped = _drop_collinear_common_causes(data, request.treatment, request.common_causes)
        if dropped:
            _log.warning(
                "dowhy: dropped near-collinear common causes %s (|corr| with treatment %s > %.2f) "
                "to avoid a singular/unstable regression",
                dropped,
                request.treatment,
                _COLLINEARITY_THRESHOLD,
            )

        try:
            model = CausalModel(
                data=data,
                treatment=request.treatment,
                outcome=request.outcome,
                common_causes=common_causes,
            )
            identified = model.identify_effect(proceed_when_unidentifiable=True)
            estimate = model.estimate_effect(identified, method_name=request.estimator)
            base_effect = float(estimate.value)
            ci = _extract_confidence_interval(estimate)

            refutations: list[RefuterOutcome] = []
            for name in request.refuters:
                refutations.append(self._refute(model, identified, estimate, base_effect, name))
        except DoWhyUnavailable:
            raise
        except Exception as exc:
            # Surface *why* with full context and a traceback -- a bare
            # DoWhyUnavailable(str(exc)) upstream loses both, which made every
            # real DoWhy failure (singular design matrix from collinear common
            # causes, near-zero-variance treatment column, etc.) indistinguishable
            # from "dowhy not installed" in the logs.
            _log.warning(
                "dowhy estimation failed: treatment=%s outcome=%s common_causes=%s n_rows=%s estimator=%s",
                request.treatment,
                request.outcome,
                request.common_causes,
                len(data),
                request.estimator,
                exc_info=True,
            )
            raise DoWhyUnavailable(f"dowhy estimation failed: {exc}") from exc

        return DoWhyEstimate(
            treatment=request.treatment,
            outcome=request.outcome,
            effect=base_effect,
            estimator=request.estimator,
            common_causes=common_causes,
            refutations=refutations,
            n_rows=len(data),
            confidence_interval=ci,
            dropped_collinear_common_causes=dropped,
        )

    # -- refuter interpretation ------------------------------------------
    def _refute(self, model, identified, estimate, base_effect: float, name: str) -> RefuterOutcome:
        kwargs: dict[str, Any] = {}
        if name == "placebo_treatment_refuter":
            kwargs = {"placebo_type": "permute"}
        elif name == "data_subset_refuter":
            kwargs = {"subset_fraction": 0.8}
        try:
            result = model.refute_estimate(identified, estimate, method_name=name, **kwargs)
            new_effect = float(getattr(result, "new_effect", 0.0))
        except Exception as exc:  # a refuter that errors counts as not-passed, not fatal
            return RefuterOutcome(name, base_effect, float("nan"), passed=False, detail={"error": str(exc)})

        denom = abs(base_effect) if abs(base_effect) > 1e-9 else 1.0
        if name == "placebo_treatment_refuter":
            # a valid effect should collapse toward zero under a placebo treatment
            passed = abs(new_effect) <= 0.5 * denom
        else:
            # effect should be stable under a random common cause / data subset
            passed = abs(new_effect - base_effect) / denom <= self._tol
        return RefuterOutcome(name, base_effect, new_effect, passed=passed)


def _extract_confidence_interval(estimate: Any) -> list[float]:
    """Best-effort CI from a DoWhy ``CausalEstimate``; never raises."""
    try:
        getter = getattr(estimate, "get_confidence_intervals", None)
        if callable(getter):
            return _normalize_confidence_interval(getter())
    except Exception:  # noqa: S110 - DoWhy versions expose CI through different optional APIs
        pass
    for attr in ("confidence_intervals", "confidence_interval"):
        try:
            raw = getattr(estimate, attr, None)
            if raw is not None:
                return _normalize_confidence_interval(raw)
        except Exception:  # noqa: S112 - malformed optional CI attributes are tried in fallback order
            continue
    return []


# ---------------------------------------------------------------------------
# Time-series effect estimation with an explicit DAG (diagnosis engine).
#
# The legacy ``DoWhyService.estimate`` above adjusts for *every* other metric
# in the frame with ``proceed_when_unidentifiable=True``: with daily business
# series that turns mediators (spend -> sessions -> sales) and colliders into
# "confounders" and lets weekly seasonality, shared trends and reverse feedback
# masquerade as causal effects. ``estimate_time_series_effect`` instead builds
# the graph it assumes and states it:
#
#   calendar (day-of-week, trend) -> X, Y      exogenous by construction
#   Y[t-1] -> X[t], Y[t]                         past outcome: blocks feedback
#   X[t-1] -> X[t], Y[t]                         carry-over of the treatment
#   Z[t-1] -> X[t], Y[t]                         other candidates, lagged only
#   X[t]   -> Y[t]                               the effect being estimated
#
# Every adjustment variable is pre-treatment, so no mediator or collider can
# enter the backdoor set; contemporaneous other metrics are deliberately left
# out (they may sit on the X -> Y path). DoWhy identifies the backdoor set from
# that graph; the point estimate is DoWhy's; the interval is HAC (Newey-West)
# because daily residuals are autocorrelated and plain OLS intervals are too
# narrow. Refutation adds two time-series checks DoWhy does not ship: a
# circular-shift placebo (keeps autocorrelation + seasonality, breaks timing)
# and a future-treatment (lead) placebo — X[t+1] must not "explain" Y[t].
# ---------------------------------------------------------------------------


@dataclass
class TimeSeriesEffect:
    treatment: str
    outcome: str
    status: Literal["estimated", "insufficient"]
    reason: str = ""
    effect: float | None = None
    std_error: float | None = None
    p_value: float | None = None
    confidence_interval: list[float] = field(default_factory=list)
    n_rows: int = 0
    adjustment_set: list[str] = field(default_factory=list)
    graph_edges: list[tuple[str, str]] = field(default_factory=list)
    identified_estimand: str = ""
    estimator: str = "backdoor.linear_regression"
    uncertainty_method: str = "max(HC3, small-sample HAC) standard error, t interval"
    refutations: list[dict[str, Any]] = field(default_factory=list)
    temporal: dict[str, Any] = field(default_factory=dict)
    naive_association: dict[str, Any] = field(default_factory=dict)

    @property
    def refutations_passed(self) -> bool:
        return bool(self.refutations) and all(r.get("passed") for r in self.refutations)

    def as_dict(self) -> dict[str, Any]:
        out = {k: v for k, v in self.__dict__.items()}
        out["graph_edges"] = [list(e) for e in self.graph_edges]
        return out


def _lag_name(column: str) -> str:
    return f"{column}__lag1"


def _hac_lags(n: int) -> int:
    # Newey-West rule of thumb floor(4 (n/100)^(2/9)), at least 1.
    return max(1, math.floor(4 * (n / 100.0) ** (2.0 / 9.0)))


def _ols(y: Any, X: Any, *, hac: bool = True) -> Any:
    import statsmodels.api as sm

    model = sm.OLS(y, sm.add_constant(X, has_constant="add"))
    if hac:
        return model.fit(cov_type="HAC", cov_kwds={"maxlags": _hac_lags(len(y))})
    return model.fit()


def conservative_interval(y: Any, X: Any, column: str, alpha: float) -> tuple[float, float, float, float]:
    """(beta, se, lo, hi) for one coefficient with a deliberately conservative SE.

    Monte Carlo at this sample size (n~55, ~10 controls, 200 reps): plain
    Newey-West 95% intervals covered the true effect only 87-90% of the time;
    HC3 covered 95-97%, the small-sample-corrected HAC ~92%. Taking the larger
    of HC3 and corrected HAC keeps coverage at or above nominal, so noise is
    not promoted into an "effect".
    """
    import statsmodels.api as sm
    from scipy import stats

    model = sm.OLS(y, sm.add_constant(X, has_constant="add"))
    hc3 = model.fit(cov_type="HC3")
    hac = model.fit(cov_type="HAC", cov_kwds={"maxlags": _hac_lags(len(y)), "use_correction": True})
    beta = float(hc3.params[column])
    se = max(float(hc3.bse[column]), float(hac.bse[column]))
    t = float(stats.t.ppf(1 - alpha / 2, df=max(1, int(hc3.df_resid))))
    return beta, se, beta - t * se, beta + t * se


def build_time_series_frame(
    outcome: dict[Any, float],
    treatment: dict[Any, float],
    others: dict[str, dict[Any, float]],
    days: list[Any],
    *,
    treatment_name: str = "treatment",
    outcome_name: str = "outcome",
    calendar: bool = True,
) -> Any:
    """Daily frame with calendar terms and one-day lags on consecutive days.

    A lag is only taken from the literal previous calendar day: a missing day
    yields NaN rather than silently pairing a value with a stale one.
    """
    import pandas as pd

    rows: list[dict[str, Any]] = []
    t0 = min(days) if days else None
    for d in sorted(days):
        prev = d - timedelta(days=1)
        row: dict[str, Any] = {
            "__day": d,
            outcome_name: outcome.get(d, math.nan),
            treatment_name: treatment.get(d, math.nan),
            _lag_name(outcome_name): outcome.get(prev, math.nan),
            _lag_name(treatment_name): treatment.get(prev, math.nan),
            "__lead": treatment.get(d + timedelta(days=1), math.nan),
        }
        for name, series in others.items():
            row[_lag_name(name)] = series.get(prev, math.nan)
        if calendar:
            row["trend"] = float((d - t0).days) / 7.0
            for k in range(1, 7):
                row[f"dow_{k}"] = 1.0 if d.weekday() == k else 0.0
        rows.append(row)
    return pd.DataFrame(rows)


def _granger_p(frame: Any, cause: str, effect: str, calendar_cols: list[str]) -> float | None:
    """F-test p-value that cause[t-1] adds to effect[t] beyond effect[t-1] + calendar."""
    cols = [_lag_name(effect), _lag_name(cause), *calendar_cols]
    sub = frame[[effect, *cols]].dropna()
    if len(sub) < len(cols) + 6:
        return None
    try:
        fit = _ols(sub[effect], sub[cols], hac=False)
        return float(fit.pvalues[_lag_name(cause)])
    except Exception:
        return None


def _intervention_shifts(frame: Any, T: str, Y: str, calendar_cols: list[str], beta: float) -> dict[str, Any]:
    """Direction evidence from abrupt, intervention-like shifts in the treatment.

    A same-day effect leaves no lead/lag signature, so Granger tests cannot
    orient it. What can: days where X jumps far outside its normal day-to-day
    change (a budget switched off/on, a site outage) while Y had NOT already
    moved the day before, and Y then moves the way the estimated effect says.
    Two or more such shifts, none contradicting, is evidence that X moves Y
    rather than Y moving X. Calendar effects are removed first so a weekly
    pattern is never read as a shift.
    """
    import numpy as np

    cols = [c for c in calendar_cols if c in frame.columns]
    sub = frame[["__day", T, Y, *cols]].dropna().reset_index(drop=True)
    if len(sub) < 14:
        return {"shifts": 0, "consistent": False}
    resid: dict[str, Any] = {}
    for c in (T, Y):
        if cols:
            fit = _ols(sub[c], sub[cols], hac=False)
            resid[c] = (sub[c] - fit.fittedvalues).to_numpy()
        else:
            resid[c] = (sub[c] - sub[c].mean()).to_numpy()
    days = list(sub["__day"])
    consecutive = [i for i in range(2, len(days)) if (days[i] - days[i - 1]).days == 1 and (days[i - 1] - days[i - 2]).days == 1]
    if not consecutive:
        return {"shifts": 0, "consistent": False}
    dx = np.array([resid[T][i] - resid[T][i - 1] for i in consecutive])
    dy = np.array([resid[Y][i] - resid[Y][i - 1] for i in consecutive])
    pre = np.array([resid[Y][i - 1] - resid[Y][i - 2] for i in consecutive])

    def robust_sd(v: Any) -> float:
        med = float(np.median(v))
        mad = float(np.median(np.abs(v - med))) * 1.4826
        return mad if mad > 0 else float(np.std(v)) or 1e-12

    sx, sy = robust_sd(dx), robust_sd(dy)
    jumps = [k for k in range(len(consecutive)) if abs(dx[k]) >= 3 * sx]
    agree = disagree = anticipated = 0
    for k in jumps:
        if abs(pre[k]) >= 2 * sy and np.sign(pre[k]) == np.sign(beta * dx[k]):
            anticipated += 1  # Y was already moving that way: X may be reacting to Y
        elif np.sign(dy[k]) == np.sign(beta * dx[k]) and abs(dy[k]) >= 1.5 * sy:
            agree += 1
        elif abs(dy[k]) >= 1.5 * sy:
            disagree += 1
    return {
        "shifts": len(jumps),
        "followed_by_outcome": agree,
        "contradicted": disagree,
        "outcome_moved_first": anticipated,
        "consistent": agree >= 2 and disagree == 0 and anticipated == 0,
    }


def estimate_time_series_effect(
    *,
    outcome: dict[Any, float],
    treatment: dict[Any, float],
    others: dict[str, dict[Any, float]],
    days: list[Any],
    treatment_name: str,
    outcome_name: str,
    alpha: float = 0.05,
    refuter_tolerance: float = 0.25,
    min_rows_per_covariate: int = 3,
    min_rows: int = 8,
    placebo_shifts: int = 60,
    run_dowhy_refuters: bool = True,
) -> TimeSeriesEffect:
    """Estimate the same-day total effect of ``treatment`` on ``outcome``.

    ``others`` are the competing candidate drivers; they enter only as one-day
    lags (pre-treatment). Returns ``status="insufficient"`` with a reason
    instead of raising whenever the data cannot support an estimate.
    """
    import networkx as nx
    import numpy as np

    T, Y = treatment_name, outcome_name
    result = TimeSeriesEffect(treatment=T, outcome=Y, status="insufficient")
    if T == Y:
        result.reason = "treatment and outcome are the same series"
        return result

    span_days = (max(days) - min(days)).days + 1 if days else 0
    calendar = span_days >= 21  # three weeks before day-of-week terms are estimable
    frame = build_time_series_frame(
        outcome, treatment, others, days,
        treatment_name=T, outcome_name=Y, calendar=calendar,
    )
    calendar_cols = (["trend", *[f"dow_{k}" for k in range(1, 7)]] if calendar else [])
    base_adjust = [*calendar_cols, _lag_name(Y), _lag_name(T)]
    other_lags = [_lag_name(n) for n in others]

    def usable(adjust: list[str]) -> Any:
        sub = frame[[Y, T, *adjust]].dropna()
        needed = max(min_rows, min_rows_per_covariate * (len(adjust) + 2))
        return sub if len(sub) >= needed else None

    adjust = [*base_adjust, *other_lags]
    data = usable(adjust)
    # Shed the least essential covariates first: competing-candidate lags, then
    # the calendar, never the outcome/treatment lags that block feedback.
    while data is None and other_lags:
        other_lags = other_lags[:-1]
        adjust = [*base_adjust, *other_lags]
        data = usable(adjust)
    if data is None and calendar_cols:
        trimmed = [_lag_name(Y), _lag_name(T)]
        if (d2 := usable(trimmed)) is not None:
            adjust, data, calendar_cols = trimmed, d2, []
    if data is None:
        complete = len(frame[[Y, T]].dropna())
        result.reason = f"only {complete} complete daily rows; too few to adjust for calendar and lags"
        result.n_rows = complete
        return result

    x = data[T].astype(float)
    if float(x.std(ddof=0)) <= 1e-12 or x.nunique() < 3:
        result.reason = f"{T} barely varies over the estimation window; its effect is not identifiable"
        result.n_rows = len(data)
        return result

    # Naive association (what a correlation-only engine would report).
    try:
        naive = _ols(data[Y], data[[T]], hac=True)
        result.naive_association = {
            "slope": float(naive.params[T]),
            "p_value": float(naive.pvalues[T]),
            "correlation": float(np.corrcoef(data[T], data[Y])[0, 1]),
        }
    except Exception:
        result.naive_association = {}

    # Explicit DAG: every adjustment variable is a pre-treatment parent of both.
    edges: list[tuple[str, str]] = [(c, T) for c in adjust] + [(c, Y) for c in adjust] + [(T, Y)]
    graph = nx.DiGraph(edges)
    try:
        from dowhy import CausalModel

        model = CausalModel(data=data[[Y, T, *adjust]], treatment=T, outcome=Y, graph=graph)
        estimand = model.identify_effect()
        backdoor = list(estimand.get_backdoor_variables() or [])
        if not backdoor and adjust:
            result.reason = "DoWhy could not identify a backdoor adjustment set for this graph"
            return result
        estimate = model.estimate_effect(estimand, method_name="backdoor.linear_regression")
        dowhy_effect = float(estimate.value)
    except Exception as exc:
        _log.warning("time-series dowhy estimation failed for %s -> %s", T, Y, exc_info=True)
        result.reason = f"DoWhy estimation failed: {type(exc).__name__}: {exc}"
        return result

    beta, se, lo, hi = conservative_interval(data[Y], data[[T, *backdoor]], T, alpha)
    if not math.isclose(beta, dowhy_effect, rel_tol=1e-6, abs_tol=1e-9):
        _log.warning("dowhy effect %s != OLS effect %s for %s -> %s", dowhy_effect, beta, T, Y)

    result.status = "estimated"
    result.effect = dowhy_effect
    result.std_error = se
    from scipy import stats

    result.p_value = float(2 * stats.t.sf(abs(beta / se), df=max(1, len(data) - len(backdoor) - 2))) if se > 0 else 0.0
    result.confidence_interval = [lo, hi]
    result.n_rows = len(data)
    result.adjustment_set = backdoor
    result.graph_edges = edges
    result.identified_estimand = "backdoor: E[Y | do(X)] via adjustment for " + (", ".join(backdoor) or "nothing")

    # ---- temporal precedence (Granger-style, calendar-controlled) ---------
    p_xy = _granger_p(frame, T, Y, calendar_cols)
    p_yx = _granger_p(frame, Y, T, calendar_cols)
    shifts_evidence = _intervention_shifts(frame, T, Y, calendar_cols, beta)
    if p_yx is not None and p_yx < alpha:
        direction = "feedback"  # past outcome predicts the treatment
    elif p_xy is not None and p_xy < alpha:
        direction = "temporal"  # past treatment predicts outcome, not vice versa
    elif shifts_evidence.get("consistent"):
        direction = "intervention"  # repeated abrupt shifts in X, Y follows without moving first
    else:
        direction = "assumed"   # same-day association only; direction is an assumption
    result.temporal = {
        "p_treatment_leads_outcome": p_xy,
        "p_outcome_leads_treatment": p_yx,
        "treatment_shifts": shifts_evidence,
        "direction": direction,
    }

    # ---- refutation ---------------------------------------------------------
    refs: list[dict[str, Any]] = []
    denom = abs(beta) if abs(beta) > 1e-12 else 1.0

    if run_dowhy_refuters:
        for name, kwargs in (
            ("random_common_cause", {"num_simulations": 20}),
            ("data_subset_refuter", {"subset_fraction": 0.8, "num_simulations": 20}),
        ):
            try:
                ref = model.refute_estimate(estimand, estimate, method_name=name, random_seed=7, **kwargs)
                new = float(ref.new_effect)
                drift = abs(new - dowhy_effect) / denom
                refs.append({
                    "name": name,
                    "new_effect": new,
                    "relative_drift": drift,
                    "passed": drift <= refuter_tolerance and (new * dowhy_effect > 0 or abs(dowhy_effect) < 1e-12),
                })
            except Exception as exc:
                refs.append({"name": name, "passed": False, "error": str(exc)})

    # Circular-shift placebo: same series, broken timing.
    n = len(frame)
    shifts = [s for s in range(7, n - 6)]
    if len(shifts) > placebo_shifts:
        step = len(shifts) / placebo_shifts
        shifts = [shifts[int(i * step)] for i in range(placebo_shifts)]
    placebo_betas: list[float] = []
    if shifts:
        xs = frame[T].to_numpy()
        for s in shifts:
            shifted = frame.copy()
            rolled = np.roll(xs, s)
            shifted[T] = rolled
            shifted[_lag_name(T)] = np.roll(rolled, 1)
            sub = shifted[[Y, T, *backdoor]].dropna()
            if len(sub) < len(backdoor) + 4 or sub[T].std(ddof=0) <= 1e-12:
                continue
            try:
                placebo_betas.append(float(_ols(sub[Y], sub[[T, *backdoor]], hac=False).params[T]))
            except Exception:  # noqa: S112 - a singular shifted design is just one fewer placebo draw
                continue
    if placebo_betas:
        exceed = sum(1 for b in placebo_betas if abs(b) >= abs(beta))
        p_placebo = (1 + exceed) / (1 + len(placebo_betas))
        refs.append({
            "name": "time_shift_placebo",
            "p_value": p_placebo,
            "placebo_mean": float(np.mean(placebo_betas)),
            "draws": len(placebo_betas),
            "passed": p_placebo < alpha,  # effect must stand out from timing-scrambled copies
        })
    else:
        refs.append({"name": "time_shift_placebo", "passed": False, "error": "series too short for a shift placebo"})

    # Future-treatment placebo: X[t+1] must not explain Y[t] given X[t].
    lead = frame[[Y, T, "__lead", *backdoor]].dropna()
    if len(lead) >= len(backdoor) + 6 and lead["__lead"].std(ddof=0) > 1e-12:
        try:
            from scipy import stats

            coef_lead, se_lead, _, _ = conservative_interval(lead[Y], lead[[T, "__lead", *backdoor]], "__lead", alpha)
            df_lead = max(1, len(lead) - len(backdoor) - 3)
            p_lead = float(2 * stats.t.sf(abs(coef_lead / se_lead), df=df_lead)) if se_lead > 0 else 0.0
            refs.append({
                "name": "future_treatment_placebo",
                "lead_effect": coef_lead,
                "p_value": p_lead,
                "passed": p_lead >= alpha or abs(coef_lead) < 0.5 * denom,
            })
        except Exception as exc:
            refs.append({"name": "future_treatment_placebo", "passed": False, "error": str(exc)})
    else:
        refs.append({"name": "future_treatment_placebo", "passed": False, "error": "not enough rows with a next-day treatment"})

    # Influence: the estimate must not hinge on a few extreme days.
    try:
        plain = _ols(data[Y], data[[T, *backdoor]], hac=False)
        cooks = plain.get_influence().cooks_distance[0]
        keep = cooks <= 4.0 / len(data)
        dropped = int((~keep).sum())
        if dropped and keep.sum() >= len(backdoor) + 6:
            kept = data[keep]
            nb = float(_ols(kept[Y], kept[[T, *backdoor]], hac=False).params[T])
            drift = abs(nb - beta) / denom
            refs.append({
                "name": "influential_days_removed",
                "dropped_days": dropped,
                "new_effect": nb,
                "relative_drift": drift,
                "passed": nb * beta > 0 and drift <= 2 * refuter_tolerance,
            })
        else:
            refs.append({"name": "influential_days_removed", "dropped_days": dropped, "passed": True})
    except Exception as exc:
        refs.append({"name": "influential_days_removed", "passed": False, "error": str(exc)})

    result.refutations = refs
    return result
